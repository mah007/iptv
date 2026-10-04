"""The media pipeline's services (SPEC §7.3; ADR-0010, ADR-0014): plan a matched file's
outputs, run its transcode jobs, and the admin's job actions.

Flow for one matched file (`prepare_file`, on the worker):

1. probe the source, plan it with P3's planner and sync its audio and subtitle tracks
   (embedded streams and sidecar files);
2. a source that plays everywhere (or any source in a `passthrough` library) gets a
   `source` rendition at once: a symlink `renditions/<key>/source.<ext>` to the library
   file, which the edge serves read-only. No job;
3. otherwise, in an `ingest` library, a compat MP4 `TranscodeJob` is queued on the
   best live backend's queue (`transcode.<backend>`; remuxes always go to CPU).
   `on_demand` libraries wait for the first play (`request_on_demand`);
4. the other outputs are queued beside it, each its own job with its own priority:
   subtitles (whenever a text subtitle waits for conversion), thumbnails (once a
   playable rendition exists), and in `ingest` libraries the HLS ladder and the UHD
   version (each behind its `library.*` setting).

`run_job` (on a transcoder) takes a lease, hands the job to its runner in
`apps.media.outputs` (ffmpeg with progress, verification, an atomic move into place),
then finishes it. Failures are retried with backoff (hardware backends fall back to
CPU) up to `conf.max_attempts()`, keeping ffmpeg's last 50 stderr lines in `error_tail`.
"""

import contextlib
import json
import os
import shutil
import socket
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, cast
from uuid import UUID

import structlog
from django.db import IntegrityError, transaction
from django.db.models import QuerySet
from django.utils import timezone

from apps.accounts.models import User
from apps.audit import services as audit
from apps.catalog.models import FileState, MediaFile
from apps.catalog.services import refresh_status_of_files
from apps.core.errors import ErrorCode, ProblemError
from apps.core.services import get_setting
from apps.core.stores import state_redis
from apps.media import conf, hwdetect, layout, outputs, subtitles
from apps.media.ffmpeg import FfmpegCancelled, FfmpegError
from apps.media.layout import asset_dir, asset_key, source_path
from apps.media.models import (
    ACTIVE_JOB_STATUSES,
    PLAYABLE_KINDS,
    PRIORITY_DEFAULT,
    PRIORITY_MAX,
    PRIORITY_MIN,
    PRIORITY_URGENT,
    PROFILE_PRIORITY,
    AudioTrack,
    JobStatus,
    Rendition,
    RenditionKind,
    RenditionStatus,
    SubtitleStatus,
    SubtitleTrack,
    TranscodeBackend,
    TranscodeJob,
    TranscodeProfile,
)
from apps.media.planner import (
    OnDemandAction,
    PlanError,
    ProcessingPlan,
    ProcessingPolicy,
    UhdMode,
    UhdPlan,
    decide_on_demand,
    plan_processing,
)
from apps.media.probe import ProbeError, ProbeResult, probe
from apps.media.profiles import Backend, ProfileError, Profiles, VideoCodec, default_profiles
from apps.media.progress import (
    DB_PUBLISH_INTERVAL_S,
    SSE_PUBLISH_INTERVAL_S,
    ProgressUpdate,
    Throttle,
)
from apps.media.verify import VerificationError
from apps.playback import tokens
from apps.playback.models import TitleKind

__all__ = ["asset_dir", "asset_key", "source_path"]

logger = structlog.get_logger(__name__)

#: Redis channel of the admin's transcode feed (`transcode-jobs/stream`).
CHANNEL: Final = "admin.transcode"
LEASE_KEY: Final = "transcode:lease:{job}"
CANCEL_KEY: Final = "transcode:cancel:{job}"
CANCEL_TTL_S: Final = 3600
#: The compat MP4 never exceeds 1080p (profiles.yaml's box), whatever the plan allows.
COMPAT_MAX_QUALITY: Final = outputs.SDR_MAX_QUALITY
COMPAT_NAME: Final = "compat.mp4"
ERROR_MAX: Final = 500
#: Rough speeds for ETAs before a job reports its own (media seconds per second).
ESTIMATED_SPEED: Final = {True: 30.0, False: 1.0}  # remux, encode
#: The rendition row each profile produces: (kind, name).
PROFILE_RENDITION: Final[dict[str, tuple[RenditionKind, str]]] = {
    TranscodeProfile.COMPAT_MP4: (RenditionKind.COMPAT_MP4, layout.COMPAT),
    TranscodeProfile.HLS: (RenditionKind.HLS_MASTER, layout.HLS),
    TranscodeProfile.UHD: (RenditionKind.UHD, layout.UHD),
    TranscodeProfile.THUMBNAILS: (RenditionKind.THUMBNAILS, layout.THUMBS),
}
#: Error of a UHD version that needs an HEVC encode no allowed transcoder can do.
NO_HEVC_ENCODER: Final = "no_hevc_encoder"


