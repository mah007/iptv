"""Libraries and the reconciliation scan (SPEC §7.1, docs/plans/poc.md slice 2).

- Admin changes to libraries are audited in their transaction.
- `request_scan` queues one scan per library at a time; the Celery task runs `run_scan`.
- `run_scan` holds a Redis lock per library, walks the folder (or one path the watcher
  reported) and diffs it against the catalogue by path, size, mtime and xxHash64:
  new files are created and sent down the ingest pipeline, changed ones re-probed, moved
  ones (same fingerprint, old path gone) keep their metadata under the new path, and
  missing ones are soft-removed (their titles are hidden once no file is left).
- Progress goes to the ScanJob row and to the Redis channel `admin.scan.<library_id>`,
  which the admin's server-sent event stream relays.
"""

import contextlib
import json
import logging
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Final
from uuid import UUID

from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone
from redis.exceptions import LockError, RedisError
from redis.lock import Lock

from apps.accounts.models import User
from apps.audit import services as audit
from apps.catalog.models import FileState, MediaFile, Movie, Series
from apps.catalog.services import refresh_status_of_files
from apps.catalog.signals import notify_catalog_changed
from apps.core.errors import ErrorCode, ProblemError
from apps.core.stores import state_redis
from apps.library import storage
from apps.library.fingerprint import fingerprint_path
from apps.library.models import ACTIVE_SCAN_STATUSES, Library, ScanJob, ScanStatus, ScanTrigger
from apps.library.parsing import parse_path

logger = logging.getLogger(__name__)

SCAN_LOCK_TTL_S: Final = 300
#: Progress is written to the database and published at most this often.
PROGRESS_INTERVAL_S: Final = 1.0
LOG_MAX_LINES: Final = 200
#: A queued job older than this is presumed lost (worker restarted) and replaced.
STALE_QUEUED_AFTER: Final = timedelta(minutes=30)


def scan_channel(library_id: UUID | str) -> str:
    """Redis pub/sub channel of a library's scan progress (SPEC §7.1)."""
    return f"admin.scan.{library_id}"


def scan_lock_key(library_id: UUID | str) -> str:
    return f"lock:scan:{library_id}"


class ScanBusyError(Exception):
    """Another scan of the library holds the lock."""


# --- Libraries (admin) ----------------------------------------------------------------------------


def _library_state(library: Library) -> dict[str, Any]:
    return {
        "name": library.name,
        "kind": library.kind,
        "path": library.path,
        "processing_policy": library.processing_policy,
        "scan_interval_min": library.scan_interval_min,
        "enabled": library.enabled,
        "default_categories": sorted(str(pk) for pk in _category_ids(library)),
    }


def _category_ids(library: Library) -> list[UUID]:
    if library.pk is None:
        return []
    return list(library.default_categories.values_list("pk", flat=True))


def _check_path(path: str, *, exclude: UUID | None = None) -> str:
    try:
        resolved = storage.validate_library_path(path)
    except Exception as exc:  # django ValidationError: one message
        message = getattr(exc, "messages", [str(exc)])[0]
        raise ProblemError(
            ErrorCode.VALIDATION_ERROR, message, field_errors={"path": [message]}
        ) from None
    libraries = Library.objects.exclude(pk=exclude) if exclude else Library.objects.all()
    others = libraries.values_list("name", "path")
    for name, other in others:
        if storage.overlaps(resolved, other):
            message = f"Overlaps the library {name} ({other})."
            raise ProblemError(ErrorCode.CONFLICT, message, field_errors={"path": [message]})
    return resolved


def create_library(data: Mapping[str, Any], *, actor: User | None, ip: str | None) -> Library:
    values = dict(data)
    categories = values.pop("default_categories", [])
    values["path"] = _check_path(values["path"])
    with transaction.atomic():
        if Library.objects.filter(name=values["name"]).exists():
            raise ProblemError(
                ErrorCode.CONFLICT,
                "A library with this name exists.",
                field_errors={"name": ["Already used by another library."]},
            )
        library = Library.objects.create(**values)
        library.default_categories.set(categories)
        audit.record(
            "library.create", actor=actor, target=library, after=_library_state(library), ip=ip
        )
    return library


