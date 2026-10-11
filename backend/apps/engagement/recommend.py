"""Recommendations (SPEC §7.9): similar titles, per-user rows and what is popular.

**Similar titles** are recomputed nightly (`compute_similar_titles`) as a weighted
overlap between titles of the same kind:

    0.35 genres + 0.25 top-5 cast + 0.15 directors + 0.15 keywords
    + 0.05 same decade + 0.05 same original language

Overlaps are Jaccard indexes. TMDB keywords are not stored yet, so that term is 0
for now and scores top out at 0.85; the ranking is unaffected. Only pairs that share
a genre, a cast member or a director are scored (an inverted index), and each title
keeps its top `SIMILAR_PER_TITLE`.

**Per-user rows**, explainable by construction:

- *Because you watched X*: the last five finished titles, each with up to twelve of
  its similar titles the customer has not watched;
- *Top picks*: those similar lists merged (by score), boosted by the genres the
  customer finishes most, minus anything watched or thumbed down; popular titles fill
  in for customers without history;
- *Popular this week*: plays of the last seven days, each weighted by
  `exp(-age_days / POPULAR_DECAY_DAYS)`, episodes counting for their series.

`Recommender` is the seam for a learned model later: the API only calls its methods.
"""

import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Protocol
from uuid import UUID

from django.db import transaction
from django.db.models import Count
from django.db.models.functions import TruncDate
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog.browse import CustomerScope, visible_movies, visible_series
from apps.catalog.models import Credit, CreditRole, Episode, Movie, Series, TitleStatus
from apps.catalog.queries import Title, TitleRef, hydrate, ref_of
from apps.engagement.models import SimilarTitle, WatchProgress
from apps.engagement.services import disliked_refs, watched_refs
from apps.playback.models import PlaybackSession, TitleKind

WEIGHTS: Final = {
    "genres": 0.35,
    "cast": 0.25,
    "directors": 0.15,
    "keywords": 0.15,
    "decade": 0.05,
    "language": 0.05,
}
SIMILAR_PER_TITLE: Final = 30
TOP_CAST: Final = 5
BECAUSE_SOURCES: Final = 5
BECAUSE_ITEMS: Final = 12
ROW_SIZE: Final = 20
POPULAR_DAYS: Final = 7
POPULAR_DECAY_DAYS: Final = 3.0
GENRE_BOOST: Final = 0.5


# --- Similar titles (nightly) -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Features:
    genres: frozenset[UUID]
    cast: frozenset[UUID]
    directors: frozenset[UUID]
    decade: int | None
    language: str


