"""What a customer may browse (SPEC §7.4, §9, §10): the catalogue filtered by entitlement.

The customer API, home rows, search and recommendations all start from these
querysets, so every surface hides the same titles:

- **Ready only.** Movies with status `ready`; series with status `ready` and at least
  one playable episode; episodes with a playable file (`services.playable_files`).
  Titles whose licence has ended are hidden even before their status is refreshed.
- **Content kinds.** `allow_movies` / `allow_series` of the entitlement.
- **Categories.** When the entitlement lists categories, a title must be in one of
  them, like playback's check 6. When it allows every category (`categories` null),
  titles without a category show too.
- **Adult content is opt-in.** Adult categories show only when the access profile
  names them explicitly; "every category" means every non-adult one. A title in any
  adult category the customer was not given is hidden, wherever else it is filed.

Browsing does not depend on the access period: an expired customer still sees the
catalogue (with a "renew" prompt in the portal); playback refuses with
SUBSCRIPTION_EXPIRED.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.db.models import Exists, OuterRef, Q, QuerySet
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog.models import Category, Episode, MediaFile, Movie, Series, TitleStatus
from apps.catalog.services import playable_files
from apps.playback import entitlements

MovieLinks = Movie.categories.through
SeriesLinks = Series.categories.through


@dataclass(frozen=True, slots=True)
class CustomerScope:
    """The browsing rights an entitlement gives; equal scopes share cached rows."""

    categories: frozenset[UUID] | None = None  # granted categories; None = every non-adult
    allow_movies: bool = True
    allow_series: bool = True
    nothing: bool = False  # no entitlement at all (no access profile)
    #: The plan's quality ceiling, for quality badges; not a browsing restriction.
    max_quality: int = 1080

    @classmethod
    def none(cls) -> "CustomerScope":
        return cls(categories=frozenset(), allow_movies=False, allow_series=False, nothing=True)

    def fingerprint(self) -> str:
        if self.nothing:
            return "none"
        granted = "*" if self.categories is None else ",".join(sorted(map(str, self.categories)))
        flags = f"{int(self.allow_movies)}{int(self.allow_series)}"
        return hashlib.sha256(f"{granted}|{flags}".encode()).hexdigest()[:16]


def scope_for(user: User) -> CustomerScope:
    """The scope of the customer's cached entitlement (redis-state, rebuilt when missing)."""
    entitlement = entitlements.get(user.pk)
    if entitlement is None:
        return CustomerScope.none()
    granted = entitlement["categories"]
    return CustomerScope(
        categories=None if granted is None else frozenset(UUID(value) for value in granted),
        allow_movies=bool(entitlement["allow_movies"]),
        allow_series=bool(entitlement["allow_series"]),
        max_quality=int(entitlement["max_quality"]),
    )


# --- Categories ----------------------------------------------------------------------------


def visible_categories(scope: CustomerScope, kind: str | None = None) -> QuerySet[Category]:
    """Categories the scope may browse (empty ones included)."""
    query = Category.objects.all()
    if kind is not None:
        query = query.filter(kind=kind)
    if scope.nothing:
        return query.none()
    if scope.categories is None:
        return query.filter(is_adult=False)
    return query.filter(pk__in=scope.categories)


def _hidden_adult(scope: CustomerScope) -> QuerySet[Category]:
    hidden = Category.objects.filter(is_adult=True)
    if scope.categories is not None:
        hidden = hidden.exclude(pk__in=scope.categories)
    return hidden


def _licensed(now: datetime) -> Q:
    return Q(license_expires_at__isnull=True) | Q(license_expires_at__gt=now)


# --- Titles --------------------------------------------------------------------------------


def playable_episodes() -> QuerySet[Episode]:
    """Episodes with an active file that plays now."""
    return Episode.objects.filter(
        Exists(MediaFile.objects.filter(playable_files(), episodes=OuterRef("pk")))
    )


def visible_movies(scope: CustomerScope, *, now: datetime | None = None) -> QuerySet[Movie]:
    if scope.nothing or not scope.allow_movies:
        return Movie.objects.none()
    query = Movie.objects.filter(_licensed(now or timezone.now()), status=TitleStatus.READY)
    query = query.exclude(
        Exists(MovieLinks.objects.filter(movie=OuterRef("pk"), category__in=_hidden_adult(scope)))
    )
    if scope.categories is not None:
        query = query.filter(
            Exists(MovieLinks.objects.filter(movie=OuterRef("pk"), category__in=scope.categories))
        )
    return query


def visible_series(scope: CustomerScope, *, now: datetime | None = None) -> QuerySet[Series]:
    if scope.nothing or not scope.allow_series:
        return Series.objects.none()
    query = Series.objects.filter(
        _licensed(now or timezone.now()), status=TitleStatus.READY
    ).filter(Exists(playable_episodes().filter(season__series=OuterRef("pk"))))
    query = query.exclude(
        Exists(SeriesLinks.objects.filter(series=OuterRef("pk"), category__in=_hidden_adult(scope)))
    )
    if scope.categories is not None:
        query = query.filter(
            Exists(SeriesLinks.objects.filter(series=OuterRef("pk"), category__in=scope.categories))
        )
    return query


def visible_episodes(scope: CustomerScope, *, now: datetime | None = None) -> QuerySet[Episode]:
    """Playable episodes of the series the scope may browse."""
    return playable_episodes().filter(season__series__in=visible_series(scope, now=now))


def visible_title(scope: CustomerScope, kind: str, title_id: UUID) -> Movie | Series | None:
    """The movie or series with this id when the scope may browse it."""
    if kind == "movie":
        return visible_movies(scope).filter(pk=title_id).first()
    if kind == "series":
        return visible_series(scope).filter(pk=title_id).first()
    return None