def update_library(
    library: Library, data: Mapping[str, Any], *, actor: User | None, ip: str | None
) -> Library:
    values = dict(data)
    categories = values.pop("default_categories", None)
    if "path" in values:
        values["path"] = _check_path(values["path"], exclude=library.pk)
    before = _library_state(library)
    with transaction.atomic():
        if (
            "name" in values
            and Library.objects.filter(name=values["name"]).exclude(pk=library.pk).exists()
        ):
            raise ProblemError(
                ErrorCode.CONFLICT,
                "A library with this name exists.",
                field_errors={"name": ["Already used by another library."]},
            )
        for name, value in values.items():
            setattr(library, name, value)
        library.save()
        if categories is not None:
            library.default_categories.set(categories)
        audit.record(
            "library.update",
            actor=actor,
            target=library,
            before=before,
            after=_library_state(library),
            ip=ip,
        )
    return library


def delete_library(library: Library, *, actor: User | None, ip: str | None) -> None:
    """Delete a library and its file records; the titles stay, hidden if no file is left."""
    with transaction.atomic():
        file_ids = list(MediaFile.objects.filter(library=library).values_list("pk", flat=True))
        movie_ids = set(Movie.objects.filter(files__library=library).values_list("pk", flat=True))
        series_ids = set(
            Series.objects.filter(seasons__episodes__files__library=library).values_list(
                "pk", flat=True
            )
        )
        audit.record(
            "library.delete",
            actor=actor,
            target=library,
            before={**_library_state(library), "files": len(file_ids)},
            ip=ip,
        )
        library.delete()
        from apps.catalog.services import (  # noqa: PLC0415
            refresh_movie_status,
            refresh_series_status,
        )

        for movie in Movie.objects.filter(pk__in=movie_ids):
            refresh_movie_status(movie)
        for series in Series.objects.filter(pk__in=series_ids):
            refresh_series_status(series)
        # Episodes and files vanished even where a title's status stayed.
        if movie_ids:
            notify_catalog_changed("movie", movie_ids)
        if series_ids:
            notify_catalog_changed("series", series_ids)


def library_stats(library: Library) -> dict[str, int]:
    """Totals for the library card: files, bytes, titles, files in review or in error."""
    files = MediaFile.objects.filter(library=library, removed_at__isnull=True)
    totals = files.aggregate(
        files=Count("pk"),
        bytes=Sum("size"),
        review=Count("pk", filter=Q(state=FileState.REVIEW)),
        errors=Count("pk", filter=Q(state=FileState.ERROR)),
        pending=Count("pk", filter=Q(state__in=(FileState.PENDING, FileState.MATCHING))),
    )
    return {
        "files": totals["files"] or 0,
        "bytes": totals["bytes"] or 0,
        "movies": Movie.objects.filter(files__in=files).distinct().count(),
        "series": Series.objects.filter(seasons__episodes__files__in=files).distinct().count(),
        "episodes": files.filter(episodes__isnull=False).values("episodes").distinct().count(),
        "review": totals["review"] or 0,
        "errors": totals["errors"] or 0,
        "pending": totals["pending"] or 0,
    }


# --- Starting scans -------------------------------------------------------------------------------


def request_scan(
    library: Library,
    *,
    trigger: str = ScanTrigger.MANUAL,
    path: str = "",
    actor: User | None = None,
    ip: str | None = None,
) -> tuple[ScanJob, bool]:
    """Queue a scan, or return the full scan already queued or running.

    Returns `(job, created)`. Watcher scans of one path always get their own job.
    """
    from apps.library import tasks  # noqa: PLC0415 (tasks import this module)

    with transaction.atomic():
        Library.objects.select_for_update().filter(pk=library.pk).first()
        if not path:
            active = (
                ScanJob.objects.filter(library=library, path="", status__in=ACTIVE_SCAN_STATUSES)
                .order_by("-created_at")
                .first()
            )
            stale = (
                active is not None
                and active.status == ScanStatus.QUEUED
                and active.created_at < timezone.now() - STALE_QUEUED_AFTER
            )
            if active is not None and not stale:
                return active, False
            if active is not None:
                _finish(active, ScanStatus.FAILED, "Never started; replaced by a new scan.")
        job = ScanJob.objects.create(library=library, trigger=trigger, path=path)
        if trigger == ScanTrigger.MANUAL:
            audit.record(
                "library.scan", actor=actor, target=library, after={"job": str(job.pk)}, ip=ip
            )
        transaction.on_commit(lambda: tasks.scan_library.delay(str(job.pk)))
    publish(job)
    return job, True


