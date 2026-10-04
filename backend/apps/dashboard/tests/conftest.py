"""Builders for the dashboard tests: titles, plays, reviews and transcode jobs."""

from collections.abc import Callable, Iterator
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from django.core.cache import cache

from apps.accounts.models import User
from apps.catalog.models import (
    Category,
    Episode,
    FileState,
    MatchReview,
    MediaFile,
    Movie,
    Season,
    Series,
)
from apps.core.ids import uuid7
from apps.library.models import Library
from apps.media.models import JobStatus, TranscodeJob
from apps.playback.models import EndReason, PlaybackSession, TitleKind

type PlayFactory = Callable[..., PlaybackSession]


@pytest.fixture(autouse=True)
def _fresh_dashboard_cache() -> Iterator[None]:
    cache.clear()
    yield
    cache.clear()


def record_play(
    user: User,
    *,
    started: datetime,
    minutes: float | None = 30,
    movie: Movie | None = None,
    episode: Episode | None = None,
    end_reason: str = EndReason.STOPPED,
) -> PlaybackSession:
    """A recorded session; `minutes=None` leaves it open (still playing)."""
    kind = TitleKind.EPISODE if episode is not None else TitleKind.MOVIE
    target: Any = episode if episode is not None else movie
    ended = None if minutes is None else started + timedelta(minutes=minutes)
    return PlaybackSession.objects.create(
        session_key=uuid4().hex,
        user=user,
        title_kind=kind,
        title_id=target.pk if target is not None else uuid7(),
        title_name=getattr(target, "title", "") or "Unknown",
        rendition="compat",
        started_at=started,
        last_heartbeat_at=ended or started,
        ended_at=ended,
        end_reason=end_reason if ended is not None else "",
    )


@pytest.fixture
def library(make_library: Callable[..., Library]) -> Library:
    return make_library()


def make_movie(title: str, *categories: Category, **fields: Any) -> Movie:
    movie = Movie.objects.create(title=title, **fields)
    movie.categories.set(categories)
    return movie


def make_episode(series_title: str, *categories: Category) -> Episode:
    show = Series.objects.create(title=series_title, title_ar=f"{series_title} (ar)")
    show.categories.set(categories)
    season = Season.objects.create(series=show, number=1)
    return Episode.objects.create(season=season, number=1, title="Pilot")


def open_review(library: Library) -> MatchReview:
    file = MediaFile.objects.create(
        library=library, storage_key=f"{uuid4().hex}.mkv", state=FileState.REVIEW
    )
    return MatchReview.objects.create(media_file=file, kind="movie", reason="no_candidates")


def transcode_job(library: Library, status: str) -> TranscodeJob:
    file = MediaFile.objects.create(
        library=library, storage_key=f"{uuid4().hex}.mkv", state=FileState.MATCHED
    )
    return TranscodeJob.objects.create(media_file=file, status=JobStatus(status))
