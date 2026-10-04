"""The media pipeline (ADR-0010): planning matched files, compat MP4 jobs on real ffmpeg,
retries, cancels, the admin's job actions, the on-demand fallback and reconciliation."""

import json
import os
import shutil
import subprocess
from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.catalog.models import Episode, FileState, MediaFile, Movie, Season, Series, TitleStatus
from apps.catalog.playable import playable_title
from apps.core.errors import ProblemError
from apps.core.services import reset_settings_cache
from apps.core.stores import state_redis
from apps.library.models import Library, ProcessingPolicy
from apps.media import receivers, services, tasks, worker
from apps.media.ffmpeg import FfmpegError
from apps.media.models import (
    PRIORITY_URGENT,
    JobStatus,
    Rendition,
    RenditionKind,
    RenditionStatus,
    TranscodeJob,
)
from apps.media.progress import ProgressUpdate
from apps.media.services import Prepared
from apps.playback.models import TitleKind
from apps.playback.services import RenditionKind as Kind

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
        reason="ffmpeg/ffprobe not installed",
    ),
]

type Sent = list[tuple[list[Any], dict[str, Any]]]


@pytest.fixture(autouse=True)
def _isolated_settings_cache() -> Iterator[None]:
    """These tests read the settings registry (this package's conftest skips it)."""
    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture(autouse=True)
def _clean_state() -> Iterator[None]:
    state_redis().flushdb()
    yield
    state_redis().flushdb()


@pytest.fixture(autouse=True)
def _no_broker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Matched-file saves queue a plan (apps.media.receivers); no broker in these tests."""
    monkeypatch.setattr(tasks.prepare_media_file, "delay", lambda *_a, **_k: None)


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> Sent:
    """Messages for run_transcode_job, captured instead of reaching a broker."""
    messages: Sent = []
    monkeypatch.setattr(
        tasks.run_transcode_job,
        "apply_async",
        lambda args, **options: messages.append((list(args), options)),
    )
    return messages


@pytest.fixture
def now_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run on_commit callbacks at once (tests roll their transaction back)."""
    monkeypatch.setattr(
        "apps.media.services.transaction.on_commit", lambda callback, **_kw: callback()
    )


def _ffmpeg(*args: str) -> None:
    subprocess.run(  # noqa: S603 - fixed argv
        ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", *args],  # noqa: S607
        check=True,
        timeout=120,
    )


_SOURCE = ["-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=2"]
_TONE = ["-f", "lavfi", "-i", "sine=frequency=440:duration=2"]