class Prepared(StrEnum):
    """What `prepare_file` did about the file's playable rendition."""

    SKIPPED = "skipped"  # unknown, removed or unmatched file
    EXISTING = "existing"  # already playable, or a job is already queued
    SOURCE = "source"  # the source is served as is
    QUEUED = "queued"  # a compat MP4 job was queued
    ON_DEMAND = "on_demand"  # waits for the first play
    ERROR = "error"  # unreadable or unplannable source (logged on the file)


# --- Routing --------------------------------------------------------------------------


def live_capabilities() -> list[dict[str, Any]]:
    """The `worker:caps:*` documents of the transcoders alive now."""
    client = state_redis()
    keys = list(client.scan_iter(match=hwdetect.CAPS_KEY_TEMPLATE.format(host="*"), count=100))
    if not keys:
        return []
    documents = []
    for raw in cast(list[bytes | None], client.mget(keys)):
        if raw is None:
            continue
        with contextlib.suppress(ValueError):
            documents.append(json.loads(raw))
    return documents


def _best(codec: VideoCodec, profiles: Profiles | None = None) -> str:
    profiles = profiles or default_profiles()
    live = hwdetect.live_backends(live_capabilities())
    return hwdetect.best_backend(live, {codec}, profiles.backend_preference).value


def route(remux: bool, profiles: Profiles | None = None) -> str:
    """The backend a compat job runs on: CPU for remuxes, else the best live H.264 encoder."""
    if remux:
        return TranscodeBackend.CPU
    return _best(VideoCodec.H264, profiles)


def route_profile(profile: str, *, remux: bool = False) -> str:
    """The backend a job of `profile` runs on. Encodes go to the best live backend for
    their codec (H.264 for compat and the ladder, HEVC for UHD); copies, thumbnails and
    subtitles always to CPU."""
    if profile == TranscodeProfile.COMPAT_MP4:
        return route(remux)
    if profile == TranscodeProfile.HLS:
        return _best(VideoCodec.H264)
    if profile == TranscodeProfile.UHD and not remux:
        return _best(VideoCodec.HEVC)
    return TranscodeBackend.CPU


def queue_for(backend: str, profiles: Profiles | None = None) -> str:
    profiles = profiles or default_profiles()
    return profiles.backend(Backend(backend)).queue


def celery_priority(priority: int) -> int:
    """Our 0-9 (higher first) as Celery's Redis priority (0 first)."""
    return PRIORITY_MAX - max(PRIORITY_MIN, min(PRIORITY_MAX, priority))


def _job_rendition(job: TranscodeJob) -> QuerySet[Rendition]:
    if job.rendition_id is None:
        return Rendition.objects.none()
    return Rendition.objects.filter(pk=job.rendition_id)


def dispatch(job: TranscodeJob, *, countdown: int | None = None) -> None:
    """(Re-)send the job's Celery message after the commit. Earlier messages go stale."""
    from apps.media import tasks  # noqa: PLC0415 (tasks import this module)

    job.dispatches += 1
    job.save(update_fields=["dispatches", "updated_at"])
    args = (str(job.pk), job.dispatches)
    options: dict[str, Any] = {
        "queue": queue_for(job.backend),
        "priority": celery_priority(job.priority),
    }
    if countdown:
        options["countdown"] = countdown
    transaction.on_commit(lambda: tasks.run_transcode_job.apply_async(args, **options))


# --- Planning a file ------------------------------------------------------------------


def _playable(file: MediaFile) -> bool:
    return Rendition.objects.filter(
        media_file=file, kind__in=PLAYABLE_KINDS, status=RenditionStatus.READY
    ).exists()


def _drop_stale(file: MediaFile) -> None:
    """Renditions and probed tracks of an earlier version of the file are useless now
    (sidecar and uploaded subtitles do not depend on the video, so they stay). The
    files on disk go with the next cleanup (`apps.media.cleanup`)."""
    stale = Rendition.objects.filter(media_file=file).exclude(source_hash=file.xxhash64)
    if stale.exists():
        TranscodeJob.objects.filter(media_file=file, status=JobStatus.QUEUED).update(
            status=JobStatus.CANCELLED, error="source_changed", finished_at=timezone.now()
        )
        stale.delete()
        AudioTrack.objects.filter(media_file=file).delete()
        SubtitleTrack.objects.filter(media_file=file, stream_index__isnull=False).delete()
        refresh_status_of_files([file.pk])