def _jaccard(a: frozenset[UUID], b: frozenset[UUID]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def similarity(a: Features, b: Features) -> float:
    score = (
        WEIGHTS["genres"] * _jaccard(a.genres, b.genres)
        + WEIGHTS["cast"] * _jaccard(a.cast, b.cast)
        + WEIGHTS["directors"] * _jaccard(a.directors, b.directors)
    )
    if a.decade is not None and a.decade == b.decade:
        score += WEIGHTS["decade"]
    if a.language and a.language == b.language:
        score += WEIGHTS["language"]
    return round(score, 4)


def _features(kind: str) -> dict[UUID, Features]:
    model = Movie if kind == "movie" else Series
    titles = model.objects.filter(status=TitleStatus.READY).only("id", "year", "original_language")
    genres: dict[UUID, set[UUID]] = defaultdict(set)
    links = model.genres.through.objects.filter(**{f"{kind}__status": TitleStatus.READY})
    for title_id, genre_id in links.values_list(f"{kind}_id", "genre_id"):
        genres[title_id].add(genre_id)
    cast: dict[UUID, list[UUID]] = defaultdict(list)
    directors: dict[UUID, set[UUID]] = defaultdict(set)
    credits = (
        Credit.objects.filter(**{f"{kind}__status": TitleStatus.READY})
        .filter(role__in=(CreditRole.CAST, CreditRole.DIRECTOR))
        .order_by("order")
        .values_list(f"{kind}_id", "person_id", "role")
    )
    for title_id, person_id, role in credits:
        if role == CreditRole.DIRECTOR:
            directors[title_id].add(person_id)
        elif len(cast[title_id]) < TOP_CAST:
            cast[title_id].append(person_id)
    return {
        title.pk: Features(
            genres=frozenset(genres.get(title.pk, ())),
            cast=frozenset(cast.get(title.pk, ())),
            directors=frozenset(directors.get(title.pk, ())),
            decade=(title.year // 10) if title.year else None,
            language=title.original_language or "",
        )
        for title in titles
    }


def similar_pairs(
    features: Mapping[UUID, Features], limit: int = SIMILAR_PER_TITLE
) -> dict[UUID, list[tuple[UUID, float]]]:
    """Top `limit` similar titles per title, scoring only titles that share a genre, a
    cast member or a director."""
    index: dict[tuple[str, UUID], list[UUID]] = defaultdict(list)
    for title_id, feature in features.items():
        for genre in feature.genres:
            index[("g", genre)].append(title_id)
        for person in feature.cast:
            index[("c", person)].append(title_id)
        for person in feature.directors:
            index[("d", person)].append(title_id)
    result: dict[UUID, list[tuple[UUID, float]]] = {}
    for title_id, feature in features.items():
        candidates: set[UUID] = set()
        for key in (
            *(("g", g) for g in feature.genres),
            *(("c", c) for c in feature.cast),
            *(("d", d) for d in feature.directors),
        ):
            candidates.update(index[key])
        candidates.discard(title_id)
        scored = [(other, similarity(feature, features[other])) for other in candidates]
        scored = [item for item in scored if item[1] > 0]
        scored.sort(key=lambda item: (-item[1], str(item[0])))
        result[title_id] = scored[:limit]
    return result


def compute_similar_titles() -> dict[str, int]:
    """Rebuild `SimilarTitle` for movies and series; returns rows written per kind."""
    written: dict[str, int] = {}
    for kind in ("movie", "series"):
        pairs = similar_pairs(_features(kind))
        rows = [
            SimilarTitle(
                **(
                    {"movie_id": source, "similar_movie_id": target}
                    if kind == "movie"
                    else {"series_id": source, "similar_series_id": target}
                ),
                score=score,
            )
            for source, items in pairs.items()
            for target, score in items
        ]
        with transaction.atomic():
            if kind == "movie":
                SimilarTitle.objects.filter(movie__isnull=False).delete()
            else:
                SimilarTitle.objects.filter(series__isnull=False).delete()
            SimilarTitle.objects.bulk_create(rows, batch_size=1000)
        written[kind] = len(rows)
    return written


def similar_refs(
    title_refs: Sequence[TitleRef], limit: int = SIMILAR_PER_TITLE
) -> dict[TitleRef, list[tuple[TitleRef, float]]]:
    """Stored similar titles of each ref (one query)."""
    movie_ids = [pk for kind, pk in title_refs if kind == "movie"]
    series_ids = [pk for kind, pk in title_refs if kind == "series"]
    found: dict[TitleRef, list[tuple[TitleRef, float]]] = defaultdict(list)
    rows = SimilarTitle.objects.filter(movie_id__in=movie_ids) | SimilarTitle.objects.filter(
        series_id__in=series_ids
    )
    for row in rows.order_by("-score").values_list(
        "movie_id", "similar_movie_id", "series_id", "similar_series_id", "score"
    ):
        movie_id, similar_movie_id, series_id, similar_series_id, score = row
        if movie_id:
            source: TitleRef = ("movie", movie_id)
            target: TitleRef = ("movie", similar_movie_id)
        else:
            source, target = ("series", series_id), ("series", similar_series_id)
        if len(found[source]) < limit:
            found[source].append((target, float(score)))
    return found


def _genre_fallback(scope: CustomerScope, title: Title, limit: int) -> list[Title]:
    """Before the first nightly run: same-genre titles, most popular first."""
    genre_ids = list(title.genres.values_list("pk", flat=True))
    query = visible_movies(scope) if isinstance(title, Movie) else visible_series(scope)
    query = query.exclude(pk=title.pk)
    if genre_ids:
        query = query.filter(genres__in=genre_ids)
    pks = list(
        query.order_by("-popularity", "title").values_list("pk", flat=True).distinct()[: limit * 2]
    )
    kind = "movie" if isinstance(title, Movie) else "series"
    return hydrate(scope, [(kind, pk) for pk in dict.fromkeys(pks)])[:limit]


def more_like_this(scope: CustomerScope, title: Title, limit: int = 12) -> list[Title]:
    ref = ref_of(title)
    stored = similar_refs([ref], limit=limit * 2).get(ref, [])
    if not stored:
        return _genre_fallback(scope, title, limit)
    return hydrate(scope, [target for target, _score in stored])[:limit]


# --- Popular this week ------------------------------------------------------------------------


def popular_refs(*, days: int = POPULAR_DAYS, now: datetime | None = None) -> list[TitleRef]:
    """Titles by decayed plays over the last `days` (episodes count for their series)."""
    moment = now or timezone.now()
    rows = (
        PlaybackSession.objects.filter(
            started_at__gte=moment - timedelta(days=days),
            title_kind__in=(TitleKind.MOVIE, TitleKind.EPISODE),  # not live TV (M12)
        )
        .annotate(day=TruncDate("started_at"))
        .values("title_kind", "title_id", "day")
        .annotate(plays=Count("id"))
        .order_by()
    )
    weights: defaultdict[TitleRef, float] = defaultdict(float)
    episode_rows: list[tuple[UUID, float]] = []
    today = moment.date()
    for row in rows:
        weight = row["plays"] * math.exp(-((today - row["day"]).days) / POPULAR_DECAY_DAYS)
        if row["title_kind"] == TitleKind.MOVIE:
            weights[("movie", row["title_id"])] += weight
        else:
            episode_rows.append((row["title_id"], weight))
    if episode_rows:
        series_of = dict(
            Episode.objects.filter(pk__in={pk for pk, _ in episode_rows}).values_list(
                "pk", "season__series_id"
            )
        )
        for episode_id, weight in episode_rows:
            series_id = series_of.get(episode_id)
            if series_id is not None:
                weights[("series", series_id)] += weight
    return [ref for ref, _ in sorted(weights.items(), key=lambda item: (-item[1], str(item[0][1])))]


def popular(scope: CustomerScope, limit: int = ROW_SIZE) -> list[Title]:
    return hydrate(scope, popular_refs()[: limit * 3])[:limit]


# --- Per-user rows ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BecauseRow:
    source: Title
    items: list[Title]


def _finished_refs(user: User, limit: int) -> list[TitleRef]:
    rows = (
        WatchProgress.objects.filter(user=user, completed=True)
        .order_by("-updated_at")
        .values_list("movie_id", "series_id")[: limit * 10]
    )
    refs: list[TitleRef] = []
    for movie_id, series_id in rows:
        ref: TitleRef = ("movie", movie_id) if movie_id else ("series", series_id)
        if ref not in refs:
            refs.append(ref)
        if len(refs) >= limit:
            break
    return refs


def _genre_affinity(user: User) -> Counter[UUID]:
    affinity: Counter[UUID] = Counter()
    finished = WatchProgress.objects.filter(user=user, completed=True)
    movie_ids = finished.filter(movie__isnull=False).values("movie_id")
    series_ids = finished.filter(series__isnull=False).values("series_id")
    for genre_id in Movie.genres.through.objects.filter(movie_id__in=movie_ids).values_list(
        "genre_id", flat=True
    ):
        affinity[genre_id] += 1
    for genre_id in Series.genres.through.objects.filter(series_id__in=series_ids).values_list(
        "genre_id", flat=True
    ):
        affinity[genre_id] += 1
    return affinity


class Recommender(Protocol):
    def because_you_watched(self, user: User, scope: CustomerScope) -> list[BecauseRow]: ...

    def top_picks(self, user: User, scope: CustomerScope) -> list[Title]: ...

    def popular(self, scope: CustomerScope) -> list[Title]: ...


class SimilarityRecommender:
    """SPEC §7.9's explainable recommender over `SimilarTitle` and watch progress."""

    def because_you_watched(self, user: User, scope: CustomerScope) -> list[BecauseRow]:
        sources = _finished_refs(user, BECAUSE_SOURCES)
        if not sources:
            return []
        exclude = watched_refs(user) | disliked_refs(user)
        similar = similar_refs(sources)
        source_titles = {ref_of(title): title for title in hydrate(scope, sources)}
        rows = []
        for ref in sources:
            source = source_titles.get(ref)
            if source is None:
                continue
            targets = [target for target, _ in similar.get(ref, []) if target not in exclude]
            items = hydrate(scope, targets)[:BECAUSE_ITEMS]
            if items:
                rows.append(BecauseRow(source=source, items=items))
        return rows

    def top_picks(self, user: User, scope: CustomerScope) -> list[Title]:
        sources = _finished_refs(user, BECAUSE_SOURCES * 2)
        exclude = watched_refs(user) | disliked_refs(user)
        scores: defaultdict[TitleRef, float] = defaultdict(float)
        for items in similar_refs(sources).values():
            for target, score in items:
                if target not in exclude:
                    scores[target] += score
        if scores:
            affinity = _genre_affinity(user)
            if affinity:
                _boost(scores, affinity)
        ranked = [ref for ref, _ in sorted(scores.items(), key=lambda i: (-i[1], str(i[0][1])))]
        picks = hydrate(scope, ranked)[:ROW_SIZE]
        if len(picks) < ROW_SIZE:
            seen = {ref_of(title) for title in picks} | exclude
            fill = [ref for ref in popular_refs() if ref not in seen]
            picks += hydrate(scope, fill)[: ROW_SIZE - len(picks)]
        if len(picks) < ROW_SIZE:
            seen = {ref_of(title) for title in picks} | exclude
            picks += [
                title for title in _top_rated(scope, ROW_SIZE * 2) if ref_of(title) not in seen
            ][: ROW_SIZE - len(picks)]
        return picks

    def popular(self, scope: CustomerScope) -> list[Title]:
        return popular(scope)


def _boost(scores: dict[TitleRef, float], affinity: Counter[UUID]) -> None:
    """Multiply each candidate's score by 1 + GENRE_BOOST x its share of top genres."""
    total = sum(affinity.values())
    movie_ids = [pk for kind, pk in scores if kind == "movie"]
    series_ids = [pk for kind, pk in scores if kind == "series"]
    genres: dict[TitleRef, list[UUID]] = defaultdict(list)
    for movie_id, genre_id in Movie.genres.through.objects.filter(
        movie_id__in=movie_ids
    ).values_list("movie_id", "genre_id"):
        genres[("movie", movie_id)].append(genre_id)
    for series_id, genre_id in Series.genres.through.objects.filter(
        series_id__in=series_ids
    ).values_list("series_id", "genre_id"):
        genres[("series", series_id)].append(genre_id)
    for ref in list(scores):
        share = sum(affinity.get(genre, 0) for genre in genres.get(ref, ())) / total
        scores[ref] *= 1 + GENRE_BOOST * min(share, 1.0)


def _top_rated(scope: CustomerScope, limit: int) -> Iterable[Title]:
    refs: list[TitleRef] = [
        ("movie", pk)
        for pk in visible_movies(scope)
        .order_by("-rating", "-popularity")
        .values_list("pk", flat=True)[:limit]
    ]
    refs += [
        ("series", pk)
        for pk in visible_series(scope)
        .order_by("-rating", "-popularity")
        .values_list("pk", flat=True)[:limit]
    ]
    return hydrate(scope, refs)


def recommender() -> Recommender:
    return SimilarityRecommender()
