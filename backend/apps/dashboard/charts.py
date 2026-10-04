"""Dashboard charts (SPEC §8.3 Dashboard, §10 `admin/dashboard/timeseries`).

Computed from Postgres, never from a media request's path: concurrent streams over
24 h in 15-minute buckets, sign-ups against access that ended over 30 days, plays per
day, plays by category (top 8) and the most played titles (top 10). Days are the
admin's calendar days in their time zone. The whole set is one query batch, cached
30 s per time zone (SPEC §8.4).

Until billing lands, "sign-ups" are new customer accounts and "churn" is customer
access periods that ended; Prometheus series (egress per edge) arrive with M13.
"""

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from django.db import connection
from django.db.models import Count
from django.db.models.functions import TruncDate
from django.utils import timezone

from apps.accounts.models import CustomerAccess, User
from apps.catalog.models import Category, Episode, Movie, Season, Series
from apps.catalog.serializers import ImageSerializer, pick_image
from apps.dashboard.services import cached
from apps.playback.models import PlaybackSession

STREAM_BUCKET = timedelta(minutes=15)
STREAM_WINDOW = timedelta(hours=24)
DAYS = 30
TOP_CATEGORIES = 8
TOP_TITLES = 10
CACHE_KEY = "dashboard:timeseries:v1:{tz}"


@dataclass(frozen=True, slots=True)
class StreamPoint:
    at: datetime
    streams: int


@dataclass(frozen=True, slots=True)
class DayPoint:
    date: date
    signups: int = 0
    churned: int = 0
    plays: int = 0
    watch_hours: float = 0.0


@dataclass(frozen=True, slots=True)
class CategoryPlays:
    id: str
    kind: str
    name_en: str
    name_ar: str
    plays: int


@dataclass(frozen=True, slots=True)
class TopTitle:
    kind: str
    id: str
    title: str
    title_ar: str
    year: int | None
    plays: int
    watch_hours: float
    poster: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class Timeseries:
    as_of: datetime
    time_zone: str
    bucket_minutes: int
    streams: list[StreamPoint] = field(default_factory=list)
    streams_peak: int = 0
    streams_peak_at: datetime | None = None
    days: list[DayPoint] = field(default_factory=list)
    categories: list[CategoryPlays] = field(default_factory=list)
    top_titles: list[TopTitle] = field(default_factory=list)