def _relink_moved_source(file: MediaFile) -> None:
    """A moved file keeps its hash and renditions; point its links (`source.<ext>`, a
    kept UHD source's `uhd.<ext>`) at the new path."""
    target = str(source_path(file))
    rows = Rendition.objects.filter(media_file=file, kind__in=(RenditionKind.SOURCE,
                                                               RenditionKind.UHD))  # fmt: skip
    for row in rows:
        stem = layout.SOURCE if row.kind == RenditionKind.SOURCE else layout.UHD
        link = asset_dir(row.storage_key) / f"{stem}.{row.container}"
        if not link.is_symlink():
            continue
        if os.readlink(link) != target:
            layout.link(target, link)


def prepare_file(
    file_id: UUID | str,
    *,
    urgent: bool = False,
    probe_fn: Callable[[Path], ProbeResult] = probe,
) -> Prepared:
    """Plan a matched file's outputs (see the module docstring). Idempotent.

    `urgent`: a viewer is waiting (on-demand fallback); the job runs before ingest work.
    """
    file = MediaFile.objects.select_related("library").filter(pk=file_id).first()
    if file is None or file.removed_at is not None or file.state != FileState.MATCHED:
        return Prepared.SKIPPED
    _drop_stale(file)
    _relink_moved_source(file)
    playable = _playable(file)
    active = (
        None
        if playable
        else TranscodeJob.objects.filter(
            media_file=file, profile=TranscodeProfile.COMPAT_MP4, status__in=ACTIVE_JOB_STATUSES
        ).first()
    )
    if active is not None and urgent and active.priority < PRIORITY_URGENT:
        _reprioritize(active, PRIORITY_URGENT)
    try:
        profiles = default_profiles()
        source = source_path(file)
        result = probe_fn(source)
        policy = ProcessingPolicy(file.library.processing_policy)
        plan = plan_processing(
            result, policy=policy, max_quality=COMPAT_MAX_QUALITY, profiles=profiles
        )
    except (ProbeError, PlanError, ProfileError, ValueError, OSError) as exc:
        logger.warning("media.prepare_failed", file=str(file.pk), error=str(exc)[:200])
        return Prepared.ERROR
    sidecars = subtitles.find_sidecars(source, Path(file.library.path))
    subtitles.sync_tracks(file, result, sidecars)
    outcome = Prepared.EXISTING
    if not playable and active is None:
        outcome = _prepare_playable(file, result, plan, policy, urgent=urgent)
    queue_outputs(file, result, plan, profiles=profiles)
    return outcome


def _prepare_playable(
    file: MediaFile,
    result: ProbeResult,
    plan: ProcessingPlan,
    policy: ProcessingPolicy,
    *,
    urgent: bool,
) -> Prepared:
    if plan.direct_playable or policy is ProcessingPolicy.PASSTHROUGH:
        return Prepared.SOURCE if publish_source(file, result) else Prepared.ERROR
    if policy is ProcessingPolicy.ON_DEMAND and not urgent:
        return Prepared.ON_DEMAND
    priority = PRIORITY_DEFAULT
    if urgent:
        decision = decide_on_demand(
            plan,
            compat_ready=False,
            realtime_enabled=bool(get_setting("playback.realtime_transcode_enabled")),
            realtime_active=0,
            realtime_max=int(cast(int, get_setting("playback.realtime_transcode_max"))),
        )
        # Real-time HLS is not built: every waiting viewer gets an urgent compat job.
        if decision.urgent or decision.action is OnDemandAction.REALTIME:
            priority = PRIORITY_URGENT
    job = ensure_job(file, plan, priority=priority)
    return Prepared.QUEUED if job is not None else Prepared.EXISTING


def queue_outputs(
    file: MediaFile,
    result: ProbeResult,
    plan: ProcessingPlan,
    *,
    profiles: Profiles | None = None,
) -> list[TranscodeJob]:
    """Queue the outputs beside the playable one that are missing (see the module
    docstring). Failed outputs of the current source are not queued again: an admin
    reprocesses them."""
    queued = []
    pending = SubtitleTrack.objects.filter(media_file=file, status=SubtitleStatus.PENDING)
    if pending.exists():
        queued.append(ensure_output(file, TranscodeProfile.SUBTITLES))
    if _playable(file):
        queued.append(ensure_output(file, TranscodeProfile.THUMBNAILS))
    if plan.policy is ProcessingPolicy.INGEST:
        if plan.hls is not None and get_setting("library.hls_enabled") is True:
            queued.append(ensure_output(file, TranscodeProfile.HLS))
        if get_setting("library.uhd_enabled") is True:
            uhd = _uhd_plan(result, plan.policy, profiles or default_profiles())
            if uhd is not None:
                queued.append(_ensure_uhd(file, uhd))
    return [job for job in queued if job is not None]


def queue_followups(file: MediaFile) -> list[TranscodeJob]:
    """Outputs that wait for a playable rendition (thumbnails read the compat MP4)."""
    if not _playable(file):
        return []
    job = ensure_output(file, TranscodeProfile.THUMBNAILS)
    return [job] if job is not None else []