def due_libraries(now: datetime | None = None) -> list[Library]:
    """Enabled libraries whose last scan is older than their interval and that have no
    full scan queued or running."""
    now = now or timezone.now()
    busy = ScanJob.objects.filter(path="", status__in=ACTIVE_SCAN_STATUSES).values("library_id")
    due = []
    for library in Library.objects.filter(enabled=True).exclude(pk__in=busy):
        interval = timedelta(minutes=library.scan_interval_min)
        if library.last_scan_at is None or library.last_scan_at + interval <= now:
            due.append(library)
    return due


# --- Progress -------------------------------------------------------------------------------------


def job_event(job: ScanJob) -> dict[str, Any]:
    """What subscribers receive: the job's state, JSON-serialisable."""
    return {
        "id": str(job.pk),
        "library_id": str(job.library_id),
        "trigger": job.trigger,
        "status": job.status,
        "path": job.path,
        "found": job.found,
        "new": job.new,
        "changed": job.changed,
        "moved": job.moved,
        "removed": job.removed,
        "errors": job.errors,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


def publish(job: ScanJob) -> None:
    """Announce the job's state on its library's channel; never fails the scan."""
    try:
        state_redis().publish(scan_channel(job.library_id), json.dumps(job_event(job)))
    except RedisError:
        logger.warning("scan progress could not be published", extra={"scan": str(job.pk)})


def _finish(job: ScanJob, status: str, message: str = "") -> None:
    job.status = status
    job.finished_at = timezone.now()
    if message:
        _log(job, message)
    job.save()
    publish(job)


def _log(job: ScanJob, message: str) -> None:
    lines = [*job.log.splitlines(), message][-LOG_MAX_LINES:]
    job.log = "\n".join(lines)


# --- The scan -------------------------------------------------------------------------------------


@dataclass
class _Progress:
    job: ScanJob
    lock: Lock | None
    last: float = field(default_factory=time.monotonic)

    def tick(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self.last < PROGRESS_INTERVAL_S:
            return
        self.last = now
        if self.lock is not None:
            try:
                self.lock.extend(SCAN_LOCK_TTL_S, replace_ttl=True)
            except LockError:
                logger.warning("scan lock lost", extra={"scan": str(self.job.pk)})
        self.job.save()
        publish(self.job)


def run_scan(job: ScanJob, *, enqueue: Callable[[UUID], None] | None = None) -> ScanJob:
    """Run a queued scan under the library's lock. `enqueue(file_id)` sends a new or
    changed file down the ingest pipeline (default: the Celery task, after commit).

    Raises `ScanBusyError` when another scan holds the lock (the task retries later).
    """
    library = job.library
    lock = state_redis().lock(scan_lock_key(library.pk), timeout=SCAN_LOCK_TTL_S)
    if not lock.acquire(blocking=False):
        raise ScanBusyError(str(library.pk))
    try:
        job.status = ScanStatus.RUNNING
        job.started_at = timezone.now()
        job.save()
        publish(job)
        if enqueue is None:
            enqueue = _enqueue_processing
        try:
            _diff(job, library, _Progress(job, lock), enqueue)
        except Exception as exc:
            logger.exception("scan failed", extra={"scan": str(job.pk)})
            _finish(job, ScanStatus.FAILED, f"Scan failed: {type(exc).__name__}")
            return job
        if not job.path:
            library.last_scan_at = job.finished_at or timezone.now()
        library.stats = library_stats(library)
        library.save(update_fields=["last_scan_at", "stats", "updated_at"])
        _finish(job, ScanStatus.DONE)
        return job
    finally:
        with contextlib.suppress(LockError):
            lock.release()


def _enqueue_processing(file_id: UUID) -> None:
    from apps.library import tasks  # noqa: PLC0415

    transaction.on_commit(lambda: tasks.process_media_file.delay(str(file_id)))


def _diff(
    job: ScanJob, library: Library, progress: _Progress, enqueue: Callable[[UUID], None]
) -> None:
    prefix = job.path.strip("/")
    rows = MediaFile.objects.filter(library=library)
    if prefix:
        rows = rows.filter(Q(storage_key=prefix) | Q(storage_key__startswith=f"{prefix}/"))
    existing = {row.storage_key: row for row in rows}
    seen: set[str] = set()
    fresh: list[storage.FoundFile] = []
    touched: list[UUID] = []
    for found in storage.walk(library.path, prefix):
        seen.add(found.key)
        job.found += 1
        row = existing.get(found.key)
        if row is None:
            fresh.append(found)
        else:
            _reconcile(job, library, row, found, enqueue=enqueue, touched=touched)
        progress.tick()
    gone = [row for key, row in existing.items() if key not in seen and row.removed_at is None]
    for found in fresh:
        _add(job, library, found, enqueue, touched)
        progress.tick()
    # Whatever is still unseen after moves were matched has been removed.
    now = timezone.now()
    for row in gone:
        row.refresh_from_db(fields=["storage_key", "removed_at"])
        if row.removed_at is not None or row.storage_key in seen:
            continue
        row.removed_at = now
        row.save(update_fields=["removed_at", "updated_at"])
        job.removed += 1
        _log(job, f"removed: {row.storage_key}")
        touched.append(row.pk)
    refresh_status_of_files(touched)
    progress.tick(force=True)


def _reconcile(  # noqa: PLR0913
    job: ScanJob,
    library: Library,
    row: MediaFile,
    found: storage.FoundFile,
    *,
    enqueue: Callable[[UUID], None],
    touched: list[UUID],
) -> None:
    unchanged = row.size == found.size and row.mtime == found.mtime
    if unchanged and row.removed_at is None:
        return
    digest = _fingerprint(job, library, found)
    if digest is None:
        return
    revived = row.removed_at is not None
    if digest == row.xxhash64 and row.size == found.size:
        row.mtime = found.mtime
        row.removed_at = None
        row.save(update_fields=["mtime", "removed_at", "updated_at"])
        if revived:
            job.new += 1
            _log(job, f"back: {found.key}")
            touched.append(row.pk)
        return
    row.size, row.mtime, row.xxhash64 = found.size, found.mtime, digest
    row.removed_at = None
    row.error = ""
    if row.state != FileState.MATCHED:
        row.state = FileState.PENDING
    row.save()
    job.changed += 1
    _log(job, f"changed: {found.key}")
    touched.append(row.pk)
    enqueue(row.pk)


def _add(
    job: ScanJob,
    library: Library,
    found: storage.FoundFile,
    enqueue: Callable[[UUID], None],
    touched: list[UUID],
) -> None:
    parsed = parse_path(found.key, library_kind=library.kind)  # type: ignore[arg-type]
    if parsed.kind == "ignore":
        return
    digest = _fingerprint(job, library, found)
    if digest is None:
        return
    moved = _move_source(library, digest, found)
    if moved is not None:
        old_key = moved.storage_key
        moved.storage_key = found.key
        moved.size, moved.mtime = found.size, found.mtime
        moved.removed_at = None
        moved.save()
        job.moved += 1
        _log(job, f"moved: {old_key} -> {found.key}")
        touched.append(moved.pk)
        return
    with transaction.atomic():
        row = MediaFile.objects.create(
            library=library,
            storage_key=found.key,
            size=found.size,
            mtime=found.mtime,
            xxhash64=digest,
            parse_result=parsed.to_json(),
            container=parsed.container or "",
        )
        enqueue(row.pk)
    job.new += 1
    _log(job, f"new: {found.key}")


def _move_source(library: Library, digest: str, found: storage.FoundFile) -> MediaFile | None:
    """A file of this library with the same fingerprint whose path no longer exists."""
    for candidate in MediaFile.objects.filter(library=library, xxhash64=digest, size=found.size):
        if candidate.storage_key == found.key:
            continue
        try:
            path = storage.library_file(library.path, candidate.storage_key)
        except ValueError:
            continue
        if not path.exists():
            return candidate
    return None


def _fingerprint(job: ScanJob, library: Library, found: storage.FoundFile) -> str | None:
    try:
        return fingerprint_path(storage.library_file(library.path, found.key))
    except OSError as exc:
        job.errors += 1
        _log(job, f"unreadable: {found.key} ({type(exc).__name__})")
        return None


def scan_summaries(jobs: Iterable[ScanJob]) -> list[dict[str, Any]]:
    return [job_event(job) for job in jobs]