def direct_play_mp4(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ffmpeg(*_SOURCE, *_TONE, "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-movflags", "+faststart", "-shortest", str(path))  # fmt: skip
    return path


def hevc_mkv(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ffmpeg(*_SOURCE, *_TONE, "-c:v", "libx265", "-preset", "ultrafast",
            "-x265-params", "log-level=error", "-c:a", "aac", "-shortest", str(path))  # fmt: skip
    return path


@pytest.fixture
def library(make_library: Callable[..., Library], data_root: Path) -> Library:
    return make_library()


def matched_movie(library: Library, make: Callable[[Path], Path], name: str) -> MediaFile:
    make(Path(library.path) / name)
    movie = Movie.objects.create(title=name)
    return MediaFile.objects.create(
        library=library,
        storage_key=name,
        movie=movie,
        state=FileState.MATCHED,
        xxhash64="00112233aabbccdd",
        duration_ms=2000,
    )


def asset(file: MediaFile) -> Path:
    return services.asset_dir(file.pk.hex)


# --- Planning ---------------------------------------------------------------------------


def test_a_direct_play_source_is_served_as_is(library: Library, sent: Sent) -> None:
    file = matched_movie(library, direct_play_mp4, "Clip.mp4")

    assert services.prepare_file(file.pk) is Prepared.SOURCE

    link = asset(file) / "source.mp4"
    assert link.is_symlink()
    assert os.readlink(link) == str(Path(library.path) / "Clip.mp4")
    rendition = Rendition.objects.get(media_file=file)
    assert rendition.kind == RenditionKind.SOURCE
    assert rendition.status == RenditionStatus.READY
    assert (rendition.height, rendition.codec, rendition.container) == (240, "h264", "mp4")
    assert rendition.storage_key == file.pk.hex
    assert not TranscodeJob.objects.exists()
    assert sent == []
    movie = Movie.objects.get(files=file)
    assert movie.status == TitleStatus.READY
    title = playable_title(TitleKind.MOVIE, movie.xc_id)
    assert title is not None
    assert [(r.kind, r.entry) for r in title.renditions] == [(Kind.SOURCE, "source.mp4")]
    # Idempotent.
    assert services.prepare_file(file.pk) is Prepared.EXISTING


def test_a_moved_source_is_linked_again(library: Library, sent: Sent) -> None:
    file = matched_movie(library, direct_play_mp4, "Old.mp4")
    services.prepare_file(file.pk)
    moved = Path(library.path) / "Sub" / "New.mp4"
    moved.parent.mkdir()
    (Path(library.path) / "Old.mp4").rename(moved)
    file.storage_key = "Sub/New.mp4"
    file.save()

    assert services.prepare_file(file.pk) is Prepared.EXISTING
    assert os.readlink(asset(file) / "source.mp4") == str(moved)


def test_passthrough_serves_any_source(library: Library, sent: Sent) -> None:
    library.processing_policy = ProcessingPolicy.PASSTHROUGH
    library.save()
    file = matched_movie(library, hevc_mkv, "Raw.mkv")

    assert services.prepare_file(file.pk) is Prepared.SOURCE
    assert (asset(file) / "source.mkv").is_symlink()
    assert Rendition.objects.get(media_file=file).container == "mkv"


def test_unplannable_or_unmatched_files(library: Library, sent: Sent) -> None:
    broken = matched_movie(library, direct_play_mp4, "Broken.mp4")
    (Path(library.path) / "Broken.mp4").write_bytes(b"not a video")
    assert services.prepare_file(broken.pk) is Prepared.ERROR

    pending = MediaFile.objects.create(library=library, storage_key="new.mkv")
    assert services.prepare_file(pending.pk) is Prepared.SKIPPED
    assert services.prepare_file(uuid4()) is Prepared.SKIPPED


def test_an_hevc_source_queues_a_compat_job_on_the_best_live_backend(
    library: Library, sent: Sent, now_commit: None
) -> None:
    file = matched_movie(library, hevc_mkv, "Film.mkv")
    state_redis().set(
        "worker:caps:gpu-box",
        json.dumps({"host": "gpu-box", "backends": {"nvenc": ["h264"], "cpu": ["h264"]}}),
    )

    assert services.prepare_file(file.pk) is Prepared.QUEUED

    job = TranscodeJob.objects.get(media_file=file)
    assert (job.status, job.backend, job.remux, job.priority) == ("queued", "nvenc", False, 5)
    assert job.rendition is not None
    assert job.rendition.status == RenditionStatus.PENDING
    assert sent == [([str(job.pk), 1], {"queue": "transcode.nvenc", "priority": 4})]
    assert Movie.objects.get(files=file).status != TitleStatus.READY
    # A second plan finds the queued job.
    assert services.prepare_file(file.pk) is Prepared.EXISTING
    assert TranscodeJob.objects.count() == 1


def test_on_demand_libraries_wait_for_the_first_play(
    library: Library, sent: Sent, now_commit: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    library.processing_policy = ProcessingPolicy.ON_DEMAND
    library.save()
    file = matched_movie(library, hevc_mkv, "Later.mkv")
    assert services.prepare_file(file.pk) is Prepared.ON_DEMAND
    assert not TranscodeJob.objects.exists()

    planned: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        tasks.prepare_media_file, "delay", lambda fid, urgent=False: planned.append((fid, urgent))
    )
    movie = Movie.objects.get(files=file)
    on_demand = services.request_on_demand(TitleKind.MOVIE, movie.pk)
    assert on_demand == services.OnDemand(preparing=True, eta_s=2 + 30)
    assert planned == [(str(file.pk), True)]

    assert services.prepare_file(file.pk, urgent=True) is Prepared.QUEUED
    job = TranscodeJob.objects.get(media_file=file)
    assert job.priority == PRIORITY_URGENT
    assert services.request_on_demand(TitleKind.MOVIE, uuid4()) == services.OnDemand(False, None)


def test_request_on_demand_hurries_a_queued_job(
    library: Library, sent: Sent, now_commit: None
) -> None:
    file = matched_movie(library, hevc_mkv, "Queued.mkv")
    services.prepare_file(file.pk)
    job = TranscodeJob.objects.get(media_file=file)

    result = services.request_on_demand(TitleKind.MOVIE, Movie.objects.get(files=file).pk)

    job.refresh_from_db()
    assert result.preparing is True
    assert job.priority == PRIORITY_URGENT
    assert job.dispatches == 2  # re-sent with the new priority
    assert sent[-1] == ([str(job.pk), 2], {"queue": "transcode.cpu", "priority": 1})
    # An urgent re-plan also hurries it (already urgent here: unchanged).
    assert services.prepare_file(file.pk, urgent=True) is Prepared.EXISTING


def test_a_changed_source_drops_its_old_renditions(library: Library, sent: Sent) -> None:
    file = matched_movie(library, direct_play_mp4, "Changed.mp4")
    services.prepare_file(file.pk)
    file.xxhash64 = "ffffffffffffffff"
    file.save()

    assert services.prepare_file(file.pk) is Prepared.SOURCE
    assert Rendition.objects.get(media_file=file).source_hash == "ffffffffffffffff"


# --- Running jobs -----------------------------------------------------------------------


def queued_job(library: Library, name: str = "Encode.mkv") -> TranscodeJob:
    file = matched_movie(library, hevc_mkv, name)
    services.prepare_file(file.pk)
    return TranscodeJob.objects.select_related("rendition").get(media_file=file)


def test_a_job_encodes_verifies_and_publishes_the_compat_mp4(
    library: Library, sent: Sent, now_commit: None
) -> None:
    job = queued_job(library)
    feed = state_redis().pubsub()
    feed.subscribe(services.CHANNEL)

    assert services.run_job(job.pk, job.dispatches, host="t1") is JobStatus.DONE

    job.refresh_from_db()
    assert (job.status, job.progress, job.attempts, job.worker_host) == ("done", 100.0, 1, "t1")
    assert job.encoder == "libx264"
    assert job.finished_at is not None
    rendition = Rendition.objects.get(jobs=job)
    assert rendition.status == RenditionStatus.READY
    assert (rendition.codec, rendition.height, rendition.encoder_used) == ("h264", 240, "libx264")
    assert rendition.duration_s == pytest.approx(2.0, abs=0.3)
    output = asset(job.media_file) / "compat.mp4"
    assert output.is_file()
    assert rendition.size == output.stat().st_size
    assert not any(p.name.startswith(".tmp") for p in asset(job.media_file).iterdir())
    movie = Movie.objects.get(files=job.media_file)
    assert movie.status == TitleStatus.READY
    title = playable_title(TitleKind.MOVIE, movie.xc_id)
    assert title is not None
    assert [r.entry for r in title.renditions] == ["compat.mp4"]
    statuses = []
    for _ in range(50):  # the first read is the subscribe confirmation (None)
        message = feed.get_message(ignore_subscribe_messages=True, timeout=0.05)
        if message is not None:
            statuses.append(json.loads(message["data"])["status"])
    feed.close()
    assert statuses[0] == "running"
    assert statuses[-1] == "done"
    assert not state_redis().exists(services.LEASE_KEY.format(job=job.pk))


def test_stale_and_duplicate_messages_are_dropped(
    library: Library, sent: Sent, now_commit: None
) -> None:
    job = queued_job(library)
    assert services.run_job(job.pk, job.dispatches + 1) is None  # an older dispatch
    assert services.run_job(uuid4(), 1) is None
    state_redis().set(services.LEASE_KEY.format(job=job.pk), "other")
    assert services.run_job(job.pk, job.dispatches) is None  # another worker has it
    job.refresh_from_db()
    assert job.status == JobStatus.QUEUED


def test_failures_retry_on_cpu_with_backoff_then_fail(
    library: Library, sent: Sent, now_commit: None, monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    settings.TRANSCODE_MAX_ATTEMPTS = 2
    settings.TRANSCODE_RETRY_BACKOFF_S = 30
    job = queued_job(library)
    job.backend = "nvenc"
    job.save()

    def broken(*_args: Any, **_kwargs: Any) -> None:
        raise FfmpegError(1, ["Error while opening encoder", "Conversion failed!"])

    monkeypatch.setattr(services, "run", broken)

    assert services.run_job(job.pk, job.dispatches) is JobStatus.QUEUED
    job.refresh_from_db()
    assert (job.status, job.attempts, job.backend, job.error) == ("queued", 1, "cpu", "ffmpeg")
    assert sent[-1] == ([str(job.pk), job.dispatches], {
        "queue": "transcode.cpu", "priority": 4, "countdown": 30,
    })  # fmt: skip

    assert services.run_job(job.pk, job.dispatches) is JobStatus.FAILED
    job.refresh_from_db()
    assert (job.status, job.attempts) == ("failed", 2)
    assert job.error_tail == "Error while opening encoder\nConversion failed!"
    assert Rendition.objects.get(jobs=job).status == RenditionStatus.FAILED
    assert Movie.objects.get(files=job.media_file).status != TitleStatus.READY


def test_verification_and_source_problems_fail_the_attempt(
    library: Library, sent: Sent, now_commit: None, monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    settings.TRANSCODE_MAX_ATTEMPTS = 1
    job = queued_job(library)
    monkeypatch.setattr(services, "run", lambda *_a, **_k: None)  # writes nothing
    assert services.run_job(job.pk, job.dispatches) is JobStatus.FAILED
    job.refresh_from_db()
    assert job.error == "verify:missing_output"

    other = queued_job(library, "Gone.mkv")
    (Path(library.path) / "Gone.mkv").unlink()
    assert services.run_job(other.pk, other.dispatches) is JobStatus.FAILED
    other.refresh_from_db()
    assert other.error == "prepare"
    assert "Gone.mkv" not in other.error_tail or "/" not in other.error_tail


def test_a_removed_source_cancels_its_job(library: Library, sent: Sent, now_commit: None) -> None:
    job = queued_job(library)
    MediaFile.objects.filter(pk=job.media_file_id).update(removed_at=timezone.now())
    assert services.run_job(job.pk, job.dispatches) is JobStatus.CANCELLED
    job.refresh_from_db()
    assert job.error == "source_removed"


def test_progress_reaches_the_row_the_feed_and_the_cancel_flag(
    library: Library, sent: Sent, now_commit: None
) -> None:
    job = queued_job(library)
    job.status = JobStatus.RUNNING
    job.save()
    reporter = services._Reporter(job)
    state_redis().set(services.CANCEL_KEY.format(job=job.pk), "1")
    update = ProgressUpdate(
        out_time_ms=1000, frame=25, fps=50.0, speed=2.0, bitrate_kbps=900.0,
        total_size=1000, done=False, percent=50.0, eta_s=1,
    )  # fmt: skip

    reporter(update)

    assert reporter.cancel.is_set()
    job.refresh_from_db()
    assert (job.progress, job.fps, job.speed, job.eta_s) == (50.0, 50.0, 2.0, 1)


def test_a_cancelled_run_stays_cancelled(
    library: Library, sent: Sent, now_commit: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from apps.media.ffmpeg import FfmpegCancelled  # noqa: PLC0415

    job = queued_job(library)

    def cancelled(*_args: Any, **_kwargs: Any) -> None:
        raise FfmpegCancelled("cancelled")

    monkeypatch.setattr(services, "run", cancelled)
    assert services.run_job(job.pk, job.dispatches) is JobStatus.CANCELLED
    assert Rendition.objects.get(jobs=job).status == RenditionStatus.FAILED


# --- Admin actions ----------------------------------------------------------------------


def test_cancel_retry_and_priority(
    library: Library, sent: Sent, now_commit: None, owner: Any
) -> None:
    job = queued_job(library)

    services.set_priority(job, 9, actor=owner, ip="192.0.2.1")
    job.refresh_from_db()
    assert (job.priority, job.dispatches) == (9, 2)
    assert sent[-1][1]["priority"] == 0

    services.cancel_job(job, actor=owner, ip=None)
    job.refresh_from_db()
    assert job.status == JobStatus.CANCELLED
    with pytest.raises(ProblemError) as conflict:
        services.cancel_job(job, actor=owner, ip=None)
    assert conflict.value.status_code == 409
    with pytest.raises(ProblemError):
        services.set_priority(job, 3, actor=owner, ip=None)

    services.retry_job(job, actor=owner, ip=None)
    job.refresh_from_db()
    assert (job.status, job.attempts, job.error) == ("queued", 0, "")
    assert Rendition.objects.get(jobs=job).status == RenditionStatus.PENDING
    with pytest.raises(ProblemError):
        services.retry_job(job, actor=owner, ip=None)
    with pytest.raises(ProblemError) as invalid:
        services.set_priority(job, 10, actor=owner, ip=None)
    assert invalid.value.status_code == 400

    actions = list(
        AuditLog.objects.filter(target_id=str(job.pk))
        .order_by("at", "id")
        .values_list("action", flat=True)
    )
    assert actions == ["transcode_job.priority", "transcode_job.cancel", "transcode_job.retry"]


def test_cancelling_a_running_job_flags_its_transcoder(
    library: Library, sent: Sent, now_commit: None
) -> None:
    job = queued_job(library)
    job.status = JobStatus.RUNNING
    job.save()
    services.cancel_job(job, actor=None, ip=None)
    assert state_redis().exists(services.CANCEL_KEY.format(job=job.pk))


def test_retry_refuses_a_second_active_job(library: Library, sent: Sent, now_commit: None) -> None:
    job = queued_job(library)
    services.cancel_job(job, actor=None, ip=None)
    services.prepare_file(job.media_file_id)  # a new job for the same file
    with pytest.raises(ProblemError) as conflict:
        services.retry_job(job, actor=None, ip=None)
    assert conflict.value.status_code == 409


# --- Reconciliation, receivers, the transcoder worker -----------------------------------


def test_reconcile_plans_missed_files_and_recovers_dead_jobs(
    library: Library, sent: Sent, now_commit: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    missed = matched_movie(library, hevc_mkv, "Missed.mkv")
    dead = queued_job(library, "Dead.mkv")
    planned: list[str] = []
    monkeypatch.setattr(tasks.prepare_media_file, "delay", lambda fid, **_k: planned.append(fid))
    TranscodeJob.objects.filter(pk=dead.pk).update(
        status=JobStatus.RUNNING,
        attempts=1,
        updated_at=timezone.now() - timedelta(hours=1),
    )

    assert tasks.reconcile_media() == 2
    assert planned == [str(missed.pk)]
    dead.refresh_from_db()
    assert (dead.status, dead.error) == ("queued", "worker_lost")


def test_matched_files_are_planned_after_the_commit(
    library: Library, monkeypatch: pytest.MonkeyPatch, django_capture_on_commit_callbacks: Any
) -> None:
    planned: list[str] = []
    monkeypatch.setattr(tasks.prepare_media_file, "delay", planned.append)
    with django_capture_on_commit_callbacks(execute=True):
        file = MediaFile.objects.create(library=library, storage_key="a.mkv")
        file.state = FileState.MATCHED
        file.save()
    assert planned == [str(file.pk)]

    def down(_file_id: str) -> None:
        raise ConnectionError("broker down")

    monkeypatch.setattr(tasks.prepare_media_file, "delay", down)
    receivers._enqueue(str(file.pk))


def test_episode_files_and_tasks(library: Library, sent: Sent, now_commit: None) -> None:
    series = Series.objects.create(title="Show")
    season = Season.objects.create(series=series, number=1)
    episode = Episode.objects.create(season=season, number=1)
    direct_play_mp4(Path(library.path) / "Show.S01E01.mp4")
    file = MediaFile.objects.create(
        library=library, storage_key="Show.S01E01.mp4", state=FileState.MATCHED
    )
    file.episodes.add(episode)

    assert tasks.prepare_media_file(str(file.pk)) == "source"
    series.refresh_from_db()
    assert series.status == TitleStatus.READY
    job = queued_job(library)
    assert tasks.run_transcode_job(str(job.pk), job.dispatches) == "done"
    assert tasks.run_transcode_job(str(job.pk), job.dispatches) is None
    assert services.request_on_demand(TitleKind.EPISODE, episode.pk).preparing is True


def test_the_transcoder_registers_its_capabilities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    caps = tmp_path / "caps.json"
    monkeypatch.setenv("TRANSCODER_CAPS_FILE", str(caps))
    assert worker.register_once() is None  # nothing detected yet
    caps.write_text(json.dumps({"host": "box", "backends": {"cpu": ["h264"]},
                                "devices": {"vaapi": "/dev/dri/renderD128"}}))  # fmt: skip

    assert worker.register_once() == "worker:caps:box"
    assert services.live_capabilities()[0]["host"] == "box"
    assert services.device_for("vaapi") == "/dev/dri/renderD128"
    assert services.device_for("cpu") is None

    worker.remove_caps()
    assert services.live_capabilities() == []
    assert services.route(remux=True) == "cpu"
