"""Celery tasks of the library and metadata pipelines, run in-process."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from django.utils import timezone

from apps.catalog.models import FileState, MediaFile, Movie, Series
from apps.core.stores import state_redis
from apps.library import services, tasks
from apps.library.models import Library, ScanJob, ScanStatus, ScanTrigger
from apps.metadata import tasks as metadata_tasks
from apps.metadata.tmdb import TMDBAuthError, TMDBRateLimitedError, TMDBServerError

pytestmark = pytest.mark.django_db


def test_scan_library_runs_a_queued_job(make_library: Callable[..., Library]) -> None:
    library = make_library()
    (Path(library.path) / "A (2001).mkv").write_bytes(b"a")
    job = ScanJob.objects.create(library=library, trigger=ScanTrigger.MANUAL)
    tasks.scan_library(str(job.pk))
    job.refresh_from_db()
    assert (job.status, job.new) == (ScanStatus.DONE, 1)
    tasks.scan_library(str(job.pk))  # not queued any more: nothing happens
    tasks.scan_library("00000000-0000-0000-0000-000000000000")


def test_scan_library_waits_for_a_busy_library(make_library: Callable[..., Library]) -> None:
    library = make_library()
    job = ScanJob.objects.create(library=library, trigger=ScanTrigger.MANUAL)
    lock = state_redis().lock(services.scan_lock_key(library.pk), timeout=30)
    lock.acquire(blocking=False)
    try:
        with pytest.raises(services.ScanBusyError):  # called directly, retry re-raises
            tasks.scan_library(str(job.pk))
    finally:
        lock.release()


def test_scan_path_queues_a_watcher_scan(
    make_library: Callable[..., Library],
    django_capture_on_commit_callbacks: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[str] = []
    monkeypatch.setattr(tasks.scan_library, "delay", sent.append)
    library = make_library()
    with django_capture_on_commit_callbacks(execute=True):
        tasks.scan_path(str(library.pk), "/Film (2001)/")
    job = ScanJob.objects.get()
    assert (job.trigger, job.path, sent) == (ScanTrigger.WATCHER, "Film (2001)", [str(job.pk)])
    Library.objects.filter(pk=library.pk).update(enabled=False)
    tasks.scan_path(str(library.pk), "x")
    assert ScanJob.objects.count() == 1


def test_reconcile_queues_due_libraries(
    make_library: Callable[..., Library], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tasks.scan_library, "delay", lambda job_id: None)
    make_library()
    fresh = make_library("Fresh", folder="fresh")
    Library.objects.filter(pk=fresh.pk).update(last_scan_at=timezone.now())
    assert tasks.reconcile_libraries() == 1
    assert ScanJob.objects.get().trigger == ScanTrigger.SCHEDULE
    assert tasks.reconcile_libraries() == 0  # the first one is still queued


def test_process_media_file_task(
    make_library: Callable[..., Library], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr("apps.library.tasks.pipeline.process_file", calls.append)
    tasks.process_media_file("abc")
    assert calls == ["abc"]


@pytest.mark.usefixtures("offline_tmdb")
def test_match_task_retries_on_provider_trouble(
    make_library: Callable[..., Library], monkeypatch: pytest.MonkeyPatch
) -> None:
    library = make_library()
    file = MediaFile.objects.create(library=library, storage_key="x.mkv", state=FileState.MATCHING)

    def unavailable(file_id: str) -> None:
        raise TMDBRateLimitedError("slow down", retry_after=7)

    monkeypatch.setattr("apps.metadata.tasks.services.match_file_task_body", unavailable)
    with pytest.raises(TMDBRateLimitedError):
        metadata_tasks.match_media_file(str(file.pk))
    assert metadata_tasks._retry_delay(TMDBRateLimitedError("x", retry_after=7)) == 8
    assert metadata_tasks._retry_delay(TMDBServerError("x")) == metadata_tasks.RETRY_S

    def refused(file_id: str) -> None:
        raise TMDBAuthError("bad key")

    monkeypatch.setattr("apps.metadata.tasks.services.match_file_task_body", refused)
    metadata_tasks.match_media_file(str(file.pk))
    file.refresh_from_db()
    assert file.state == FileState.ERROR
    assert "credential" in file.error


@pytest.mark.usefixtures("offline_tmdb")
def test_refresh_and_artwork_tasks(
    make_library: Callable[..., Library], data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    movie = Movie.objects.create(title="The Matrix", tmdb_id=603)
    series = Series.objects.create(title="Breaking Bad", tmdb_id=1396)
    metadata_tasks.refresh_title("movie", str(movie.pk))
    metadata_tasks.refresh_title("series", str(series.pk))
    movie.refresh_from_db()
    assert movie.title_ar == "المصفوفة"
    metadata_tasks.fetch_title_images("movie", str(movie.pk))
    metadata_tasks.fetch_title_images("series", str(series.pk))
    assert movie.images.count() == 4
    assert series.images.count() == 4  # no episode has a file: no season art, no stills
    missing = "00000000-0000-0000-0000-000000000000"
    for kind in ("movie", "series"):
        metadata_tasks.refresh_title(kind, missing)
        metadata_tasks.fetch_title_images(kind, missing)

    def unavailable(*args: Any) -> None:
        raise TMDBServerError("down")

    monkeypatch.setattr("apps.metadata.tasks.services.refresh_movie", unavailable)
    monkeypatch.setattr("apps.metadata.tasks.services.fetch_movie_images", unavailable)
    with pytest.raises(TMDBServerError):
        metadata_tasks.refresh_title("movie", str(movie.pk))
    with pytest.raises(TMDBServerError):
        metadata_tasks.fetch_title_images("movie", str(movie.pk))


def test_beat_and_queues_are_wired() -> None:
    from django.conf import settings  # noqa: PLC0415

    from config.celery import app  # noqa: PLC0415

    entry = settings.CELERY_BEAT_SCHEDULE["library-reconcile"]
    assert entry["task"] in app.tasks
    route = app.amqp.router.route
    assert route({}, "apps.library.tasks.scan_library")["queue"].name == "scan"
    assert route({}, "apps.metadata.tasks.match_media_file")["queue"].name == "metadata"
    assert route({}, "apps.metadata.tasks.fetch_title_images")["queue"].name == "images"