def _uhd_plan(result: ProbeResult, policy: ProcessingPolicy, profiles: Profiles) -> UhdPlan | None:
    try:
        return plan_processing(
            result, policy=policy, max_quality=outputs.UHD_MAX_QUALITY, profiles=profiles
        ).uhd
    except PlanError:
        return None


def _ensure_uhd(file: MediaFile, uhd: UhdPlan, *, force: bool = False) -> TranscodeJob | None:
    keep = uhd.mode is UhdMode.KEEP_SOURCE
    backend = route_profile(TranscodeProfile.UHD, remux=keep)
    if (
        not keep
        and backend == TranscodeBackend.CPU
        and get_setting("library.uhd_cpu_encode") is not True
    ):
        _unavailable(file, TranscodeProfile.UHD, NO_HEVC_ENCODER)
        return None
    return ensure_output(file, TranscodeProfile.UHD, remux=keep, backend=backend, force=force)


def _unavailable(file: MediaFile, profile: str, error: str) -> None:
    """Record why an output is not made (shown by the admin) without queueing a job."""
    kind, name = PROFILE_RENDITION[profile]
    Rendition.objects.update_or_create(
        media_file=file,
        kind=kind,
        defaults={
            "name": name,
            "storage_key": asset_key(file),
            "status": RenditionStatus.FAILED,
            "error": error,
            "source_hash": file.xxhash64,
        },
    )


def _source_extension(file: MediaFile) -> str | None:
    ext = Path(file.storage_key).suffix.lstrip(".").lower()
    return ext if tokens.EXTENSION.fullmatch(ext) else None


def publish_source(file: MediaFile, result: ProbeResult) -> bool:
    """Serve the library file as is: `renditions/<key>/source.<ext>` -> the file."""
    ext = _source_extension(file)
    if ext is None:
        logger.warning("media.source_extension", file=str(file.pk))
        return False
    key = asset_key(file)
    directory = asset_dir(key)
    layout.link(str(source_path(file)), directory / f"{layout.SOURCE}.{ext}")
    video = result.video
    Rendition.objects.update_or_create(
        media_file=file,
        kind=RenditionKind.SOURCE,
        defaults={
            "name": layout.SOURCE,
            "storage_key": key,
            "container": ext,
            "width": video.width if video else None,
            "height": video.height if video else None,
            "codec": video.codec if video else "",
            "bitrate": result.bitrate,
            "size": result.size or file.size,
            "duration_s": result.duration_ms / 1000 if result.duration_ms else None,
            "status": RenditionStatus.READY,
            "encoder_used": "",
            "error": "",
            "ready_at": timezone.now(),
            "source_hash": file.xxhash64,
            "details": {"linked": True},
        },
    )
    refresh_status_of_files([file.pk])
    return True


def ensure_job(
    file: MediaFile, plan: ProcessingPlan, *, priority: int = PRIORITY_DEFAULT
) -> TranscodeJob | None:
    """Queue the compat MP4 job unless it is ready or already queued; returns the new job."""
    compat = plan.compat_mp4
    if compat is None:
        return None
    return ensure_output(
        file, TranscodeProfile.COMPAT_MP4, priority=priority, remux=compat.is_remux
    )


def ensure_output(  # noqa: PLR0913
    file: MediaFile,
    profile: str,
    *,
    priority: int | None = None,
    remux: bool = False,
    backend: str | None = None,
    force: bool = False,
) -> TranscodeJob | None:
    """Queue a job for one output unless it is ready (or failed for this source, unless
    `force`) or already queued or running; returns the new job."""
    try:
        with transaction.atomic():
            rendition = None
            if profile in PROFILE_RENDITION:
                kind, name = PROFILE_RENDITION[profile]
                lookup: dict[str, Any] = {"media_file": file, "kind": kind}
                if kind in (RenditionKind.HLS_MASTER, RenditionKind.HLS_VARIANT):
                    lookup["name"] = name
                rendition, created = Rendition.objects.select_for_update().get_or_create(
                    **lookup,
                    defaults={
                        "name": name,
                        "storage_key": asset_key(file),
                        "container": "mp4",
                        "source_hash": file.xxhash64,
                    },
                )
                if rendition.status == RenditionStatus.READY and not force:
                    return None
                failed_now = (
                    rendition.status == RenditionStatus.FAILED
                    and rendition.source_hash == file.xxhash64
                )
                if failed_now and not created and not force:
                    return None
                if TranscodeJob.objects.filter(
                    media_file=file, profile=profile, status__in=ACTIVE_JOB_STATUSES
                ).exists():
                    return None
                if rendition.status != RenditionStatus.READY:
                    rendition.status = RenditionStatus.PENDING
                rendition.error = ""
                rendition.source_hash = file.xxhash64
                rendition.save(update_fields=["status", "error", "source_hash", "updated_at"])
            job = TranscodeJob.objects.create(
                media_file=file,
                rendition=rendition,
                profile=profile,
                remux=remux,
                backend=backend or route_profile(profile, remux=remux),
                priority=PROFILE_PRIORITY.get(profile, PRIORITY_DEFAULT)
                if priority is None
                else priority,
            )
            dispatch(job)
    except IntegrityError:
        return None  # another worker queued it first
    logger.info(
        "media.job_queued",
        job=str(job.pk),
        profile=profile,
        backend=job.backend,
        remux=job.remux,
        priority=job.priority,
    )
    publish(job)
    return job


