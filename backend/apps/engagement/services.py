"""Engagement services (SPEC §7.9, §9, §10 Engagement): watch progress from the web
player, continue-watching, watch history, favourites and thumbs ratings.

Progress is reported by the player through the API (every 15 s, on pause and on stop),
never by the media edge: media requests stay off Postgres (SPEC §1.5). Reports closer
together than `playback.progress_min_interval_s` are dropped, and a title counts as
watched past `playback.watched_ratio` of its duration.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Literal, cast
from uuid import UUID

import redis
import structlog
from django.db import IntegrityError, transaction
from django.db.models import Q, QuerySet

from apps.accounts.models import User
from apps.catalog.browse import CustomerScope, playable_episodes, visible_movies, visible_series
from apps.catalog.models import Episode, Movie, Series
from apps.catalog.queries import Title, TitleRef, art, cards
from apps.core.errors import ErrorCode, ProblemError
from apps.core.services import get_setting
from apps.core.stores import state_redis
from apps.engagement.models import Favorite, Rating, Thumb, WatchProgress
from apps.playback.models import PlaybackSession, TitleKind

logger = structlog.get_logger(__name__)

type TitleType = Literal["movie", "series"]
#: Below this position nothing is resumed or shown in continue-watching...
RESUME_MIN_MS: Final = 30_000
#: ...nor below this share of a title shorter than five minutes (a 30-second clip resumes
#: from 3 s), whose 30 s would otherwise sit past the watched ratio.
RESUME_MIN_RATIO: Final = 0.1
CONTINUE_LIMIT: Final = 20
FAVORITES_LIMIT: Final = 500


def _title_q(title_type: str, title_id: UUID) -> Q:
    return Q(movie_id=title_id) if title_type == "movie" else Q(series_id=title_id)


def resolve_title(scope: CustomerScope, title_type: str, title_id: UUID) -> Title:
    """The visible movie or series, else NOT_FOUND."""
    query = visible_movies(scope) if title_type == "movie" else visible_series(scope)
    title = query.filter(pk=title_id).first() if title_type in {"movie", "series"} else None
    if title is None:
        raise ProblemError(ErrorCode.NOT_FOUND, "No such title.")
    return title


# --- Progress ---------------------------------------------------------------------------------


def started(position_ms: int, duration_ms: int | None) -> bool:
    """Past the opening: 30 s in, or a tenth of the way through a short title."""
    threshold = RESUME_MIN_MS
    if duration_ms is not None and duration_ms > 0:
        threshold = min(threshold, int(duration_ms * RESUME_MIN_RATIO))
    return position_ms >= threshold


def progress_body(row: WatchProgress | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "position_ms": row.position_ms,
        "duration_ms": row.duration_ms,
        "completed": row.completed,
        "updated_at": row.updated_at,
    }


def _runtime_ms(row: PlaybackSession) -> int:
    if row.title_kind == TitleKind.MOVIE:
        runtime = (
            Movie.objects.filter(pk=row.title_id).values_list("runtime_min", flat=True).first()
        )
        return int(runtime or 0) * 60_000
    episode = Episode.objects.select_related("season__series").filter(pk=row.title_id).first()
    if episode is None:
        return 0
    minutes = episode.runtime_min or episode.season.series.episode_run_time or 0
    return int(minutes) * 60_000


def _throttled(session_id: UUID) -> bool:
    interval = int(cast("int", get_setting("playback.progress_min_interval_s")))
    if interval <= 0:
        return False
    try:
        fresh = state_redis().set(f"prog:{session_id}", "1", nx=True, ex=interval)
    except redis.RedisError:
        return False
    return not fresh


@dataclass(frozen=True, slots=True)
class ProgressResult:
    saved: bool
    progress: WatchProgress | None


def record_progress(
    user: User,
    session: PlaybackSession,
    position_ms: int,
    duration_ms: int | None = None,
    *,
    force: bool = False,
) -> ProgressResult:
    """Save where the customer is in the session's title (throttled unless `force`)."""
    if session.user_id != user.pk:
        raise ProblemError(ErrorCode.NOT_FOUND, "No such playback session.")
    if session.title_kind not in (TitleKind.MOVIE, TitleKind.EPISODE):
        return ProgressResult(saved=False, progress=None)  # live TV has no position to resume
    if not force and _throttled(session.pk):
        return ProgressResult(saved=False, progress=None)
    lookup: dict[str, Any]
    defaults: dict[str, Any] = {}
    if session.title_kind == TitleKind.MOVIE:
        lookup = {"user": user, "movie_id": session.title_id}
    else:
        series_id = (
            Episode.objects.filter(pk=session.title_id)
            .values_list("season__series_id", flat=True)
            .first()
        )
        if series_id is None:
            raise ProblemError(ErrorCode.NOT_FOUND, "No such title.")
        lookup = {"user": user, "episode_id": session.title_id}
        defaults["series_id"] = series_id
    ratio = float(cast("float", get_setting("playback.watched_ratio")))
    with transaction.atomic():
        row = WatchProgress.objects.select_for_update().filter(**lookup).first()
        duration = (
            duration_ms
            if duration_ms and duration_ms > 0
            else (row.duration_ms if row and row.duration_ms else _runtime_ms(session))
        )
        position = max(0, min(position_ms, duration)) if duration else max(0, position_ms)
        values = {
            **defaults,
            "position_ms": position,
            "duration_ms": duration,
            "completed": bool(duration) and position >= ratio * duration,
            "last_session_id": session.pk,
        }
        if row is None:
            try:
                with transaction.atomic():
                    row = WatchProgress.objects.create(**lookup, **values)
            except IntegrityError:  # a concurrent first report
                row = WatchProgress.objects.select_for_update().get(**lookup)
        for name, value in values.items():
            setattr(row, name, value)
        row.save()
    return ProgressResult(saved=True, progress=row)


