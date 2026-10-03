"""Celery tasks of the metadata pipeline: matching and refreshes on `metadata`, artwork
(`fetch_*`) on `images` (CELERY_TASK_ROUTES)."""

import logging

from celery import shared_task
from celery.app.task import Task

from apps.catalog.models import FileState, MediaFile, Movie, Series
from apps.metadata import services
from apps.metadata.tmdb import TMDBAuthError, TMDBRateLimitedError, TMDBRetryableError

logger = logging.getLogger(__name__)

RETRY_S = 60
MAX_RETRIES = 5


def _retry_delay(exc: Exception) -> int:
    if isinstance(exc, TMDBRateLimitedError) and exc.retry_after:
        return int(exc.retry_after) + 1
    return RETRY_S


@shared_task(bind=True, name="apps.metadata.tasks.match_media_file", max_retries=MAX_RETRIES)
def match_media_file(self: Task, file_id: str) -> None:
    """Match a probed file to TMDB (or the fixtures), or open a review."""
    try:
        services.match_file_task_body(file_id)
    except TMDBRetryableError as exc:
        raise self.retry(countdown=_retry_delay(exc), exc=exc) from None
    except TMDBAuthError:
        logger.exception("TMDB rejected the credential; check TMDB_API_KEY")
        MediaFile.objects.filter(pk=file_id).update(
            state=FileState.ERROR, error="TMDB rejected the configured credential."
        )


@shared_task(bind=True, name="apps.metadata.tasks.refresh_title", max_retries=MAX_RETRIES)
def refresh_title(self: Task, kind: str, title_id: str) -> None:
    """The admin's "Refresh metadata": fetch again, keeping locked fields."""
    try:
        if kind == "movie":
            movie = Movie.objects.filter(pk=title_id).first()
            if movie is not None:
                services.refresh_movie(movie)
        else:
            series = Series.objects.filter(pk=title_id).first()
            if series is not None:
                services.refresh_series(series)
    except TMDBRetryableError as exc:
        raise self.retry(countdown=_retry_delay(exc), exc=exc) from None


@shared_task(bind=True, name="apps.metadata.tasks.fetch_title_images", max_retries=MAX_RETRIES)
def fetch_title_images(self: Task, kind: str, title_id: str) -> None:
    """Download and convert a title's artwork into the media volume."""
    try:
        if kind == "movie":
            movie = Movie.objects.filter(pk=title_id).first()
            if movie is not None:
                services.fetch_movie_images(movie)
        else:
            series = Series.objects.filter(pk=title_id).first()
            if series is not None:
                services.fetch_series_images(series)
    except TMDBRetryableError as exc:
        raise self.retry(countdown=_retry_delay(exc), exc=exc) from None
