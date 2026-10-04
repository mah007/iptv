"""Customer search (SPEC §7.8): Meilisearch first, PostgreSQL when it is unhealthy.

Every query is normalised like the index (`normalize`) and carries the customer's
server-side filter: `status = "ready"`, their content kinds, and their categories
(`category_ids IN [...]`, with adult titles only when an adult category was granted).
Hits are ids only; they are loaded through `apps.catalog.browse`, which applies the
exact visibility rules again, so the index can never show a title the customer may
not browse.

The fallback runs on the trigram-indexed `search_text` (normalised titles) of movies
and series: `websearch_to_tsquery` matches plus `word_similarity` for typos, ranked by
the better of the two. After a Meilisearch failure the fallback is used for
`BREAKER_S` seconds without retrying, so an outage costs one timeout, not one per query.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Final, Literal
from uuid import UUID

from django.contrib.postgres.search import (
    SearchQuery,
    SearchRank,
    SearchVector,
    TrigramWordSimilarity,
)
from django.db.models import F, Q, QuerySet
from django.db.models.functions import Greatest

from apps.catalog.browse import (
    CustomerScope,
    visible_episodes,
    visible_movies,
    visible_series,
)
from apps.catalog.models import Episode, Movie, Person, Series
from apps.catalog.queries import Title, TitleRef, art, hydrate
from apps.search import index
from apps.search.meili import MeiliError, MeiliIndexMissing, client
from apps.search.normalize import normalize

logger = logging.getLogger(__name__)

type ResultType = Literal["movie", "series", "episode"]
TYPES: Final = ("movie", "series", "episode")
MAX_QUERY: Final = 200
PAGE_SIZE: Final = 20
PEOPLE_LIMIT: Final = 10
TRIGRAM_THRESHOLD: Final = 0.3
BREAKER_S: Final = 30.0

_meili_down_until = 0.0


@dataclass(frozen=True, slots=True)
class Filters:
    type: ResultType | None = None
    genre: UUID | None = None
    year: int | None = None


@dataclass(slots=True)
class Hit:
    type: ResultType
    title: Title | None = None  # the movie or series (an episode's series)
    episode: Episode | None = None


@dataclass(slots=True)
class SearchResult:
    query: str
    engine: Literal["meilisearch", "database"]
    page: int
    page_size: int
    total: int
    hits: list[Hit] = field(default_factory=list)
    people: list[Person] = field(default_factory=list)


def _quote(value: object) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def scope_filter(scope: CustomerScope, filters: Filters) -> list[Any]:
    """Meilisearch filter clauses (AND of the list items) for this customer and query."""
    clauses: list[Any] = ['status = "ready"']
    kinds = []
    if scope.allow_movies:
        kinds.append("movie")
    if scope.allow_series:
        kinds += ["series", "episode"]
    if filters.type is not None:
        kinds = [kind for kind in kinds if kind == filters.type]
    clauses.append(
        f"type IN [{', '.join(_quote(kind) for kind in kinds)}]" if kinds else "type IN []"
    )
    if scope.categories is None:
        clauses.append("is_adult = false")
    else:
        granted = ", ".join(_quote(pk) for pk in sorted(map(str, scope.categories)))
        clauses.append(f"category_ids IN [{granted}]")
    if filters.genre is not None:
        clauses.append(f"genre_ids = {_quote(filters.genre)}")
    if filters.year is not None:
        clauses.append(f"year = {int(filters.year)}")
    return clauses


def _load(scope: CustomerScope, refs: list[tuple[ResultType, UUID]]) -> list[Hit]:
    """Visible titles and episodes for the hits, in hit order."""
    titles = {
        ("movie" if isinstance(title, Movie) else "series", title.pk): title
        for title in hydrate(
            scope, [(kind, pk) for kind, pk in refs if kind in {"movie", "series"}]
        )
    }
    episode_ids = [pk for kind, pk in refs if kind == "episode"]
    episodes: dict[UUID, Episode] = {}
    series_of: dict[UUID, Title] = {}
    if episode_ids:
        for episode in (
            visible_episodes(scope)
            .filter(pk__in=episode_ids)
            .select_related("season")
            .prefetch_related(art(("still",)))
        ):
            episodes[episode.pk] = episode
        series_ids = {episode.season.series_id for episode in episodes.values()}
        for series in hydrate(scope, [("series", pk) for pk in series_ids]):
            series_of[series.pk] = series
    hits: list[Hit] = []
    for kind, pk in refs:
        if kind == "episode":
            found = episodes.get(pk)
            if found is not None and found.season.series_id in series_of:
                hits.append(Hit("episode", series_of[found.season.series_id], found))
        elif (kind, pk) in titles:
            hits.append(Hit(kind, titles[(kind, pk)]))
    return hits


def _meili(
    scope: CustomerScope, folded: str, filters: Filters, page: int, page_size: int
) -> tuple[list[Hit], int]:
    body = {
        "q": folded,
        "filter": scope_filter(scope, filters),
        "page": page,
        "hitsPerPage": page_size,
        "attributesToRetrieve": ["type", "pk"],
    }
    result = client().search(index.INDEX, body)
    refs: list[tuple[ResultType, UUID]] = []
    for hit in result.get("hits", []):
        kind = hit.get("type")
        if kind in TYPES:
            refs.append((kind, UUID(str(hit["pk"]))))
    return _load(scope, refs), int(result.get("totalHits") or 0)


def _ranked[T: (Movie, Series)](query: QuerySet[T], folded: str) -> QuerySet[T]:
    vector = SearchVector("search_text", config="simple")
    terms = SearchQuery(folded, config="simple", search_type="websearch")
    return (
        query.annotate(
            similarity=TrigramWordSimilarity(folded, "search_text"),
            rank=SearchRank(vector, terms),
        )
        .filter(
            Q(similarity__gte=TRIGRAM_THRESHOLD) | Q(search_text__icontains=folded) | Q(rank__gt=0)
        )
        .annotate(score=Greatest(F("similarity"), F("rank")))
        .order_by("-score", "-popularity", "pk")
    )


def _database(
    scope: CustomerScope, folded: str, filters: Filters, page: int, page_size: int
) -> tuple[list[Hit], int]:
    """PostgreSQL search over movies and series (episodes need the index)."""
    if filters.type == "episode":
        return [], 0
    queries: list[tuple[Literal["movie", "series"], QuerySet[Any]]] = []
    if filters.type in {None, "movie"}:
        queries.append(("movie", visible_movies(scope)))
    if filters.type in {None, "series"}:
        queries.append(("series", visible_series(scope)))
    scored: list[tuple[float, float, TitleRef]] = []
    wanted = page * page_size
    total = 0
    for kind, base in queries:
        narrowed = base
        if filters.genre is not None:
            narrowed = narrowed.filter(genres=filters.genre)
        if filters.year is not None:
            narrowed = narrowed.filter(year=filters.year)
        ranked = _ranked(narrowed.distinct(), folded)
        total += ranked.count()
        for pk, score, popularity in ranked.values_list("pk", "score", "popularity")[:wanted]:
            scored.append((float(score or 0), float(popularity or 0), (kind, pk)))
    scored.sort(key=lambda row: (-row[0], -row[1], str(row[2][1])))
    window = scored[(page - 1) * page_size : wanted]
    titles = {
        ("movie" if isinstance(title, Movie) else "series", title.pk): title
        for title in hydrate(scope, [ref for _s, _p, ref in window])
    }
    hits = [Hit(ref[0], titles[ref]) for _s, _p, ref in window if ref in titles]  # type: ignore[arg-type]
    return hits, total


def people(scope: CustomerScope, query: str, limit: int = PEOPLE_LIMIT) -> list[Person]:
    """People credited in titles the customer may browse whose name matches."""
    raw = query.strip()
    credited = Q(credits__movie__in=visible_movies(scope)) | Q(
        credits__series__in=visible_series(scope)
    )
    matches = (
        Person.objects.filter(credited)
        .annotate(similarity=TrigramWordSimilarity(raw, "name"))
        .filter(
            Q(name__unaccent__icontains=raw)
            | Q(name_ar__icontains=raw)
            | Q(similarity__gte=TRIGRAM_THRESHOLD + 0.2)
        )
        .order_by("-similarity", "name")
        .distinct()
    )
    ids = list(matches.values_list("pk", flat=True)[:limit])
    found = {p.pk: p for p in Person.objects.filter(pk__in=ids).prefetch_related(art(("profile",)))}
    return [found[pk] for pk in ids if pk in found]


def search(
    scope: CustomerScope,
    query: str,
    filters: Filters | None = None,
    *,
    page: int = 1,
    page_size: int = PAGE_SIZE,
) -> SearchResult:
    global _meili_down_until  # noqa: PLW0603 (process-local circuit breaker)
    filters = filters or Filters()
    text = query[:MAX_QUERY]
    folded = normalize(text)
    if not folded or scope.nothing:
        return SearchResult(text, "meilisearch", page, page_size, 0)
    engine: Literal["meilisearch", "database"] = "meilisearch"
    hits: list[Hit] = []
    total = 0
    if time.monotonic() >= _meili_down_until:
        try:
            hits, total = _meili(scope, folded, filters, page, page_size)
        except MeiliError as exc:
            logger.warning("search falling back to the database: %s", exc)
            if isinstance(exc, MeiliIndexMissing):
                from apps.search.tasks import schedule_rebuild  # noqa: PLC0415

                schedule_rebuild()
            _meili_down_until = time.monotonic() + BREAKER_S
            engine = "database"
    else:
        engine = "database"
    if engine == "database":
        hits, total = _database(scope, folded, filters, page, page_size)
    found_people = people(scope, text) if page == 1 and filters.type is None else []
    return SearchResult(text, engine, page, page_size, total, hits, found_people)


def reset_breaker() -> None:
    global _meili_down_until  # noqa: PLW0603
    _meili_down_until = 0.0
