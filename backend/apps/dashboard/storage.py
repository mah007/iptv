"""Storage usage for the admin Storage page (SPEC §8.3 item 11).

Sources are the library files that are present (`removed_at` unset); renditions are
the outputs under DATA_ROOT/renditions, except links to the source (`details.linked`),
which take no space of their own. A rendition keeps counting while its source file is
gone, until the cleanup deletes it.

Growth over 90 days is rebuilt from row timestamps: a source counts from its
`created_at` until its `removed_at`, a rendition from its `ready_at`. Renditions deleted
since are no longer known, so the rendition curve is the history of what is on disk
today. The whole set is one query batch, cached 30 s (SPEC §8.4).
"""

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from django.db import connection
from django.utils import timezone

from apps.catalog.models import Episode, MediaFile, Movie, Season, Series
from apps.dashboard.services import cached
from apps.library.models import Library
from apps.media.models import Rendition

GROWTH_DAYS = 90
LARGEST = 10
CACHE_KEY = "dashboard:storage:v1:{tz}"

#: Bytes on disk of one rendition row: a link to the source occupies nothing.
_RENDITION_BYTES = (
    "CASE WHEN COALESCE((r.details ->> 'linked')::boolean, false) THEN 0 ELSE r.size END"
)


@dataclass(frozen=True, slots=True)
class LibraryUsage:
    id: str
    name: str
    kind: str
    files: int
    sources: int
    renditions: int


@dataclass(frozen=True, slots=True)
class GrowthPoint:
    date: date
    sources: int
    renditions: int


@dataclass(frozen=True, slots=True)
class TitleUsage:
    kind: str
    id: str
    title: str
    title_ar: str
    year: int | None
    files: int
    sources: int
    renditions: int


@dataclass(frozen=True, slots=True)
class StorageUsage:
    as_of: datetime
    time_zone: str
    files: int = 0
    sources: int = 0
    renditions: int = 0
    libraries: list[LibraryUsage] = field(default_factory=list)
    growth: list[GrowthPoint] = field(default_factory=list)
    largest: list[TitleUsage] = field(default_factory=list)


def _libraries() -> list[LibraryUsage]:
    libraries = Library._meta.db_table
    files = MediaFile._meta.db_table
    renditions = Rendition._meta.db_table
    sql = f"""
        SELECT l.id, l.name, l.kind,
            COALESCE(src.files, 0), COALESCE(src.bytes, 0), COALESCE(out.bytes, 0)
        FROM {libraries} l
        LEFT JOIN (
            SELECT library_id, COUNT(*) AS files, SUM(size) AS bytes
            FROM {files} WHERE removed_at IS NULL GROUP BY library_id
        ) src ON src.library_id = l.id
        LEFT JOIN (
            SELECT f.library_id, SUM({_RENDITION_BYTES}) AS bytes
            FROM {renditions} r JOIN {files} f ON f.id = r.media_file_id
            GROUP BY f.library_id
        ) out ON out.library_id = l.id
        ORDER BY COALESCE(src.bytes, 0) + COALESCE(out.bytes, 0) DESC, l.name
    """  # noqa: S608
    with connection.cursor() as cursor:
        cursor.execute(sql)
        rows = cursor.fetchall()
    return [
        LibraryUsage(
            id=str(pk), name=name, kind=kind, files=n, sources=int(src), renditions=int(out)
        )
        for pk, name, kind, n, src, out in rows
    ]


def _growth(now: datetime, tz: ZoneInfo) -> list[GrowthPoint]:
    """Bytes on disk at the end of each of the last 90 days (the last one is today, so far)."""
    files = MediaFile._meta.db_table
    renditions = Rendition._meta.db_table
    today = now.astimezone(tz).date()
    first_day = today - timedelta(days=GROWTH_DAYS - 1)
    start = datetime.combine(first_day, datetime.min.time(), tzinfo=tz)
    # Each row adds its bytes on the day it appeared and takes them back on the day it
    # went; what happened before the window is the opening balance.
    sql = f"""
        WITH events AS (
            SELECT created_at AS at, size AS sources, 0::bigint AS renditions
            FROM {files} WHERE created_at <= %(now)s
            UNION ALL
            SELECT removed_at, -size, 0 FROM {files}
            WHERE removed_at IS NOT NULL AND removed_at <= %(now)s
            UNION ALL
            SELECT r.ready_at, 0, {_RENDITION_BYTES} FROM {renditions} r
            WHERE r.ready_at IS NOT NULL AND r.ready_at <= %(now)s
        )
        SELECT CASE WHEN at < %(start)s THEN NULL ELSE (at AT TIME ZONE %(tz)s)::date END AS day,
            COALESCE(SUM(sources), 0), COALESCE(SUM(renditions), 0)
        FROM events
        GROUP BY day
    """  # noqa: S608
    with connection.cursor() as cursor:
        cursor.execute(sql, {"now": now, "start": start, "tz": str(tz)})
        deltas = {day: (int(src), int(out)) for day, src, out in cursor.fetchall()}
    sources, outputs = deltas.pop(None, (0, 0))
    points = []
    for offset in range(GROWTH_DAYS):
        day = first_day + timedelta(days=offset)
        add_sources, add_outputs = deltas.get(day, (0, 0))
        sources += add_sources
        outputs += add_outputs
        points.append(GrowthPoint(date=day, sources=sources, renditions=outputs))
    return points


