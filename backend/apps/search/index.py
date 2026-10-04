"""The Meilisearch index `titles`: movies, series and episodes (SPEC §7.8).

Searchable text is stored *normalised* (`apps.search.normalize`, the matcher's
normaliser): Arabic letter variants, tashkeel, articles and punctuation are folded
the same way at index and at query time, so "المصفوفة", "مصفوفه" and "The Matrix"
find their title whatever the spelling. Meilisearch adds typo tolerance on top.
Display text is never read from the index: hits are ids, loaded from PostgreSQL
through the customer's visibility rules (`apps.catalog.browse`).

- `index_titles(kind, ids)` (Celery, on `catalog_changed`) re-indexes the titles (a
  series with its episodes) and drops the documents of titles that are gone.
- `rebuild()` (nightly, `manage.py search_reindex`) builds a fresh index beside the live
  one and swaps them atomically, so searches never see a half-built index.
"""

from collections import defaultdict
from collections.abc import Iterable, Iterator, Sequence
from typing import Any, Final
from uuid import UUID

from django.db.models import Prefetch
from django.utils import timezone

from apps.catalog.models import Credit, CreditRole, Episode, MediaFile, Movie, Series
from apps.catalog.services import playable_files
from apps.search.meili import MeiliClient, client
from apps.search.normalize import normalize

INDEX: Final = "titles"
BATCH: Final = 500
MAX_PEOPLE: Final = 10

SETTINGS: Final[dict[str, Any]] = {
    # SPEC §7.8 order: titles first, then people, then overviews.
    "searchableAttributes": [
        "title",
        "title_ar",
        "original_title",
        "alt_titles",
        "cast_names",
        "director_names",
        "overview",
        "overview_ar",
    ],
    "filterableAttributes": [
        "type",
        "genre_ids",
        "year",
        "category_ids",
        "status",
        "is_adult",
        "series_id",
    ],
    "sortableAttributes": ["popularity", "year", "added_at", "rating"],
    "displayedAttributes": ["id", "type", "pk", "series_id"],
    "typoTolerance": {
        "enabled": True,
        "minWordSizeForTypos": {"oneTypo": 4, "twoTypos": 8},
    },
    "pagination": {"maxTotalHits": 1000},
}


def doc_id(kind: str, pk: UUID) -> str:
    return f"{kind}-{pk}"


def _people(credits: Iterable[Credit], role: str) -> list[str]:
    names: dict[str, None] = {}
    for credit in credits:
        if credit.role != role:
            continue
        for value in (credit.person.name, credit.person.name_ar):
            folded = normalize(value or "")
            if folded:
                names.setdefault(folded, None)
        if len(names) >= MAX_PEOPLE * 2:
            break
    return list(names)


def _title_doc(kind: str, title: Movie | Series) -> dict[str, Any]:
    categories = list(title.categories.all())
    credits = list(title.credits.all())
    return {
        "id": doc_id(kind, title.pk),
        "type": kind,
        "pk": str(title.pk),
        "series_id": None,
        "title": normalize(title.title),
        "title_ar": normalize(title.title_ar),
        "original_title": normalize(title.original_title),
        "alt_titles": [folded for name in title.alt_titles or [] if (folded := normalize(name))],
        "cast_names": _people(credits, CreditRole.CAST),
        "director_names": _people(credits, CreditRole.DIRECTOR),
        "overview": normalize(title.overview),
        "overview_ar": normalize(title.overview_ar),
        "genre_ids": [str(genre.pk) for genre in title.genres.all()],
        "year": title.year,
        "category_ids": [str(category.pk) for category in categories],
        "is_adult": any(category.is_adult for category in categories),
        "status": title.status,
        "popularity": title.popularity,
        "rating": title.rating,
        "added_at": int(title.created_at.timestamp()),
    }


def _episode_doc(
    episode: Episode, series: Series, series_doc: dict[str, Any], playable: bool
) -> dict[str, Any]:
    return {
        "id": doc_id("episode", episode.pk),
        "type": "episode",
        "pk": str(episode.pk),
        "series_id": str(series.pk),
        "title": normalize(episode.title),
        "title_ar": normalize(episode.title_ar),
        "original_title": "",
        "alt_titles": [series_doc["title"], series_doc["title_ar"]],
        "cast_names": [],
        "director_names": [],
        "overview": normalize(episode.overview),
        "overview_ar": normalize(episode.overview_ar),
        "genre_ids": series_doc["genre_ids"],
        "year": episode.air_date.year if episode.air_date else series_doc["year"],
        "category_ids": series_doc["category_ids"],
        "is_adult": series_doc["is_adult"],
        # An episode is searchable once it plays, and while its series is visible.
        "status": series_doc["status"] if playable else "processing",
        "popularity": series_doc["popularity"],
        "rating": episode.rating,
        "added_at": int(episode.created_at.timestamp()),
    }


