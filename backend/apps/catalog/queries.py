"""Querysets behind the customer API: artwork, people and titles loaded in a fixed
number of queries whatever the list size (tests pin the counts)."""

from collections.abc import Iterable, Sequence
from typing import Any, Final
from uuid import UUID

from django.db.models import Count, Prefetch, QuerySet

from apps.catalog.browse import CustomerScope, playable_episodes, visible_movies, visible_series
from apps.catalog.models import Credit, Episode, MediaImage, Movie, Season, Series

CARD_ART: Final = ("poster", "backdrop", "logo")

type TitleRef = tuple[str, UUID]  # ("movie" | "series", id)
type Title = Movie | Series


def art(kinds: Sequence[str] = CARD_ART, *, through: str = "") -> "Prefetch[Any]":
    """Artwork of the given kinds into `.art`, primary first within each kind
    (`through="person__"` for the people of credits)."""
    return Prefetch(
        f"{through}images",
        queryset=MediaImage.objects.filter(kind__in=kinds)
        .only(
            "id", "kind", "sizes", "width", "height", "blurhash", "is_primary", "created_at",
            "movie_id", "series_id", "season_id", "episode_id", "person_id",
        )
        .order_by("kind", "-is_primary", "created_at"),
        to_attr="art",
    )  # fmt: skip


def cards[T: (Movie, Series)](query: QuerySet[T]) -> QuerySet[T]:
    return query.prefetch_related(art())


def credits() -> "Prefetch[Any]":
    return Prefetch(
        "credits",
        queryset=Credit.objects.select_related("person")
        .prefetch_related(art(("profile",), through="person__"))
        .order_by("role", "order"),
    )


def details[T: (Movie, Series)](query: QuerySet[T]) -> QuerySet[T]:
    return query.prefetch_related(art(), "genres", "categories", credits())


def hydrate(scope: CustomerScope, refs: Iterable[TitleRef]) -> list[Title]:
    """The visible titles among `refs`, as cards, in the order given (two queries)."""
    ordered = list(dict.fromkeys(refs))
    movie_ids = [pk for kind, pk in ordered if kind == "movie"]
    series_ids = [pk for kind, pk in ordered if kind == "series"]
    found: dict[TitleRef, Title] = {}
    if movie_ids:
        for movie in cards(visible_movies(scope).filter(pk__in=movie_ids)):
            found[("movie", movie.pk)] = movie
    if series_ids:
        for series in cards(visible_series(scope).filter(pk__in=series_ids)):
            found[("series", series.pk)] = series
    return [found[ref] for ref in ordered if ref in found]


def ref_of(title: Title) -> TitleRef:
    return ("movie" if isinstance(title, Movie) else "series", title.pk)


def seasons_with_episodes(series: Series) -> list[Season]:
    """The series' seasons that have a playable episode, with `playable_count` and art."""
    counts = dict(
        playable_episodes()
        .filter(season__series=series)
        .order_by()
        .values("season_id")
        .annotate(count=Count("id"))
        .values_list("season_id", "count")
    )
    seasons = list(
        Season.objects.filter(pk__in=list(counts))
        .prefetch_related(art(("poster",)))
        .order_by("number")
    )
    for season in seasons:
        season.playable_count = counts[season.pk]  # type: ignore[attr-defined]
    return seasons


def episodes_of(season: Season) -> list[Episode]:
    return list(
        playable_episodes()
        .filter(season=season)
        .select_related("season")
        .prefetch_related(art(("still",)))
        .order_by("number")
    )