def resume_position(user: User, kind: TitleKind, title_id: UUID) -> int:
    """Where playback should resume: the saved position, unless watched or barely started."""
    field = "movie_id" if kind == TitleKind.MOVIE else "episode_id"
    row = WatchProgress.objects.filter(user=user, **{field: title_id}).first()
    if row is None or row.completed or not started(row.position_ms, row.duration_ms):
        return 0
    return row.position_ms


def episode_progress(user: User, episodes: Iterable[Episode]) -> dict[UUID, dict[str, Any]]:
    ids = [episode.pk for episode in episodes]
    rows = WatchProgress.objects.filter(user=user, episode_id__in=ids)
    return {
        row.episode_id: body
        for row in rows
        if row.episode_id is not None and (body := progress_body(row)) is not None
    }


def next_episode(user: User, series: Series) -> Episode | None:
    """The episode "Play" starts: the one in progress, else the one after the last
    watched, else the first."""
    playable = (
        playable_episodes()
        .filter(season__series=series)
        .select_related("season")
        .prefetch_related(art(("still",)))
        .order_by("season__number", "number")
    )
    latest = (
        WatchProgress.objects.filter(user=user, series=series)
        .select_related("episode__season")
        .order_by("-updated_at")
        .first()
    )
    if latest is not None and latest.episode is not None:
        if not latest.completed:
            current = playable.filter(pk=latest.episode.pk).first()
            if current is not None:
                return current
        after = playable.filter(
            Q(season__number__gt=latest.episode.season.number)
            | Q(season__number=latest.episode.season.number, number__gt=latest.episode.number)
        ).first()
        if after is not None:
            return after
    # Specials (season 0) last: start a new viewer on season 1.
    return playable.exclude(season__number=0).first() or playable.first()


# --- Continue watching and history ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WatchItem:
    """A row of continue-watching or the watch history."""

    progress: WatchProgress | None
    title: Title
    episode: Episode | None
    position_ms: int
    duration_ms: int
    completed: bool
    updated_at: datetime


def _visible_maps(
    scope: CustomerScope, rows: Sequence[WatchProgress]
) -> tuple[dict[UUID, Movie], dict[UUID, Series]]:
    movie_ids = {row.movie_id for row in rows if row.movie_id}
    series_ids = {row.series_id for row in rows if row.series_id}
    movies = {m.pk: m for m in cards(visible_movies(scope).filter(pk__in=movie_ids))}
    series = {s.pk: s for s in cards(visible_series(scope).filter(pk__in=series_ids))}
    return movies, series


def _progress_rows(user: User) -> QuerySet[WatchProgress]:
    return WatchProgress.objects.filter(user=user).select_related("episode__season")


def continue_watching(
    user: User, scope: CustomerScope, limit: int = CONTINUE_LIMIT
) -> list[WatchItem]:
    """Unfinished titles, newest first, one entry per series (its latest episode); a
    series whose latest episode is finished continues with the next one."""
    rows = list(_progress_rows(user).order_by("-updated_at")[: limit * 5])
    movies, series_map = _visible_maps(scope, rows)
    items: list[WatchItem] = []
    seen_series: set[UUID] = set()
    for row in rows:
        if row.movie_id is not None:
            movie = movies.get(row.movie_id)
            if movie is None or row.completed or not started(row.position_ms, row.duration_ms):
                continue
            items.append(
                WatchItem(row, movie, None, row.position_ms, row.duration_ms, False, row.updated_at)
            )
        elif row.series_id is not None and row.series_id not in seen_series:
            seen_series.add(row.series_id)
            series = series_map.get(row.series_id)
            if series is None:
                continue
            if (
                not row.completed
                and started(row.position_ms, row.duration_ms)
                and row.episode is not None
            ):
                episode = _with_still(row.episode)
                items.append(
                    WatchItem(
                        row,
                        series,
                        episode,
                        row.position_ms,
                        row.duration_ms,
                        False,
                        row.updated_at,
                    )
                )
                continue
            following = next_episode(user, series)
            if following is not None and following.pk != row.episode_id:
                items.append(WatchItem(None, series, following, 0, 0, False, row.updated_at))
        if len(items) >= limit:
            break
    return items


