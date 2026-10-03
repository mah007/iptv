"""The reconciliation scan (SPEC §7.1): new, changed, moved, back and removed files,
the per-library lock, progress, and when scans are queued."""

import json
import os
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from django.utils import timezone

from apps.catalog.models import Episode, FileState, MediaFile, Movie, Season, Series, TitleStatus
from apps.core.stores import state_redis
from apps.library import services
from apps.library.models import Library, ScanJob, ScanStatus, ScanTrigger

pytestmark = pytest.mark.django_db


def write(root: Path, key: str, content: bytes | None = None) -> Path:
    """A fake video; its content defaults to its key, so no two files look alike."""
    path = root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key.encode() if content is None else content)
    return path


def scan(library: Library, path: str = "") -> tuple[ScanJob, list[UUID]]:
    queued: list[UUID] = []
    job = ScanJob.objects.create(library=library, trigger=ScanTrigger.MANUAL, path=path)
    services.run_scan(job, enqueue=queued.append)
    job.refresh_from_db()
    return job, queued


def counts(job: ScanJob) -> tuple[int, ...]:
    return (job.found, job.new, job.changed, job.moved, job.removed, job.errors)


def test_new_files_are_recorded_and_queued(make_library: Callable[..., Library]) -> None:
    library = make_library()
    root = Path(library.path)
    write(root, "The.Matrix.1999.mkv")
    write(root, "Inception (2010)/Inception.2010.mkv")
    write(root, "Inception (2010)/sample.mkv")  # a sample: ignored by the parser
    write(root, "Inception (2010)/poster.jpg")  # not a video
    job, queued = scan(library)
    assert job.status == ScanStatus.DONE
    assert counts(job) == (3, 2, 0, 0, 0, 0)
    files = {f.storage_key: f for f in MediaFile.objects.all()}
    assert set(files) == {"The.Matrix.1999.mkv", "Inception (2010)/Inception.2010.mkv"}
    matrix = files["The.Matrix.1999.mkv"]
    assert (matrix.size, matrix.state, len(matrix.xxhash64)) == (19, FileState.PENDING, 16)
    assert matrix.parse_result["title"] == "The Matrix"
    assert set(queued) == {f.pk for f in files.values()}
    assert "new: The.Matrix.1999.mkv" in job.log
    library.refresh_from_db()
    assert library.last_scan_at is not None
    assert library.stats["files"] == 2
    assert library.stats["pending"] == 2


def test_rescans_skip_unchanged_files(make_library: Callable[..., Library]) -> None:
    library = make_library()
    write(Path(library.path), "A (2001)/A.mkv")
    scan(library)
    job, queued = scan(library)
    assert counts(job) == (1, 0, 0, 0, 0, 0)
    assert queued == []


def test_changed_content_is_reprobed(make_library: Callable[..., Library]) -> None:
    library = make_library()
    path = write(Path(library.path), "A (2001)/A.mkv")
    scan(library)
    path.write_bytes(b"new video content")
    job, queued = scan(library)
    assert counts(job) == (1, 0, 1, 0, 0, 0)
    file = MediaFile.objects.get()
    assert file.size == len(b"new video content")
    assert queued == [file.pk]


def test_a_touched_file_with_the_same_content_only_updates_mtime(
    make_library: Callable[..., Library],
) -> None:
    library = make_library()
    path = write(Path(library.path), "A (2001)/A.mkv")
    scan(library)
    stat = path.stat()
    os.utime(path, (stat.st_atime, stat.st_mtime + 100))
    job, queued = scan(library)
    assert counts(job) == (1, 0, 0, 0, 0, 0)
    assert queued == []
    mtime = MediaFile.objects.get().mtime
    assert mtime is not None
    assert mtime.timestamp() == pytest.approx(stat.st_mtime + 100)


def test_moved_files_keep_their_metadata(make_library: Callable[..., Library]) -> None:
    library = make_library()
    root = Path(library.path)
    old = write(root, "Old/The.Matrix.1999.mkv", b"matrix bytes")
    scan(library)
    movie = Movie.objects.create(title="The Matrix", year=1999, status=TitleStatus.PROCESSING)
    MediaFile.objects.update(movie=movie, state=FileState.MATCHED)
    new = root / "New" / "The.Matrix.1999.mkv"
    new.parent.mkdir()
    old.rename(new)
    job, queued = scan(library)
    assert counts(job) == (1, 0, 0, 1, 0, 0)
    assert queued == []
    file = MediaFile.objects.get()
    assert (file.storage_key, file.movie_id, file.removed_at) == (
        "New/The.Matrix.1999.mkv",
        movie.pk,
        None,
    )
    assert "moved: Old/The.Matrix.1999.mkv -> New/The.Matrix.1999.mkv" in job.log


def test_removed_files_are_soft_removed_and_titles_hidden(
    make_library: Callable[..., Library],
) -> None:
    library = make_library()
    path = write(Path(library.path), "The.Matrix.1999.mkv")
    scan(library)
    movie = Movie.objects.create(title="The Matrix", status=TitleStatus.PROCESSING)
    MediaFile.objects.update(movie=movie, state=FileState.MATCHED)
    path.unlink()
    job, _ = scan(library)
    assert counts(job) == (0, 0, 0, 0, 1, 0)
    assert MediaFile.objects.get().removed_at is not None
    movie.refresh_from_db()
    assert movie.status == TitleStatus.HIDDEN
    # The same file coming back revives the row and its title.
    write(Path(library.path), "The.Matrix.1999.mkv")
    job, queued = scan(library)
    assert counts(job) == (1, 1, 0, 0, 0, 0)
    assert queued == []
    assert MediaFile.objects.get().removed_at is None
    movie.refresh_from_db()
    assert movie.status == TitleStatus.PROCESSING