# --- Running a job (transcoder) -------------------------------------------------------


def host_name() -> str:
    return socket.gethostname()


def _local_caps() -> dict[str, Any]:
    path = conf.caps_file()
    if path is None:
        return {}
    try:
        return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {}


def device_for(backend: str) -> str | None:
    """The DRM render node this host's vaapi/qsv encoder uses (from `run_transcoder`)."""
    devices = _local_caps().get("devices") or {}
    value = devices.get(backend)
    return value if isinstance(value, str) else None


class _Reporter:
    """ffmpeg progress -> the admin feed (1 s), the job row (5 s), the lease, cancels."""

    def __init__(self, job: TranscodeJob) -> None:
        self.job = job
        self.cancel = threading.Event()
        self.sse = Throttle(SSE_PUBLISH_INTERVAL_S)
        self.db = Throttle(DB_PUBLISH_INTERVAL_S)

    def __call__(self, update: ProgressUpdate) -> None:
        job = self.job
        if update.percent is not None:
            job.progress = round(min(100.0, max(0.0, update.percent)), 1)
        job.fps = update.fps
        job.speed = update.speed
        job.eta_s = update.eta_s
        if self.sse.ready():
            client = state_redis()
            client.expire(LEASE_KEY.format(job=job.pk), conf.lease_ttl_s())
            if client.exists(CANCEL_KEY.format(job=job.pk)):
                self.cancel.set()
            publish(job)
        if self.db.ready():
            TranscodeJob.objects.filter(pk=job.pk, status=JobStatus.RUNNING).update(
                progress=job.progress,
                fps=job.fps,
                speed=job.speed,
                eta_s=job.eta_s,
                updated_at=timezone.now(),
            )


def run_job(
    job_id: UUID | str,
    dispatch_no: int,
    *,
    host: str | None = None,
    probe_fn: Callable[[Path], ProbeResult] = probe,
) -> JobStatus | None:
    """Run one queued job to its end (done, retried, failed or cancelled).

    Returns None for a stale or duplicate message (an older dispatch, a job that is no
    longer queued, or one whose lease another worker holds).
    """
    host = host or host_name()
    job = (
        TranscodeJob.objects.select_related("media_file__library", "media_file__movie", "rendition")
        .filter(pk=job_id)
        .first()
    )
    if job is None or job.dispatches != dispatch_no or job.status != JobStatus.QUEUED:
        return None
    client = state_redis()
    lease = LEASE_KEY.format(job=job.pk)
    if not client.set(lease, host, nx=True, ex=conf.lease_ttl_s()):
        return None
    file = job.media_file
    workdir = asset_dir(job.rendition.storage_key if job.rendition else asset_key(file))
    temporary = workdir / f".tmp-{job.pk.hex}"
    try:
        if file.removed_at is not None:
            _finish(job, JobStatus.CANCELLED, error="source_removed")
            return JobStatus.CANCELLED
        _start(job, host)
        return _execute(job, file, workdir, temporary, probe_fn)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
        client.delete(lease)


def _start(job: TranscodeJob, host: str) -> None:
    job.status = JobStatus.RUNNING
    job.attempts += 1
    job.worker_host = host[:128]
    job.started_at = timezone.now()
    job.finished_at = None
    job.progress = 0.0
    job.fps = job.speed = None
    job.eta_s = None
    job.error = ""
    job.error_tail = ""
    job.save()
    # A ready output being made again keeps serving until the new one replaces it.
    _job_rendition(job).exclude(status=RenditionStatus.READY).update(
        status=RenditionStatus.RUNNING, error="", updated_at=timezone.now()
    )
    publish(job)