def _largest() -> list[TitleUsage]:
    files = MediaFile._meta.db_table
    renditions = Rendition._meta.db_table
    file_episodes = MediaFile.episodes.through._meta.db_table
    episodes = Episode._meta.db_table
    seasons = Season._meta.db_table
    # A file of several episodes counts once for its series.
    sql = f"""
        WITH per_file AS (
            SELECT f.id, f.movie_id, CASE WHEN f.removed_at IS NULL THEN f.size ELSE 0 END AS src,
                COALESCE(out.bytes, 0) AS out
            FROM {files} f
            LEFT JOIN (
                SELECT r.media_file_id, SUM({_RENDITION_BYTES}) AS bytes
                FROM {renditions} r GROUP BY r.media_file_id
            ) out ON out.media_file_id = f.id
        ), owned AS (
            SELECT 'movie' AS kind, movie_id AS title_id, id, src, out
            FROM per_file WHERE movie_id IS NOT NULL
            UNION
            SELECT 'series', se.series_id, p.id, p.src, p.out
            FROM per_file p
            JOIN {file_episodes} fe ON fe.mediafile_id = p.id
            JOIN {episodes} e ON e.id = fe.episode_id
            JOIN {seasons} se ON se.id = e.season_id
        )
        SELECT kind, title_id, COUNT(*) FILTER (WHERE src > 0), SUM(src), SUM(out)
        FROM owned
        GROUP BY kind, title_id
        HAVING SUM(src) + SUM(out) > 0
        ORDER BY SUM(src) + SUM(out) DESC
        LIMIT %(limit)s
    """  # noqa: S608
    with connection.cursor() as cursor:
        cursor.execute(sql, {"limit": LARGEST})
        rows = cursor.fetchall()
    titles: dict[tuple[str, str], Movie | Series] = {}
    movie_ids = [row[1] for row in rows if row[0] == "movie"]
    series_ids = [row[1] for row in rows if row[0] == "series"]
    if movie_ids:
        titles.update((("movie", str(m.pk)), m) for m in Movie.objects.filter(pk__in=movie_ids))
    if series_ids:
        titles.update((("series", str(s.pk)), s) for s in Series.objects.filter(pk__in=series_ids))
    largest = []
    for kind, title_id, count, src, out in rows:
        title = titles.get((kind, str(title_id)))
        if title is None:  # deleted between the two queries
            continue
        largest.append(
            TitleUsage(
                kind=kind,
                id=str(title.pk),
                title=title.title,
                title_ar=title.title_ar,
                year=title.year,
                files=count,
                sources=int(src),
                renditions=int(out),
            )
        )
    return largest


def compute_storage(*, tz: ZoneInfo, now: datetime | None = None) -> StorageUsage:
    moment = now or timezone.now()
    libraries = _libraries()
    return StorageUsage(
        as_of=moment,
        time_zone=str(tz),
        files=sum(item.files for item in libraries),
        sources=sum(item.sources for item in libraries),
        renditions=sum(item.renditions for item in libraries),
        libraries=libraries,
        growth=_growth(moment, tz),
        largest=_largest(),
    )


def _decode(data: dict[str, Any]) -> StorageUsage:
    return StorageUsage(
        **{
            **data,
            "libraries": [LibraryUsage(**item) for item in data["libraries"]],
            "growth": [GrowthPoint(**point) for point in data["growth"]],
            "largest": [TitleUsage(**item) for item in data["largest"]],
        }
    )


def storage(tz: ZoneInfo) -> StorageUsage:
    """Storage usage in the admin's time zone, at most 30 s old."""
    return cached(CACHE_KEY.format(tz=str(tz)), lambda: compute_storage(tz=tz), asdict, _decode)