def test_episode_files_hide_their_series_when_removed(
    make_library: Callable[..., Library],
) -> None:
    library = make_library("Series", "series", "series")
    path = write(Path(library.path), "Show/Season 01/Show.S01E01.mkv")
    scan(library)
    series = Series.objects.create(title="Show", status=TitleStatus.PROCESSING)
    episode = Episode.objects.create(
        season=Season.objects.create(series=series, number=1), number=1
    )
    MediaFile.objects.get().episodes.add(episode)
    path.unlink()
    scan(library)
    series.refresh_from_db()
    assert series.status == TitleStatus.HIDDEN


def test_path_scans_only_look_at_their_folder(make_library: Callable[..., Library]) -> None:
    library = make_library()
    root = Path(library.path)
    write(root, "A (2001)/A.mkv")
    write(root, "B (2002)/B.mkv")
    scan(library)
    (root / "A (2001)" / "A.mkv").unlink()
    (root / "B (2002)" / "B.mkv").unlink()
    write(root, "C (2003)/C.mkv")
    job, queued = scan(library, path="A (2001)")
    assert counts(job) == (0, 0, 0, 0, 1, 0)
    assert queued == []
    active = MediaFile.objects.filter(removed_at__isnull=True)
    assert set(active.values_list("storage_key", flat=True)) == {"B (2002)/B.mkv"}
    job, queued = scan(library, path="C (2003)/C.mkv")
    assert counts(job) == (1, 1, 0, 0, 0, 0)
    library.refresh_from_db()
    assert library.last_scan_at is not None  # set by the full scans only
    assert job.path == "C (2003)/C.mkv"


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads everything")
def test_unreadable_files_count_as_errors(make_library: Callable[..., Library]) -> None:
    library = make_library()
    path = write(Path(library.path), "A (2001)/A.mkv")
    path.chmod(0)
    try:
        job, queued = scan(library)
    finally:
        path.chmod(0o644)
    assert counts(job) == (1, 0, 0, 0, 0, 1)
    assert queued == []
    assert "unreadable: A (2001)/A.mkv (PermissionError)" in job.log


def test_one_scan_per_library_at_a_time(make_library: Callable[..., Library]) -> None:
    library = make_library()
    lock = state_redis().lock(services.scan_lock_key(library.pk), timeout=30)
    assert lock.acquire(blocking=False)
    try:
        job = ScanJob.objects.create(library=library, trigger=ScanTrigger.MANUAL)
        with pytest.raises(services.ScanBusyError):
            services.run_scan(job)
        job.refresh_from_db()
        assert job.status == ScanStatus.QUEUED
    finally:
        lock.release()


def test_a_failing_scan_is_marked_failed(
    make_library: Callable[..., Library], monkeypatch: pytest.MonkeyPatch
) -> None:
    library = make_library()

    def explode(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError

    monkeypatch.setattr("apps.library.services.storage.walk", explode)
    job, _ = scan(library)
    assert job.status == ScanStatus.FAILED
    assert "Scan failed: RuntimeError" in job.log
    assert not state_redis().exists(services.scan_lock_key(library.pk))


def test_progress_is_published(make_library: Callable[..., Library]) -> None:
    library = make_library()
    write(Path(library.path), "A (2001)/A.mkv")
    pubsub = state_redis().pubsub()
    pubsub.subscribe(services.scan_channel(library.pk))
    assert pubsub.get_message(timeout=1)["type"] == "subscribe"
    try:
        scan(library)
        events = []
        while (
            message := pubsub.get_message(ignore_subscribe_messages=True, timeout=1)
        ) is not None:
            events.append(json.loads(message["data"]))
    finally:
        pubsub.close()
    assert events[0]["status"] == ScanStatus.RUNNING
    assert events[-1]["status"] == ScanStatus.DONE
    assert events[-1]["new"] == 1
    assert events[-1]["library_id"] == str(library.pk)


def test_request_scan_reuses_the_active_scan(
    make_library: Callable[..., Library],
    django_capture_on_commit_callbacks: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.library import tasks  # noqa: PLC0415

    sent: list[str] = []
    monkeypatch.setattr(tasks.scan_library, "delay", sent.append)
    library = make_library()
    with django_capture_on_commit_callbacks(execute=True):
        first, created = services.request_scan(library, actor=None, ip="203.0.113.9")
    assert created
    assert sent == [str(first.pk)]
    again, created = services.request_scan(library)
    assert (again.pk, created) == (first.pk, False)
    # A path scan from the watcher always gets its own job.
    watcher, created = services.request_scan(library, trigger=ScanTrigger.WATCHER, path="A")
    assert created
    assert watcher.pk != first.pk
    # A queued job that never started is replaced.
    ScanJob.objects.filter(pk=first.pk).update(created_at=timezone.now() - timedelta(hours=1))
    _fresh, created = services.request_scan(library)
    assert created
    first.refresh_from_db()
    assert first.status == ScanStatus.FAILED


def test_due_libraries(make_library: Callable[..., Library]) -> None:
    now = timezone.now()
    never = make_library("Never", folder="never")
    recent = make_library("Recent", folder="recent")
    old = make_library("Old", folder="old")
    disabled = make_library("Disabled", folder="disabled")
    busy = make_library("Busy", folder="busy")
    Library.objects.filter(pk=recent.pk).update(last_scan_at=now - timedelta(minutes=5))
    Library.objects.filter(pk=old.pk).update(last_scan_at=now - timedelta(minutes=16))
    Library.objects.filter(pk=disabled.pk).update(enabled=False)
    ScanJob.objects.create(library=busy, trigger=ScanTrigger.SCHEDULE)
    assert {lib.name for lib in services.due_libraries(now)} == {never.name, old.name}