def _execute(
    job: TranscodeJob,
    file: MediaFile,
    workdir: Path,
    temporary: Path,
    probe_fn: Callable[[Path], ProbeResult],
) -> JobStatus:
    reporter = _Reporter(job)
    try:
        source = source_path(file)
        result = probe_fn(source)
        temporary.mkdir(parents=True, exist_ok=True)
        context = outputs.JobContext(
            job=job,
            file=file,
            source=source,
            probe=result,
            profiles=default_profiles(),
            workdir=workdir,
            temporary=temporary,
            device=device_for(job.backend),
            on_progress=reporter,
            cancel=reporter.cancel,
            timeout_s=conf.job_timeout_s(),
        )
        outputs.RUNNERS[job.profile](context)
    except FfmpegCancelled:
        _finish(job, JobStatus.CANCELLED, error="cancelled")
        return JobStatus.CANCELLED
    except FfmpegError as exc:
        code = "timeout" if exc.timed_out else "ffmpeg"
        return _failed(job, code, "\n".join(exc.stderr_tail))
    except VerificationError as exc:
        return _failed(job, "verify:" + ",".join(exc.problems), "")
    except (ProbeError, PlanError, ProfileError, ValueError, OSError) as exc:
        code = "source_missing" if isinstance(exc, FileNotFoundError) else "prepare"
        return _failed(job, code, _redact(str(exc), file)[:ERROR_MAX])
    job.progress = 100.0
    job.eta_s = 0
    _finish(job, JobStatus.DONE)
    logger.info(
        "media.job_done",
        job=str(job.pk),
        profile=job.profile,
        encoder=job.encoder,
        attempts=job.attempts,
    )
    if job.profile == TranscodeProfile.COMPAT_MP4:
        queue_followups(file)
    return JobStatus.DONE


def _redact(text: str, file: MediaFile) -> str:
    """Error text without storage paths (OSError messages name the file)."""
    for path, placeholder in (
        (str(source_path(file)), "<source>"),
        (str(conf.renditions_root()), "<renditions>"),
        (str(file.library.path), "<library>"),
    ):
        text = text.replace(path, placeholder)
    return text


def _failed(job: TranscodeJob, code: str, tail: str) -> JobStatus:
    job.error = code[:ERROR_MAX]
    job.error_tail = tail
    if TranscodeJob.objects.filter(pk=job.pk, status=JobStatus.CANCELLED).exists():
        _finish(job, JobStatus.CANCELLED, error=job.error)
        return JobStatus.CANCELLED
    if job.attempts < conf.max_attempts():
        # A hardware encoder that failed once gets no second chance: retry on CPU.
        job.backend = TranscodeBackend.CPU
        job.status = JobStatus.QUEUED
        job.save()
        delay = conf.retry_backoff_s() * 2 ** (job.attempts - 1)
        dispatch(job, countdown=delay)
        logger.warning("media.job_retry", job=str(job.pk), error=job.error, delay_s=delay)
        publish(job)
        return JobStatus.QUEUED
    _finish(job, JobStatus.FAILED, error=job.error)
    logger.error("media.job_failed", job=str(job.pk), error=job.error, attempts=job.attempts)
    return JobStatus.FAILED


def _finish(job: TranscodeJob, status: JobStatus, *, error: str | None = None) -> None:
    job.status = status
    job.finished_at = timezone.now()
    if error is not None:
        job.error = error[:ERROR_MAX]
    job.save()
    if status in (JobStatus.FAILED, JobStatus.CANCELLED):
        _job_rendition(job).exclude(status=RenditionStatus.READY).update(
            status=RenditionStatus.FAILED, error=job.error, updated_at=timezone.now()
        )
        if job.profile == TranscodeProfile.SUBTITLES:
            SubtitleTrack.objects.filter(
                media_file_id=job.media_file_id, status=SubtitleStatus.PENDING
            ).update(status=SubtitleStatus.FAILED, error=job.error[:200], updated_at=timezone.now())
    publish(job)


# --- The admin feed -------------------------------------------------------------------


def event(job: TranscodeJob) -> dict[str, Any]:
    """A job's live fields, as the admin feed sends them."""
    return {
        "id": str(job.pk),
        "status": job.status,
        "profile": job.profile,
        "progress": job.progress,
        "fps": job.fps,
        "speed": job.speed,
        "eta_s": job.eta_s,
        "backend": job.backend,
        "encoder": job.encoder,
        "priority": job.priority,
        "attempts": job.attempts,
        "worker_host": job.worker_host,
        "error": job.error,
    }


def publish(job: TranscodeJob) -> None:
    """Send the job's live fields to the admin feed (best effort)."""
    payload = json.dumps(event(job), separators=(",", ":"))
    try:
        state_redis().publish(CHANNEL, payload)
    except Exception:
        logger.warning("media.publish_failed", job=str(job.pk))


# --- Admin actions --------------------------------------------------------------------


def _conflict(detail: str) -> ProblemError:
    return ProblemError(ErrorCode.CONFLICT, detail, status=409)


def _state(job: TranscodeJob) -> dict[str, Any]:
    return {"status": job.status, "priority": job.priority, "backend": job.backend}