def _floor(moment: datetime, step: timedelta) -> datetime:
    seconds = step.total_seconds()
    return datetime.fromtimestamp((moment.timestamp() // seconds) * seconds, tz=moment.tzinfo)


def _stream_points(now: datetime) -> list[StreamPoint]:
    """Streams that played at any time in each 15-minute bucket of the last 24 h."""
    last = _floor(now, STREAM_BUCKET)
    first = last - STREAM_WINDOW + STREAM_BUCKET
    sessions = PlaybackSession._meta.db_table
    # Open sessions, plus closed ones that started at most a day before the window and
    # ended inside it: both arms use an index (started_at; the partial index on open rows).
    sql = f"""
        WITH played AS (
            SELECT s.started_at, COALESCE(s.ended_at, %(now)s) AS ended_at
            FROM {sessions} s
            WHERE s.ended_at IS NULL
               OR (s.started_at >= %(first)s - INTERVAL '1 day' AND s.ended_at >= %(first)s)
        )
        SELECT bucket.start, COUNT(played.started_at)
        FROM generate_series(%(first)s::timestamptz, %(last)s::timestamptz, %(step)s)
            AS bucket(start)
        LEFT JOIN played
            ON played.started_at < bucket.start + %(step)s
            AND played.ended_at >= bucket.start
        GROUP BY bucket.start
        ORDER BY bucket.start
    """  # noqa: S608 (table names come from model metadata, values are parameters)
    with connection.cursor() as cursor:
        params: dict[str, Any] = {"first": first, "last": last, "step": STREAM_BUCKET, "now": now}
        cursor.execute(sql, params)
        rows = cursor.fetchall()
    return [StreamPoint(at=start, streams=count) for start, count in rows]


def _days(now: datetime, tz: ZoneInfo) -> list[DayPoint]:
    today = now.astimezone(tz).date()
    first_day = today - timedelta(days=DAYS - 1)
    start = datetime.combine(first_day, datetime.min.time(), tzinfo=tz)
    signups = dict(
        User.objects.filter(is_staff=False, created_at__gte=start, created_at__lte=now)
        .annotate(day=TruncDate("created_at", tzinfo=tz))
        .values_list("day")
        .annotate(n=Count("pk"))
        .values_list("day", "n")
    )
    churned = dict(
        CustomerAccess.objects.filter(
            user__is_staff=False, expires_at__gte=start, expires_at__lte=now
        )
        .annotate(day=TruncDate("expires_at", tzinfo=tz))
        .values_list("day")
        .annotate(n=Count("pk"))
        .values_list("day", "n")
    )
    sessions = PlaybackSession._meta.db_table
    sql = f"""
        SELECT (s.started_at AT TIME ZONE %(tz)s)::date AS day, COUNT(*),
            COALESCE(SUM(EXTRACT(EPOCH FROM
                COALESCE(s.ended_at, s.last_heartbeat_at) - s.started_at)), 0)
        FROM {sessions} s
        WHERE s.started_at >= %(start)s AND s.started_at <= %(now)s
        GROUP BY day
    """  # noqa: S608
    with connection.cursor() as cursor:
        cursor.execute(sql, {"tz": str(tz), "start": start, "now": now})
        plays = {day: (count, float(seconds)) for day, count, seconds in cursor.fetchall()}
    points = []
    for offset in range(DAYS):
        day = first_day + timedelta(days=offset)
        count, seconds = plays.get(day, (0, 0.0))
        points.append(
            DayPoint(
                date=day,
                signups=signups.get(day, 0),
                churned=churned.get(day, 0),
                plays=count,
                watch_hours=round(max(seconds, 0.0) / 3600, 2),
            )
        )
    return points


def _plays_sql(start_param: str) -> str:
    """Plays since `start_param` as (kind, title_id, seconds): episodes count for their series."""
    sessions = PlaybackSession._meta.db_table
    episodes = Episode._meta.db_table
    seasons = Season._meta.db_table
    seconds = "EXTRACT(EPOCH FROM COALESCE(s.ended_at, s.last_heartbeat_at) - s.started_at)"
    return f"""
        SELECT 'movie' AS kind, s.title_id AS title_id, {seconds} AS seconds
        FROM {sessions} s
        WHERE s.title_kind = 'movie' AND s.started_at >= {start_param}
        UNION ALL
        SELECT 'series', se.series_id, {seconds}
        FROM {sessions} s
        JOIN {episodes} e ON e.id = s.title_id
        JOIN {seasons} se ON se.id = e.season_id
        WHERE s.title_kind = 'episode' AND s.started_at >= {start_param}
    """  # noqa: S608


def _categories(since: datetime) -> list[CategoryPlays]:
    movie_categories = Movie.categories.through._meta.db_table
    series_categories = Series.categories.through._meta.db_table
    categories = Category._meta.db_table
    sql = f"""
        WITH plays AS ({_plays_sql("%(since)s")})
        SELECT c.id, c.kind, c.name_en, c.name_ar, COUNT(*) AS plays
        FROM (
            SELECT mc.category_id FROM plays p
            JOIN {movie_categories} mc ON p.kind = 'movie' AND mc.movie_id = p.title_id
            UNION ALL
            SELECT sc.category_id FROM plays p
            JOIN {series_categories} sc ON p.kind = 'series' AND sc.series_id = p.title_id
        ) counted
        JOIN {categories} c ON c.id = counted.category_id
        GROUP BY c.id, c.kind, c.name_en, c.name_ar
        ORDER BY plays DESC, c.name_en
        LIMIT %(limit)s
    """  # noqa: S608
    with connection.cursor() as cursor:
        cursor.execute(sql, {"since": since, "limit": TOP_CATEGORIES})
        rows = cursor.fetchall()
    return [
        CategoryPlays(id=str(pk), kind=kind, name_en=name_en, name_ar=name_ar, plays=plays)
        for pk, kind, name_en, name_ar, plays in rows
    ]


def _top_titles(since: datetime) -> list[TopTitle]:
    sql = f"""
        WITH plays AS ({_plays_sql("%(since)s")})
        SELECT kind, title_id, COUNT(*) AS plays, COALESCE(SUM(seconds), 0) AS seconds
        FROM plays
        GROUP BY kind, title_id
        ORDER BY plays DESC, seconds DESC
        LIMIT %(limit)s
    """  # noqa: S608
    with connection.cursor() as cursor:
        cursor.execute(sql, {"since": since, "limit": TOP_TITLES})
        rows = cursor.fetchall()
    ids = {"movie": [row[1] for row in rows if row[0] == "movie"]}
    ids["series"] = [row[1] for row in rows if row[0] == "series"]
    titles: dict[tuple[str, str], Movie | Series] = {}
    if ids["movie"]:
        for movie in Movie.objects.filter(pk__in=ids["movie"]).prefetch_related("images"):
            titles["movie", str(movie.pk)] = movie
    if ids["series"]:
        for show in Series.objects.filter(pk__in=ids["series"]).prefetch_related("images"):
            titles["series", str(show.pk)] = show
    top = []
    for kind, title_id, plays, seconds in rows:
        title = titles.get((kind, str(title_id)))
        if title is None:  # deleted since it played
            continue
        image = pick_image(list(title.images.all()), "poster")
        top.append(
            TopTitle(
                kind=kind,
                id=str(title.pk),
                title=title.title,
                title_ar=title.title_ar,
                year=title.year,
                plays=plays,
                watch_hours=round(max(float(seconds), 0.0) / 3600, 2),
                poster=dict(ImageSerializer(image).data) if image is not None else None,
            )
        )
    return top


def compute_timeseries(*, tz: ZoneInfo, now: datetime | None = None) -> Timeseries:
    moment = now or timezone.now()
    streams = _stream_points(moment)
    peak = max(streams, key=lambda point: point.streams, default=None)
    since = moment - timedelta(days=DAYS)
    return Timeseries(
        as_of=moment,
        time_zone=str(tz),
        bucket_minutes=int(STREAM_BUCKET.total_seconds() // 60),
        streams=streams,
        streams_peak=peak.streams if peak is not None else 0,
        streams_peak_at=peak.at if peak is not None and peak.streams > 0 else None,
        days=_days(moment, tz),
        categories=_categories(since),
        top_titles=_top_titles(since),
    )


def _encode(series: Timeseries) -> dict[str, Any]:
    return asdict(series)


def _decode(data: dict[str, Any]) -> Timeseries:
    return Timeseries(
        **{
            **data,
            "streams": [StreamPoint(**point) for point in data["streams"]],
            "days": [DayPoint(**point) for point in data["days"]],
            "categories": [CategoryPlays(**item) for item in data["categories"]],
            "top_titles": [TopTitle(**item) for item in data["top_titles"]],
        }
    )


def timeseries(tz: ZoneInfo) -> Timeseries:
    """The dashboard charts in the admin's time zone, at most 30 s old."""
    return cached(
        CACHE_KEY.format(tz=str(tz)),
        lambda: compute_timeseries(tz=tz),
        _encode,
        _decode,
    )