def _credits() -> "Prefetch[Any]":
    return Prefetch("credits", queryset=Credit.objects.select_related("person").order_by("order"))


def movie_documents(ids: Sequence[UUID] | None = None) -> Iterator[dict[str, Any]]:
    query = Movie.objects.prefetch_related("categories", "genres", _credits()).order_by("pk")
    if ids is not None:
        query = query.filter(pk__in=ids)
    for movie in query.iterator(chunk_size=BATCH):
        yield _title_doc("movie", movie)


def series_documents(ids: Sequence[UUID] | None = None) -> Iterator[dict[str, Any]]:
    """Each series, then its episodes; a fixed number of queries per chunk of series."""
    query = Series.objects.order_by("pk")
    if ids is not None:
        query = query.filter(pk__in=ids)
    keys = list(query.values_list("pk", flat=True))
    for start in range(0, len(keys), BATCH):
        chunk = keys[start : start + BATCH]
        playable = set(
            MediaFile.objects.filter(playable_files(), episodes__season__series__in=chunk)
            .values_list("episodes", flat=True)
            .distinct()
        )
        rows = (
            Series.objects.filter(pk__in=chunk)
            .prefetch_related("categories", "genres", _credits(), "seasons__episodes")
            .order_by("pk")
        )
        for series in rows:
            series_doc = _title_doc("series", series)
            yield series_doc
            for season in series.seasons.all():
                for episode in season.episodes.all():
                    yield _episode_doc(episode, series, series_doc, episode.pk in playable)


def _batches(documents: Iterable[dict[str, Any]]) -> Iterator[list[dict[str, Any]]]:
    batch: list[dict[str, Any]] = []
    for document in documents:
        batch.append(document)
        if len(batch) >= BATCH:
            yield batch
            batch = []
    if batch:
        yield batch


_configured: set[str] = set()


def ensure_index(meili: MeiliClient, uid: str | None = None) -> None:
    """Create the index (default: `INDEX`) if needed and apply `SETTINGS` (once per
    process and index)."""
    uid = uid or INDEX
    if uid in _configured and meili.index_exists(uid):
        return
    if not meili.index_exists(uid):
        meili.wait(meili.create_index(uid, "id"))
    meili.wait(meili.update_settings(uid, SETTINGS))
    _configured.add(uid)


def index_titles(kind: str, ids: Sequence[UUID]) -> int:
    """Re-index these movies or series (series with their episodes); returns documents
    written. Documents of titles that no longer exist are deleted.

    Meilisearch runs an index's tasks in order, so a series' old episodes are deleted
    before its current ones are added: episodes removed since the last run disappear."""
    meili = client()
    ensure_index(meili)
    documents = list(movie_documents(ids) if kind == "movie" else series_documents(ids))
    present = {UUID(doc["pk"]) for doc in documents if doc["type"] == kind}
    tasks = []
    gone = [doc_id(kind, pk) for pk in ids if pk not in present]
    if gone:
        tasks.append(meili.delete_documents(INDEX, gone))
    if kind == "series":
        for pk in ids:
            tasks.append(meili.delete_by_filter(INDEX, f'type = "episode" AND series_id = "{pk}"'))
    tasks += [meili.add_documents(INDEX, batch) for batch in _batches(documents)]
    for task in tasks:
        meili.wait(task)
    return len(documents)


def rebuild() -> dict[str, int]:
    """Build every document into a new index and swap it in atomically."""
    meili = client()
    ensure_index(meili)
    staging = f"{INDEX}_{timezone.now():%Y%m%d%H%M%S}"
    ensure_index(meili, staging)
    counts: dict[str, int] = defaultdict(int)
    tasks = []
    for batch in _batches([*movie_documents(), *series_documents()]):
        for document in batch:
            counts[document["type"]] += 1
        tasks.append(meili.add_documents(staging, batch))
    for task in tasks:
        meili.wait(task)
    meili.wait(meili.swap(INDEX, staging))
    meili.wait(meili.delete_index(staging))
    return dict(counts)