def cancel_job(job: TranscodeJob, *, actor: User | None, ip: str | None) -> TranscodeJob:
    """Stop a queued or running job. A running ffmpeg is killed within a second."""
    with transaction.atomic():
        job = TranscodeJob.objects.select_for_update().get(pk=job.pk)
        if job.status not in ACTIVE_JOB_STATUSES:
            raise _conflict("Only a queued or running job can be cancelled.")
        before = _state(job)
        running = job.status == JobStatus.RUNNING
        _finish(job, JobStatus.CANCELLED, error="cancelled")
        audit.record(
            "transcode_job.cancel", actor=actor, target=job, before=before, after=_state(job), ip=ip
        )
    if running:
        state_redis().set(CANCEL_KEY.format(job=job.pk), "1", ex=CANCEL_TTL_S)
    return job


def retry_job(job: TranscodeJob, *, actor: User | None, ip: str | None) -> TranscodeJob:
    """Queue a failed or cancelled job again, with fresh attempts."""
    try:
        with transaction.atomic():
            job = TranscodeJob.objects.select_for_update().get(pk=job.pk)
            if job.status not in (JobStatus.FAILED, JobStatus.CANCELLED):
                raise _conflict("Only a failed or cancelled job can be retried.")
            before = _state(job)
            job.status = JobStatus.QUEUED
            job.attempts = 0
            job.progress = 0.0
            job.fps = job.speed = None
            job.eta_s = None
            job.error = ""
            job.error_tail = ""
            job.finished_at = None
            job.backend = route_profile(job.profile, remux=job.remux)
            job.save()
            _job_rendition(job).exclude(status=RenditionStatus.READY).update(
                status=RenditionStatus.PENDING, error="", updated_at=timezone.now()
            )
            if job.profile == TranscodeProfile.SUBTITLES:
                SubtitleTrack.objects.filter(
                    media_file_id=job.media_file_id, status=SubtitleStatus.FAILED
                ).update(status=SubtitleStatus.PENDING, error="", updated_at=timezone.now())
            dispatch(job)
            audit.record(
                "transcode_job.retry",
                actor=actor,
                target=job,
                before=before,
                after=_state(job),
                ip=ip,
            )
    except IntegrityError:
        raise _conflict("Another job for this file is already queued or running.") from None
    state_redis().delete(CANCEL_KEY.format(job=job.pk))
    publish(job)
    return job


def _reprioritize(job: TranscodeJob, priority: int) -> None:
    job.priority = priority
    job.save(update_fields=["priority", "updated_at"])
    if job.status == JobStatus.QUEUED:
        dispatch(job)  # the earlier message goes stale; this one carries the new priority
    publish(job)


def set_priority(
    job: TranscodeJob, priority: int, *, actor: User | None, ip: str | None
) -> TranscodeJob:
    """Change a queued (or running) job's priority, 0-9, higher first."""
    if not PRIORITY_MIN <= priority <= PRIORITY_MAX:
        raise ProblemError(ErrorCode.VALIDATION_ERROR, "Priority is 0 to 9.", status=400)
    with transaction.atomic():
        job = TranscodeJob.objects.select_for_update().get(pk=job.pk)
        if job.status not in ACTIVE_JOB_STATUSES:
            raise _conflict("Only a queued or running job can change priority.")
        before = _state(job)
        _reprioritize(job, priority)
        audit.record(
            "transcode_job.priority",
            actor=actor,
            target=job,
            before=before,
            after=_state(job),
            ip=ip,
        )
    return job


def reprocess(
    file: MediaFile,
    profiles: Iterable[str],
    *,
    probe_fn: Callable[[Path], ProbeResult] = probe,
) -> dict[str, str]:
    """Make outputs again (the admin's Reprocess). Ready outputs are replaced when the
    new one is ready; a running job for an output is left alone. Returns, per profile,
    `queued`, `active` (a job already runs) or why it was not queued."""
    result_by_profile: dict[str, str] = {}
    wanted = list(dict.fromkeys(profiles))
    try:
        result = probe_fn(source_path(file))
        policy = ProcessingPolicy(file.library.processing_policy)
        plan = plan_processing(
            result, policy=policy, max_quality=COMPAT_MAX_QUALITY, profiles=default_profiles()
        )
    except (ProbeError, PlanError, ProfileError, ValueError, OSError):
        return dict.fromkeys(wanted, "unreadable_source")
    for profile in wanted:
        if TranscodeJob.objects.filter(
            media_file=file, profile=profile, status__in=ACTIVE_JOB_STATUSES
        ).exists():
            result_by_profile[profile] = "active"
            continue
        job: TranscodeJob | None
        if profile == TranscodeProfile.SUBTITLES:
            sidecars = subtitles.find_sidecars(source_path(file), Path(file.library.path))
            subtitles.sync_tracks(file, result, sidecars)
            SubtitleTrack.objects.filter(media_file=file).exclude(
                status=SubtitleStatus.UNSUPPORTED
            ).update(status=SubtitleStatus.PENDING, error="", updated_at=timezone.now())
            job = ensure_output(file, profile, force=True)
        elif profile == TranscodeProfile.UHD:
            uhd = _uhd_plan(result, ProcessingPolicy.INGEST, default_profiles())
            if uhd is None:
                result_by_profile[profile] = "not_uhd"
                continue
            job = _ensure_uhd(file, uhd, force=True)
            if job is None:
                result_by_profile[profile] = NO_HEVC_ENCODER
                continue
        elif profile == TranscodeProfile.COMPAT_MP4:
            compat = plan.compat_mp4
            if compat is None:
                result_by_profile[profile] = "passthrough"
                continue
            job = ensure_output(file, profile, remux=compat.is_remux, force=True)
        elif profile == TranscodeProfile.HLS and plan.hls is None:
            result_by_profile[profile] = "passthrough"
            continue
        else:
            job = ensure_output(file, profile, force=True)
        result_by_profile[profile] = "queued" if job is not None else "active"
    return result_by_profile