def _with_still(episode: Episode) -> Episode:
    return (
        Episode.objects.select_related("season")
        .prefetch_related(art(("still",)))
        .get(pk=episode.pk)
    )


def history(user: User, scope: CustomerScope) -> QuerySet[WatchProgress]:
    """The watch history: every progress row, newest first (paginate it)."""
    return _progress_rows(user).order_by("-updated_at", "-id")


def history_items(scope: CustomerScope, rows: Sequence[WatchProgress]) -> list[WatchItem]:
    movies, series_map = _visible_maps(scope, rows)
    episodes = {
        episode.pk: episode
        for episode in Episode.objects.filter(
            pk__in=[row.episode_id for row in rows if row.episode_id]
        )
        .select_related("season")
        .prefetch_related(art(("still",)))
    }
    items = []
    for row in rows:
        title: Title | None = (
            movies.get(row.movie_id) if row.movie_id else series_map.get(row.series_id)  # type: ignore[arg-type]
        )
        if title is None:
            continue
        episode = episodes.get(row.episode_id) if row.episode_id else None
        items.append(
            WatchItem(
                row, title, episode, row.position_ms, row.duration_ms, row.completed, row.updated_at
            )
        )
    return items


def forget(user: User, progress_id: UUID) -> None:
    deleted, _ = WatchProgress.objects.filter(user=user, pk=progress_id).delete()
    if not deleted:
        raise ProblemError(ErrorCode.NOT_FOUND, "No such history entry.")


# --- Favourites and ratings -------------------------------------------------------------------


def favorites(user: User, scope: CustomerScope) -> list[Title]:
    rows = list(Favorite.objects.filter(user=user).order_by("-created_at")[:FAVORITES_LIMIT])
    refs: list[TitleRef] = [
        ("movie", row.movie_id) if row.movie_id else ("series", cast("UUID", row.series_id))
        for row in rows
    ]
    from apps.catalog.queries import hydrate  # noqa: PLC0415

    return hydrate(scope, refs)


def add_favorite(user: User, scope: CustomerScope, title_type: str, title_id: UUID) -> bool:
    """True when added, False when already there."""
    resolve_title(scope, title_type, title_id)
    field = "movie_id" if title_type == "movie" else "series_id"
    try:
        with transaction.atomic():
            lookup: dict[str, Any] = {"user": user, field: title_id}
            _, created = Favorite.objects.get_or_create(**lookup)
    except IntegrityError:
        return False
    return created


def remove_favorite(user: User, title_type: str, title_id: UUID) -> None:
    Favorite.objects.filter(_title_q(title_type, title_id), user=user).delete()


def set_rating(
    user: User, scope: CustomerScope, title_type: str, title_id: UUID, value: str | None
) -> str | None:
    """Thumbs "up", "down", or None to clear; returns the stored value."""
    resolve_title(scope, title_type, title_id)
    field = "movie_id" if title_type == "movie" else "series_id"
    if value is None:
        Rating.objects.filter(user=user, **{field: title_id}).delete()
        return None
    thumb = Thumb.UP if value == "up" else Thumb.DOWN
    with transaction.atomic():
        lookup: dict[str, Any] = {"user": user, field: title_id}
        Rating.objects.update_or_create(**lookup, defaults={"value": thumb})
    return value


def viewer_state(user: User, title: Title) -> dict[str, Any]:
    """Favourite, thumbs and progress of the customer on one title (three queries)."""
    is_movie = isinstance(title, Movie)
    where: dict[str, Any] = {"movie": title} if is_movie else {"series": title}
    rating = Rating.objects.filter(user=user, **where).values_list("value", flat=True).first()
    progress = None
    if is_movie:
        progress = progress_body(WatchProgress.objects.filter(user=user, **where).first())
    return {
        "favorite": Favorite.objects.filter(user=user, **where).exists(),
        "rating": None if rating is None else ("up" if rating == Thumb.UP else "down"),
        "progress": progress,
    }


def watched_refs(user: User) -> set[TitleRef]:
    """Titles the customer has watched any of (movies, and series by any episode)."""
    rows = WatchProgress.objects.filter(user=user).values_list("movie_id", "series_id")
    refs: set[TitleRef] = set()
    for movie_id, series_id in rows:
        if movie_id:
            refs.add(("movie", movie_id))
        elif series_id:
            refs.add(("series", series_id))
    return refs


def disliked_refs(user: User) -> set[TitleRef]:
    rows = Rating.objects.filter(user=user, value=Thumb.DOWN).values_list("movie_id", "series_id")
    return {("movie", m) if m else ("series", s) for m, s in rows}


def log_progress_error(exc: Exception) -> None:
    logger.warning("progress.save_failed", error=type(exc).__name__)