# --- On-demand fallback and reconciliation --------------------------------------------


@dataclass(frozen=True, slots=True)
class OnDemand:
    """A title that is not playable yet: whether work is under way, and a rough ETA."""

    preparing: bool
    eta_s: int | None


def _title_files(kind: TitleKind, title_id: UUID) -> list[MediaFile]:
    files = MediaFile.objects.filter(removed_at__isnull=True, state=FileState.MATCHED)
    if kind == TitleKind.MOVIE:
        files = files.filter(movie_id=title_id)
    else:
        files = files.filter(episodes__id=title_id)
    return list(files.order_by("-is_primary", "created_at"))


def estimate_eta_s(file: MediaFile, job: TranscodeJob | None) -> int | None:
    if job is not None and job.status == JobStatus.RUNNING and job.eta_s is not None:
        return job.eta_s
    if not file.duration_ms:
        return None
    remux = bool(job.remux) if job is not None else False
    return int(file.duration_ms / 1000 / ESTIMATED_SPEED[remux]) + 30


def request_on_demand(kind: TitleKind, title_id: UUID) -> OnDemand:
    """SPEC §7.3's on-demand fallback, for a play request that got TITLE_PREPARING:
    make sure an urgent compat job (a remux when only the container or audio is wrong)
    is on its way, and estimate when the title will play."""
    from apps.media import tasks  # noqa: PLC0415 (tasks import this module)

    files = _title_files(kind, title_id)
    if not files:
        return OnDemand(preparing=False, eta_s=None)
    file = files[0]
    job = (
        TranscodeJob.objects.filter(
            media_file=file, profile=TranscodeProfile.COMPAT_MP4, status__in=ACTIVE_JOB_STATUSES
        )
        .order_by("-created_at")
        .first()
    )
    if job is not None and job.priority < PRIORITY_URGENT:
        _reprioritize(job, PRIORITY_URGENT)
    elif job is None:
        file_id = str(file.pk)
        transaction.on_commit(lambda: tasks.prepare_media_file.delay(file_id, urgent=True))
    return OnDemand(preparing=True, eta_s=estimate_eta_s(file, job))


def files_to_prepare(limit: int = 200) -> list[UUID]:
    """Matched files with no rendition row yet in libraries that produce at ingest."""
    rows = (
        MediaFile.objects.filter(
            state=FileState.MATCHED,
            removed_at__isnull=True,
            library__processing_policy__in=(
                ProcessingPolicy.INGEST,
                ProcessingPolicy.PASSTHROUGH,
            ),
        )
        .exclude(renditions__kind__in=PLAYABLE_KINDS)
        .exclude(transcode_jobs__status__in=ACTIVE_JOB_STATUSES)
        .order_by("created_at")
        .values_list("pk", flat=True)[:limit]
    )
    return list(rows)


def recover_stale_jobs() -> int:
    """Running jobs whose transcoder died (no lease) go back to the queue."""
    cutoff = timezone.now() - timedelta(seconds=2 * conf.lease_ttl_s())
    client = state_redis()
    recovered = 0
    for job in TranscodeJob.objects.filter(status=JobStatus.RUNNING, updated_at__lt=cutoff):
        if client.exists(LEASE_KEY.format(job=job.pk)):
            continue
        with transaction.atomic():
            locked = TranscodeJob.objects.select_for_update().get(pk=job.pk)
            if locked.status != JobStatus.RUNNING:
                continue
            if locked.attempts >= conf.max_attempts():
                _finish(locked, JobStatus.FAILED, error="worker_lost")
            else:
                locked.status = JobStatus.QUEUED
                locked.error = "worker_lost"
                locked.save()
                dispatch(locked)
        recovered += 1
    return recovered
