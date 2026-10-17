"""Import sources and jobs (ADR-0025/0027).

- **Sources** are created, changed and deleted by admins (audited without credentials).
  Their Test and their remote browser run rclone in the importer: the web process
  queues the request on the `import` queue and the admin polls the answer, kept for a
  few minutes in redis-cache.
- **Jobs** are queued by an admin for specific files or folders of a source, or one
  direct link, with the rights they come under (`import.start`). `dispatch` starts at
  most `library.import_max_concurrent` of them; the rest wait in order.
- **A run** (in the importer, the only service with the media root writable):
  1. plans: lists the selected items on the source (or asks the link for its size),
     keeps video and subtitle files only, refuses names already in the library, and
     refuses the job when the copy plus `library.import_reserve_gb` would not fit;
  2. copies into the job's staging folder inside the library (rclone, or the resumable
     downloader for a link), reporting bytes, speed and ETA every two seconds;
  3. moves each finished file into place atomically and queues a scan of what arrived,
     so the existing scan, match and transcode pipeline does the rest;
  4. finishes (`import.finish`). A transient failure is retried once, resuming from what
     was staged; a cancel (`import.cancel`) stops the copy and deletes the staging data.
- `sweep` (beat, every minute, in the importer) requeues jobs whose importer died,
  removes staging folders nothing uses, and dispatches.
"""

import contextlib
import json
import logging
import shutil
import socket
import time
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any, Final, cast
from uuid import UUID

import orjson
import redis
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from apps.accounts import crypto
from apps.accounts.models import User
from apps.audit import services as audit
from apps.core.ids import uuid7
from apps.core.services import get_setting
from apps.core.stores import cache_redis, state_redis
from apps.imports import direct, files, rclone, remotes, xtream
from apps.imports.models import (
    ACTIVE_JOB_STATUSES,
    FileKind,
    ImportFile,
    ImportJob,
    ImportSource,
    JobOrigin,
    JobPhase,
    JobStatus,
    SourceKind,
    TestStatus,
)
from apps.library.models import Library
from apps.library.storage import library_file
from apps.live import egress

logger = logging.getLogger(__name__)

#: A job is tried at most this often: the first run and one retry (transient errors).
MAX_ATTEMPTS: Final = 2
RETRY_AFTER: Final = timedelta(seconds=60)
LEASE_TTL_S: Final = 120
LEASE_KEY: Final = "lease:import:{job}"
DISPATCH_LOCK: Final = "lock:imports:dispatch"
#: A dispatched job no importer claimed within this time is dispatched again.
STALE_DISPATCH: Final = timedelta(minutes=10)
HEARTBEAT_KEY: Final = "hb:importer"
HEARTBEAT_TTL_S: Final = 180
REQUEST_TTL_S: Final = 600
REQUEST_PREFIX: Final = "import:request:"
PROGRESS_S: Final = 2.0
SPACE_CHECK_S: Final = 10.0
MAX_ITEMS: Final = 500
MAX_FILES: Final = 5000
BROWSE_LIMIT: Final = 2000
SCAN_PATHS_MAX: Final = 50
LOG_ITEMS: Final = 20


class ImportRefused(Exception):
    """A job (or a request to start one) that can't go ahead; `code` is stable."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        transient: bool = False,
        params: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.transient = transient
        self.params = dict(params or {})


# --- Settings -------------------------------------------------------------------------------------


def max_concurrent() -> int:
    return int(cast("int", get_setting("library.import_max_concurrent")))


def reserve_bytes() -> int:
    return int(cast("int", get_setting("library.import_reserve_gb"))) * files.GB


def bwlimit_bytes() -> int:
    """`library.import_bandwidth_mbit` in bytes per second (0: no limit)."""
    return int(cast("int", get_setting("library.import_bandwidth_mbit"))) * 125_000


def importer_online() -> bool:
    try:
        return bool(state_redis().exists(HEARTBEAT_KEY))
    except redis.RedisError:
        return False


# --- Sources ------------------------------------------------------------------------------------


def source_secrets(source: ImportSource) -> dict[str, str]:
    if not source.credentials_encrypted:
        return {}
    try:
        data = json.loads(crypto.decrypt(source.credentials_encrypted))
    except (crypto.DecryptionError, ValueError):
        return {}
    return {key: str(value) for key, value in data.items() if isinstance(value, str) and value}


def _encrypt(values: Mapping[str, str]) -> str:
    kept = {key: value for key, value in values.items() if value}
    return crypto.encrypt(json.dumps(kept)) if kept else ""


def source_state(source: ImportSource) -> dict[str, Any]:
    """What the audit log keeps of a source: no secret values, only which are set."""
    return {
        "name": source.name,
        "kind": source.kind,
        "options": dict(source.options),
        # Names only ("secret" in a key would have the audit log mask the list).
        "set": sorted(source_secrets(source)),
    }


def _refused(error: remotes.SourceError) -> ImportRefused:
    return ImportRefused(error.code, error.message, params={"field": error.field})


def create_source(  # noqa: PLR0913 (keyword-only)
    *,
    name: str,
    kind: str,
    options: Mapping[str, Any],
    secrets: Mapping[str, str],
    actor: User | None,
    ip: str | None,
) -> ImportSource:
    try:
        cleaned = remotes.clean_options(kind, options)
        given = remotes.clean_secrets(kind, secrets)
    except remotes.SourceError as exc:
        raise _refused(exc) from None
    missing = remotes.missing_secrets(kind, given)
    if missing:
        raise ImportRefused(
            "required", "This kind of source needs its credentials.", params={"field": missing[0]}
        )
    with transaction.atomic():
        if ImportSource.objects.filter(name=name).exists():
            raise ImportRefused(
                "source_name_taken", "A source with this name exists.", params={"field": "name"}
            )
        source = ImportSource.objects.create(
            name=name,
            kind=kind,
            options=cleaned,
            credentials_encrypted=_encrypt(given),
            created_by=actor if actor is not None and actor.is_authenticated else None,
        )
        audit.record(
            "import.source.create", actor=actor, target=source, after=source_state(source), ip=ip
        )
    return source


def credentials_change(
    source: ImportSource, secrets: Mapping[str, str], options: Mapping[str, Any]
) -> bool:
    """Whether a change touches credentials (it then needs a step-up, ADR-0021): any
    secret given, or a new host, endpoint or URL for the stored ones."""
    if any(value for value in secrets.values()):
        return True
    for name in ("host", "port", "endpoint", "url", "username", "host_keys"):
        if name in options and options[name] != source.options.get(name):
            return True
    return False


def update_source(  # noqa: PLR0913 (keyword-only)
    source: ImportSource,
    *,
    name: str | None,
    options: Mapping[str, Any] | None,
    secrets: Mapping[str, str],
    clear: Iterable[str] = (),
    actor: User | None,
    ip: str | None,
) -> ImportSource:
    before = source_state(source)
    try:
        merged = {**source.options, **(options or {})}
        cleaned = remotes.clean_options(source.kind, merged)
        given = remotes.clean_secrets(source.kind, secrets)
    except remotes.SourceError as exc:
        raise _refused(exc) from None
    current = source_secrets(source)
    for secret in clear:
        current.pop(secret, None)
    current.update(given)
    missing = remotes.missing_secrets(source.kind, current)
    if missing:
        raise ImportRefused(
            "required", "This kind of source needs its credentials.", params={"field": missing[0]}
        )
    with transaction.atomic():
        if name and name != source.name:
            if ImportSource.objects.filter(name=name).exclude(pk=source.pk).exists():
                raise ImportRefused(
                    "source_name_taken", "A source with this name exists.", params={"field": "name"}
                )
            source.name = name
        source.options = cleaned
        source.credentials_encrypted = _encrypt(current)
        source.save()
        after = source_state(source)
        after["changed"] = sorted({*given, *clear})
        audit.record(
            "import.source.update", actor=actor, target=source, before=before, after=after, ip=ip
        )
    return source


def delete_source(source: ImportSource, *, actor: User | None, ip: str | None) -> None:
    if ImportJob.objects.filter(source=source, status__in=ACTIVE_JOB_STATUSES).exists():
        raise ImportRefused("source_busy", "Jobs of this source are queued or running.")
    with transaction.atomic():
        audit.record(
            "import.source.delete", actor=actor, target=source, before=source_state(source), ip=ip
        )
        source.delete()


def _guard(kind: str, options: Mapping[str, Any]) -> None:
    """The full egress check (DNS included) of the hosts a source connects to."""
    for host, port in remotes.hosts(kind, options):
        try:
            egress.check_host(host, port)
        except egress.UnsafeDestination as exc:
            raise ImportRefused(TestStatus.UNSAFE, f"Not allowed: {exc.reason}.") from None


def _save_updates(source: ImportSource, updates: Mapping[str, str]) -> None:
    """Store what rclone wrote back: a refreshed token, a pinned host key, a drive."""
    if not updates:
        return
    with transaction.atomic():
        locked = ImportSource.objects.select_for_update().filter(pk=source.pk).first()
        if locked is None:
            return
        options = dict(locked.options)
        secrets = source_secrets(locked)
        if updates.get("token"):
            secrets["token"] = updates["token"]
        for name in ("host_keys", "drive_id", "drive_type"):
            if updates.get(name) and name in remotes.OPTIONS[locked.kind]:
                options[name] = updates[name]
        locked.options = options
        locked.credentials_encrypted = _encrypt(secrets)
        locked.save(update_fields=["options", "credentials_encrypted", "updated_at"])
    source.options = locked.options
    source.credentials_encrypted = locked.credentials_encrypted
    logger.info(
        "import source updated by rclone",
        extra={"source": str(source.pk), "fields": sorted(updates)},
    )


@contextlib.contextmanager
def _remote(source: ImportSource, *, pin_host_key: bool = False) -> Iterator[rclone.Session]:
    """The source as a configured rclone remote; what rclone wrote back is stored after,
    also when the run failed (a token refreshed before an error is still the new one)."""
    _guard(source.kind, source.options)
    secrets = source_secrets(source)
    current: rclone.Session | None = None
    try:
        with rclone.session(
            source.kind, source.options, secrets, pin_host_key=pin_host_key
        ) as current:
            if source.kind == SourceKind.ONEDRIVE and not (
                source.options.get("drive_id") and source.options.get("drive_type")
            ):
                # rclone writes the drive it found into this run's config.
                found = rclone.discover_onedrive(current)
                current.updates.update(found)
                current.options.update(found)
            yield current
    finally:
        if current is not None:
            _save_updates(source, current.updates)


# --- Remote requests (Test, browse) ---------------------------------------------------------------


def _request_key(request_id: str) -> str:
    return f"{REQUEST_PREFIX}{request_id}"


def queue_request(kind: str, source: ImportSource, path: str = "") -> str:
    from apps.imports import tasks  # noqa: PLC0415 (tasks import this module)

    request_id = uuid7().hex
    store_result(request_id, {"status": "pending"})
    tasks.remote_request.delay(request_id, kind, str(source.pk), path)
    return request_id


def store_result(request_id: str, result: Mapping[str, Any]) -> None:
    try:
        cache_redis().set(_request_key(request_id), orjson.dumps(dict(result)), ex=REQUEST_TTL_S)
    except redis.RedisError:
        logger.warning("import request result unstored")


def read_result(request_id: str) -> dict[str, Any] | None:
    try:
        raw = cache_redis().get(_request_key(request_id))
    except redis.RedisError:
        return None
    if not isinstance(raw, bytes):
        return None
    data = orjson.loads(raw)
    return data if isinstance(data, dict) else None


def test_source(source: ImportSource) -> dict[str, Any]:
    """List the source's root: reachable, sign-in refused, not found, timed out... An
    SFTP server's host key is pinned on the first successful Test (trust on first use)."""
    pin = source.kind == SourceKind.SFTP and not source.options.get("host_keys")
    try:
        if source.kind == SourceKind.XTREAM:
            _guard(source.kind, source.options)
            secrets = source_secrets(source)
            base_url = str(source.options["url"])
            xtream.authenticate(
                base_url, secrets.get("account_username", ""), secrets.get("password", "")
            )
            entries = xtream.categories(
                base_url, secrets.get("account_username", ""), secrets.get("password", "")
            )
        else:
            with _remote(source, pin_host_key=pin) as current:
                entries = rclone.lsjson(
                    current, remotes.remote_path(str(source.options.get("root", "")))
                )
        result: dict[str, Any] = {
            "ok": True,
            "code": TestStatus.OK,
            "message": "",
            "entries": len(entries),
        }
    except rclone.RcloneError as exc:
        result = {"ok": False, "code": exc.code, "message": exc.message}
    except ImportRefused as exc:
        result = {"ok": False, "code": exc.code, "message": exc.message}
    except xtream.XtreamError as exc:
        result = {"ok": False, "code": exc.code, "message": exc.message}
    if source.kind == SourceKind.SFTP:
        source.refresh_from_db(fields=["options"])
        result["host_keys"] = str(source.options.get("host_keys") or "")
    code = str(result["code"]) if result["code"] in TestStatus.values else TestStatus.UNEXPECTED
    ImportSource.objects.filter(pk=source.pk).update(
        last_test_at=timezone.now(),
        last_test_status=code,
        last_test_message=str(result.get("message", ""))[:300],
        updated_at=timezone.now(),
    )
    result["code"] = code
    return result


def browse(source: ImportSource, path: str) -> dict[str, Any]:
    """One folder of the source: folders first, then files with their sizes, and
    whether each file is a type an import takes."""
    try:
        relative = remotes.clean_root(path, "path")
    except remotes.SourceError as exc:
        return {"ok": False, "code": exc.code, "message": exc.message, "path": "", "entries": []}
    if source.kind == SourceKind.XTREAM:
        return _browse_xtream(source, relative)
    try:
        with _remote(source) as current:
            listing = rclone.lsjson(
                current, remotes.remote_path(str(source.options.get("root", "")), relative)
            )
    except rclone.RcloneError as exc:
        return {
            "ok": False,
            "code": exc.code,
            "message": exc.message,
            "path": relative,
            "entries": [],
        }
    except ImportRefused as exc:
        return {
            "ok": False,
            "code": exc.code,
            "message": exc.message,
            "path": relative,
            "entries": [],
        }
    entries = []
    for entry in listing:
        name = str(entry.get("Name") or "")
        if not name or name.startswith("."):
            continue
        is_dir = bool(entry.get("IsDir"))
        size = int(entry.get("Size") or 0)
        entries.append(
            {
                "name": name,
                "path": f"{relative}/{name}" if relative else name,
                "is_dir": is_dir,
                "size": None if is_dir or size < 0 else size,
                "modified": str(entry.get("ModTime") or "") or None,
                "importable": is_dir or files.file_kind(name) is not None,
            }
        )
    entries.sort(key=lambda item: (not item["is_dir"], str(item["name"]).casefold()))
    return {
        "ok": True,
        "code": TestStatus.OK,
        "message": "",
        "path": relative,
        "entries": entries[:BROWSE_LIMIT],
        "truncated": len(entries) > BROWSE_LIMIT,
    }


def _browse_xtream(source: ImportSource, relative: str) -> dict[str, Any]:
    """Categories at the top, and the movies inside one category."""
    secrets = source_secrets(source)
    base_url = str(source.options.get("url") or "")
    username, password = secrets.get("account_username", ""), secrets.get("password", "")
    try:
        _guard(source.kind, source.options)
        if not relative:
            entries = [
                {
                    "name": row["name"],
                    "path": f"category/{row['id']}",
                    "is_dir": True,
                    "size": None,
                    "modified": None,
                    # Open a category and choose individual movies. Whole-catalog
                    # acquisition is deliberately not offered.
                    "importable": False,
                }
                for row in xtream.categories(base_url, username, password)
            ]
        else:
            kind, category_id = xtream.parse_path(relative)
            if kind != "category":
                raise xtream.XtreamError("invalid_path", "Open a movie category.")
            entries = [
                {
                    "name": item.file_name,
                    "path": item.path,
                    "is_dir": False,
                    "size": None,
                    "modified": None,
                    "importable": xtream.importable(item),
                }
                for item in xtream.movies(base_url, username, password, category_id=category_id)
            ]
    except (xtream.XtreamError, ImportRefused) as exc:
        return {
            "ok": False,
            "code": exc.code,
            "message": exc.message,
            "path": relative,
            "entries": [],
        }
    entries.sort(key=lambda item: (not item["is_dir"], str(item["name"]).casefold()))
    return {
        "ok": True,
        "code": TestStatus.OK,
        "message": "",
        "path": relative,
        "entries": entries[:BROWSE_LIMIT],
        "truncated": len(entries) > BROWSE_LIMIT,
    }


def run_request(request_id: str, kind: str, source_id: str, path: str) -> dict[str, Any]:
    source = ImportSource.objects.filter(pk=source_id).first()
    if source is None:
        result: dict[str, Any] = {
            "ok": False,
            "code": "not_found",
            "message": "The source is gone.",
        }
    elif kind == "test":
        result = test_source(source)
    else:
        result = browse(source, path)
    store_result(request_id, {"status": "done", "result": result})
    return result


# --- Jobs: starting and cancelling ----------------------------------------------------------


def _actor_name(actor: User | None) -> str:
    if actor is None or not actor.is_authenticated:
        return ""
    return (actor.name or actor.email or actor.get_username())[:150]


def _target(library: Library, subfolder: str) -> str:
    if not Path(library.path).is_dir():
        raise ImportRefused(
            "library_missing", "The library's folder isn't there.", params={"field": "library"}
        )
    try:
        return files.clean_subfolder(subfolder)
    except files.PathRefused as exc:
        raise ImportRefused(exc.code, exc.message, params={"field": "subfolder"}) from None


def _rights(rights_holder: str, license_ref: str) -> tuple[str, str]:
    holder, ref = rights_holder.strip(), license_ref.strip()
    if not holder:
        raise ImportRefused(
            "required", "Say who holds the rights.", params={"field": "rights_holder"}
        )
    if not ref:
        raise ImportRefused(
            "required",
            "Give the licence reference (or the public-domain source).",
            params={"field": "license_ref"},
        )
    return holder, ref


def _start_state(job: ImportJob) -> dict[str, Any]:
    return {
        "origin": job.origin,
        "source": job.source_name,
        "source_kind": job.source_kind,
        "items": [item["path"] for item in job.items[:LOG_ITEMS]],
        "items_total": len(job.items),
        "url": job.url_display,
        "library": job.library_name,
        "subfolder": job.subfolder,
        "rights_holder": job.rights_holder,
        "license_ref": job.license_ref,
        "license_expires_at": job.license_expires_at.isoformat()
        if job.license_expires_at
        else None,
    }


def _queue(job: ImportJob, actor: User | None, ip: str | None) -> ImportJob:
    with transaction.atomic():
        job.save()
        audit.record("import.start", actor=actor, target=job, after=_start_state(job), ip=ip)
    dispatch()
    return job


def create_source_job(  # noqa: PLR0913 (everything is keyword-only)
    *,
    source: ImportSource,
    items: list[Mapping[str, Any]],
    library: Library,
    subfolder: str,
    rights_holder: str,
    license_ref: str,
    license_expires_at: datetime | None,
    actor: User | None,
    ip: str | None,
) -> ImportJob:
    if not items:
        raise ImportRefused(
            "nothing_selected", "Choose files or folders to import.", params={"field": "items"}
        )
    if len(items) > MAX_ITEMS:
        raise ImportRefused(
            "too_many_items", f"At most {MAX_ITEMS} items per import.", params={"field": "items"}
        )
    cleaned: list[dict[str, Any]] = []
    refused: list[str] = []
    for item in items:
        try:
            path = remotes.clean_root(item.get("path"), "items")
        except remotes.SourceError as exc:
            raise ImportRefused(exc.code, exc.message, params={"field": "items"}) from None
        if not path:
            raise ImportRefused(
                "invalid_path",
                "Choose files or folders inside the source.",
                params={"field": "items"},
            )
        is_dir = bool(item.get("is_dir"))
        if source.kind == SourceKind.XTREAM:
            try:
                xtream_kind, _item_id = xtream.parse_path(path)
            except xtream.XtreamError as exc:
                raise ImportRefused(exc.code, exc.message, params={"field": "items"}) from None
            if is_dir or xtream_kind != "movie":
                raise ImportRefused(
                    "unsupported_type",
                    "Open the category and choose individual movies.",
                    params={"field": "items", "names": [path.rpartition("/")[2]]},
                )
        if not is_dir and files.file_kind(path) is None:
            refused.append(path.rpartition("/")[2])
        cleaned.append({"path": path, "is_dir": is_dir})
    if refused:
        raise ImportRefused(
            "unsupported_type",
            "Only video files and subtitles can be imported.",
            params={"field": "items", "names": refused[:10]},
        )
    holder, ref = _rights(rights_holder, license_ref)
    job = ImportJob(
        origin=JobOrigin.SOURCE,
        source=source,
        source_name=source.name,
        source_kind=source.kind,
        items=list({item["path"]: item for item in cleaned}.values()),
        library=library,
        library_name=library.name,
        subfolder=_target(library, subfolder),
        rights_holder=holder,
        license_ref=ref,
        license_expires_at=license_expires_at,
        created_by=actor if actor is not None and actor.is_authenticated else None,
        created_by_name=_actor_name(actor),
    )
    return _queue(job, actor, ip)


def create_link_job(  # noqa: PLR0913 (everything is keyword-only)
    *,
    url: str,
    file_name: str,
    checksum: str,
    library: Library,
    subfolder: str,
    rights_holder: str,
    license_ref: str,
    license_expires_at: datetime | None,
    actor: User | None,
    ip: str | None,
) -> ImportJob:
    link = url.strip()
    try:
        direct.check_link(link)
    except egress.UnsafeDestination as exc:
        raise ImportRefused(
            "unsafe_destination", f"Not allowed: {exc.reason}.", params={"field": "url"}
        ) from None
    except direct.LinkRefused as exc:
        raise ImportRefused(exc.code, str(exc), params={"field": "url"}) from None
    name = ""
    if file_name.strip():
        try:
            name = files.clean_file_name(file_name)
        except files.PathRefused as exc:
            raise ImportRefused(exc.code, exc.message, params={"field": "file_name"}) from None
    else:
        # The link's own name when it is usable; else the server's, when it answers.
        with contextlib.suppress(files.PathRefused):
            name = files.clean_file_name(files.name_from_url(link))
    try:
        direct.checksum_algorithm(checksum)
    except ValueError as exc:
        raise ImportRefused("invalid_checksum", str(exc), params={"field": "checksum"}) from None
    holder, ref = _rights(rights_holder, license_ref)
    job = ImportJob(
        origin=JobOrigin.LINK,
        source_kind="link",
        url_encrypted=crypto.encrypt(link),
        url_display=direct.display(link),
        file_name=name,
        checksum=checksum.strip().lower(),
        library=library,
        library_name=library.name,
        subfolder=_target(library, subfolder),
        rights_holder=holder,
        license_ref=ref,
        license_expires_at=license_expires_at,
        created_by=actor if actor is not None and actor.is_authenticated else None,
        created_by_name=_actor_name(actor),
    )
    return _queue(job, actor, ip)


def cancel_job(job: ImportJob, *, actor: User | None, ip: str | None) -> ImportJob:
    """A queued job ends now; a running one stops within seconds (its importer deletes
    the staged data). Finished jobs can't be cancelled."""
    with transaction.atomic():
        locked = ImportJob.objects.select_for_update().get(pk=job.pk)
        if locked.status not in ACTIVE_JOB_STATUSES:
            raise ImportRefused("finished", "The import has already ended.")
        now = timezone.now()
        locked.cancel_requested = True
        if locked.status == JobStatus.QUEUED:
            locked.status = JobStatus.CANCELLED
            locked.finished_at = now
            locked.phase = ""
            locked.speed_bps = None
            locked.eta_s = None
        locked.save()
        audit.record(
            "import.cancel", actor=actor, target=locked, after={"status": locked.status}, ip=ip
        )
    dispatch()
    return locked


# --- Dispatch -------------------------------------------------------------------------------


def dispatch() -> int:
    """Start queued jobs while fewer than `library.import_max_concurrent` are active."""
    from apps.imports import tasks  # noqa: PLC0415 (tasks import this module)

    try:
        lock = state_redis().lock(DISPATCH_LOCK, timeout=30, blocking_timeout=5)
        if not lock.acquire():
            return 0
    except redis.RedisError:
        logger.warning("import dispatch skipped: redis-state unavailable")
        return 0
    try:
        with transaction.atomic():
            now = timezone.now()
            ImportJob.objects.filter(
                status=JobStatus.QUEUED, dispatched_at__lt=now - STALE_DISPATCH
            ).update(dispatched_at=None, updated_at=now)
            active = ImportJob.objects.filter(
                Q(status=JobStatus.RUNNING)
                | Q(status=JobStatus.QUEUED, dispatched_at__isnull=False)
            ).count()
            room = max_concurrent() - active
            if room <= 0:
                return 0
            ready = list(
                ImportJob.objects.select_for_update(skip_locked=True)
                .filter(status=JobStatus.QUEUED, dispatched_at__isnull=True, cancel_requested=False)
                .filter(Q(retry_at__isnull=True) | Q(retry_at__lte=now))
                .order_by("created_at")[:room]
            )
            for job in ready:
                job.dispatch_no += 1
                job.dispatched_at = now
                job.save(update_fields=["dispatch_no", "dispatched_at", "updated_at"])
                transaction.on_commit(partial(tasks.run_import.delay, str(job.pk), job.dispatch_no))
            return len(ready)
    finally:
        with contextlib.suppress(redis.RedisError):
            lock.release()


def _claim(job_id: UUID | str, dispatch_no: int) -> ImportJob | None:
    now = timezone.now()
    claimed = ImportJob.objects.filter(
        pk=job_id, status=JobStatus.QUEUED, dispatch_no=dispatch_no, cancel_requested=False
    ).update(
        status=JobStatus.RUNNING,
        phase=JobPhase.PLANNING,
        attempts=F("attempts") + 1,
        started_at=now,
        finished_at=None,
        dispatched_at=None,
        retry_at=None,
        error_code="",
        error="",
        error_params={},
        speed_bps=None,
        eta_s=None,
        updated_at=now,
    )
    if not claimed:
        return None
    _lease(job_id)
    return ImportJob.objects.select_related("library", "source").get(pk=job_id)


def _lease(job_id: UUID | str) -> None:
    with contextlib.suppress(redis.RedisError):
        state_redis().set(LEASE_KEY.format(job=job_id), socket.gethostname(), ex=LEASE_TTL_S)


def _release(job_id: UUID | str) -> None:
    with contextlib.suppress(redis.RedisError):
        state_redis().delete(LEASE_KEY.format(job=job_id))


# --- Running -------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Planned:
    """One file of a job: where it is on the source, where it lands, its size."""

    remote: str
    key: str
    size: int
    kind: FileKind


class Reporter:
    """Progress to the job row every two seconds; the cancel flag and the lease with it."""

    def __init__(self, job: ImportJob, library_path: str) -> None:
        self.job = job
        self.library_path = library_path
        self.last = 0.0
        self.space_at = time.monotonic()
        self.cancelled = False

    def update(
        self, *, done: int, total: int | None, speed: float, files_done: int | None = None
    ) -> None:
        now = time.monotonic()
        if now - self.last < PROGRESS_S:
            return
        self.last = now
        total_bytes = max(self.job.bytes_total, total or 0)
        done = min(max(done, 0), total_bytes) if total_bytes else max(done, 0)
        eta = int((total_bytes - done) / speed) if speed > 0 and total_bytes else None
        values: dict[str, Any] = {
            "bytes_done": done,
            "bytes_total": total_bytes,
            "speed_bps": int(speed),
            "eta_s": eta,
            "updated_at": timezone.now(),
        }
        if files_done is not None:
            values["files_done"] = min(files_done, self.job.files_total)
        ImportJob.objects.filter(pk=self.job.pk).update(**values)
        self.job.bytes_done, self.job.bytes_total = done, total_bytes
        self.cancelled = bool(
            ImportJob.objects.filter(pk=self.job.pk, cancel_requested=True).exists()
        )
        _lease(self.job.pk)
        if now - self.space_at >= SPACE_CHECK_S:
            self.space_at = now
            free = files.free_bytes(self.library_path)
            if free < reserve_bytes() // 2:
                raise ImportRefused(
                    "insufficient_space",
                    "The disk is filling up: the import stopped to keep the reserve free.",
                    params={"free": free, "reserve": reserve_bytes()},
                )

    def rclone_stats(self, stats: rclone.Stats) -> None:
        # rclone counts only what is left to copy; a resumed job has the rest staged.
        remaining = max(stats.total - stats.bytes, 0)
        done = self.job.bytes_total - remaining if stats.total else stats.bytes
        self.update(
            done=done, total=self.job.bytes_total, speed=stats.speed, files_done=stats.transfers
        )

    def should_stop(self) -> bool:
        return self.cancelled


def _set(job: ImportJob, **values: Any) -> None:
    for name, value in values.items():
        setattr(job, name, value)
    ImportJob.objects.filter(pk=job.pk).update(**values, updated_at=timezone.now())


def _check_space(job: ImportJob, library_path: str, needed: int) -> None:
    """Refuse a copy that, with the other running imports to the same disk and the
    reserve, wouldn't fit."""
    free = files.free_bytes(library_path)
    reserve = reserve_bytes()
    here = files.device(library_path)
    others = 0
    for other in (
        ImportJob.objects.filter(status=JobStatus.RUNNING)
        .exclude(pk=job.pk)
        .select_related("library")
    ):
        if other.library is None:
            continue
        with contextlib.suppress(OSError):
            if files.device(other.library.path) == here:
                others += max(other.bytes_total - other.bytes_done, 0)
    space = files.Space(free=free - others, needed=needed, reserve=reserve)
    if not space.fits:
        raise ImportRefused(
            "insufficient_space",
            "Not enough free space for this import and the reserve.",
            params={"needed": needed, "free": max(free - others, 0), "reserve": reserve},
        )


def _plan_source(
    job: ImportJob, current: rclone.Session, root: str
) -> tuple[list[Planned], dict[str, Any]]:
    planned: dict[str, Planned] = {}
    skipped: list[str] = []
    skipped_count = 0
    for item in job.items:
        path, is_dir = str(item["path"]), bool(item["is_dir"])
        remote = remotes.remote_path(root, path)
        if is_dir:
            entries = rclone.lsjson(current, remote, recursive=True, timeout=600)
        else:
            entries = rclone.lsjson(current, remote, stat=True)
            if entries and entries[0].get("IsDir"):
                is_dir = True
                entries = rclone.lsjson(current, remote, recursive=True, timeout=600)
        for entry in entries:
            if entry.get("IsDir"):
                continue
            inner = str(entry.get("Path") or entry.get("Name") or "")
            name = str(entry.get("Name") or inner.rpartition("/")[2])
            kind = files.file_kind(name)
            hidden = any(part.startswith(".") for part in inner.split("/"))
            if kind is None or hidden:
                if not is_dir:
                    raise ImportRefused(
                        "unsupported_type",
                        "Only video files and subtitles can be imported.",
                        params={"names": [name]},
                    )
                skipped_count += 1
                if len(skipped) < 10:
                    skipped.append(inner)
                continue
            relative = f"{path}/{inner}" if is_dir else path
            try:
                key = files.library_key(job.subfolder, path, is_dir, inner if is_dir else "")
            except files.PathRefused as exc:
                skipped_count += 1
                if len(skipped) < 10:
                    skipped.append(inner)
                logger.info("import file skipped", extra={"job": str(job.pk), "reason": exc.code})
                continue
            size = int(entry.get("Size") or 0)
            planned.setdefault(
                relative, Planned(remote=relative, key=key, size=max(size, 0), kind=kind)
            )
    if len(planned) > MAX_FILES:
        raise ImportRefused(
            "too_many_items",
            f"At most {MAX_FILES} files per import.",
            params={"count": len(planned)},
        )
    return list(planned.values()), {"count": skipped_count, "names": skipped}


def _check_targets(library_path: str, plan: list[Planned]) -> None:
    seen: set[str] = set()
    clashes: list[str] = []
    for item in plan:
        target = library_file(library_path, item.key)
        if item.key in seen or target.exists():
            clashes.append(item.key)
        seen.add(item.key)
    if clashes:
        raise ImportRefused(
            "target_exists",
            "Files with these names are already in the library.",
            params={"names": clashes[:10], "count": len(clashes)},
        )


def _copy_source(
    job: ImportJob, library: Library, staging: Path, reporter: Reporter
) -> list[tuple[Planned, Path]]:
    source = job.source
    if source is None:
        raise ImportRefused("source_missing", "The source was deleted.")
    if source.kind == SourceKind.XTREAM:
        return _copy_xtream(job, source, library, staging, reporter)
    root = str(source.options.get("root", ""))
    with _remote(source) as current:
        plan, skipped = _plan_source(job, current, root)
        if not plan:
            raise ImportRefused(
                "nothing_to_import",
                "No video or subtitle files were found.",
                params={"skipped": skipped["count"]},
            )
        _check_targets(library.path, plan)
        total = sum(item.size for item in plan)
        _check_space(job, library.path, max(total - files.staged_bytes(staging), 0))
        _set(job, files_total=len(plan), bytes_total=total, skipped=skipped, phase=JobPhase.COPYING)
        files.make_dirs(staging)
        rclone.copy(
            current,
            root=remotes.remote_path(root),
            destination=staging,
            files=[item.remote for item in plan],
            bwlimit_bytes=bwlimit_bytes(),
            on_stats=reporter.rclone_stats,
            should_stop=reporter.should_stop,
        )
    staged = []
    for item in plan:
        path = staging.joinpath(*item.remote.split("/"))
        if not path.is_file() or (item.size and path.stat().st_size != item.size):
            raise ImportRefused(
                "transfer_failed", "A file is missing or incomplete after the copy.", transient=True
            )
        staged.append((item, path))
    return staged


def _xtream_plan(
    job: ImportJob, source: ImportSource
) -> tuple[list[Planned], dict[str, str], dict[str, Any]]:
    secrets = source_secrets(source)
    base_url = str(source.options.get("url") or "")
    username, password = secrets.get("account_username", ""), secrets.get("password", "")
    listing = xtream.Listing(base_url, username, password)
    selected: dict[str, xtream.Movie] = {}
    skipped: list[str] = []
    skipped_count = 0
    try:
        _guard(source.kind, source.options)
        for raw in job.items:
            kind, item_id = xtream.parse_path(str(raw.get("path") or ""))
            if kind != "movie":
                raise xtream.XtreamError(
                    "invalid_path", "Open the category and choose individual movies."
                )
            movie = xtream.movie(base_url, username, password, item_id, listing=listing)
            if xtream.importable(movie):
                selected.setdefault(movie.stream_id, movie)
            else:
                skipped_count += 1
                if len(skipped) < 10:
                    skipped.append(movie.file_name)
            if len(selected) > MAX_FILES:
                raise ImportRefused(
                    "too_many_items",
                    f"At most {MAX_FILES} files per import.",
                    params={"count": len(selected)},
                )
    except xtream.XtreamError as exc:
        raise ImportRefused(exc.code, exc.message, transient=exc.transient) from None
    if not selected:
        raise ImportRefused(
            "nothing_to_import",
            "No supported video files were found.",
            params={"skipped": skipped_count},
        )
    plan: list[Planned] = []
    for movie in selected.values():
        url = xtream.stream_url(base_url, username, password, movie)
        try:
            info = direct.probe(url, schemes=xtream.schemes(base_url))
        except direct.DownloadError as exc:
            raise ImportRefused(exc.code, exc.message, transient=exc.transient) from None
        plan.append(
            Planned(
                remote=movie.path,
                key=files.library_key(job.subfolder, movie.file_name, False),
                size=info.size or 0,
                kind=FileKind.VIDEO,
            )
        )
    return (
        plan,
        {"username": username, "password": password},
        {"count": skipped_count, "names": skipped},
    )


def _copy_xtream(
    job: ImportJob,
    source: ImportSource,
    library: Library,
    staging: Path,
    reporter: Reporter,
) -> list[tuple[Planned, Path]]:
    plan, credentials, skipped = _xtream_plan(job, source)
    _check_targets(library.path, plan)
    total = sum(item.size for item in plan)
    _check_space(job, library.path, max(total - files.staged_bytes(staging), 0))
    _set(
        job,
        files_total=len(plan),
        bytes_total=total,
        skipped=skipped,
        phase=JobPhase.COPYING,
    )
    files.make_dirs(staging)
    base_url = str(source.options["url"])
    completed = 0
    staged: list[tuple[Planned, Path]] = []
    for index, item in enumerate(plan):
        _kind, stream_id = xtream.parse_path(item.remote)
        name = item.key.rpartition("/")[2]
        movie = xtream.Movie(stream_id, name.rsplit(".", 1)[0], files.extension(name), "")
        url = xtream.stream_url(base_url, credentials["username"], credentials["password"], movie)
        final = staging / name
        partial_file = staging / f"{name}.partial"
        if final.is_file() and (not item.size or final.stat().st_size == item.size):
            size = final.stat().st_size
        else:

            def progress(
                done: int,
                current_total: int | None,
                speed: float,
                *,
                before: int = completed,
                file_index: int = index,
            ) -> None:
                reporter.update(
                    done=before + done,
                    total=max(total, before + (current_total or done)),
                    speed=speed,
                    files_done=file_index,
                )

            try:
                size = direct.download(
                    url,
                    partial_file,
                    expected_size=item.size or None,
                    bwlimit_bytes=bwlimit_bytes(),
                    on_progress=progress,
                    should_stop=reporter.should_stop,
                    check_space=lambda: _check_download_space(library.path),
                    schemes=xtream.schemes(base_url),
                )
            except direct.DownloadError as exc:
                raise ImportRefused(exc.code, exc.message, transient=exc.transient) from None
            partial_file.rename(final)
        completed += size
        reporter.update(
            done=completed, total=max(total, completed), speed=0.0, files_done=index + 1
        )
        staged.append((Planned(remote=item.remote, key=item.key, size=size, kind=item.kind), final))
    return staged


def _check_download_space(library_path: str) -> None:
    if files.free_bytes(library_path) < reserve_bytes() // 2:
        raise ImportRefused(
            "insufficient_space",
            "The disk is filling up: the import stopped to keep the reserve free.",
        )


def _copy_link(
    job: ImportJob, library: Library, staging: Path, reporter: Reporter
) -> list[tuple[Planned, Path]]:
    try:
        url = crypto.decrypt(job.url_encrypted)
    except crypto.DecryptionError:
        raise ImportRefused(
            "unreadable_link", "The link can't be read (encryption key changed)."
        ) from None
    try:
        info = direct.probe(url)
    except direct.DownloadError as exc:
        raise ImportRefused(exc.code, exc.message, transient=exc.transient) from None
    try:
        name = files.clean_file_name(job.file_name or info.name)
    except files.PathRefused as exc:
        raise ImportRefused(
            exc.code, exc.message, params={"names": [job.file_name or info.name]}
        ) from None
    item = Planned(
        remote=job.url_display,
        key=files.library_key(job.subfolder, name, False),
        size=info.size or 0,
        kind=files.file_kind(name) or FileKind.VIDEO,
    )
    _check_targets(library.path, [item])
    partial_file = staging / f"{name}.partial"
    have = partial_file.stat().st_size if partial_file.exists() else 0
    _check_space(job, library.path, max((info.size or 0) - have, 0))
    _set(job, file_name=name, files_total=1, bytes_total=info.size or 0, phase=JobPhase.COPYING)
    files.make_dirs(staging)
    try:
        size = direct.download(
            url,
            partial_file,
            expected_size=info.size,
            bwlimit_bytes=bwlimit_bytes(),
            on_progress=lambda done, total, speed: reporter.update(
                done=done, total=total, speed=speed
            ),
            should_stop=reporter.should_stop,
            check_space=lambda: _check_download_space(library.path),
        )
        if job.checksum:
            direct.verify(partial_file, job.checksum)
    except direct.DownloadError as exc:
        if exc.code == "checksum_mismatch":
            partial_file.unlink(missing_ok=True)
        raise ImportRefused(exc.code, exc.message, transient=exc.transient) from None
    final = staging / name
    partial_file.rename(final)
    return [(Planned(remote=item.remote, key=item.key, size=size, kind=item.kind), final)]


def _move(job: ImportJob, library: Library, staged: list[tuple[Planned, Path]]) -> list[ImportFile]:
    """Each staged file into place, subtitles first; a file moved is recorded even when a
    later one fails (it is in the library, and the watcher will scan it)."""
    _set(job, phase=JobPhase.MOVING, speed_bps=None, eta_s=None)
    order = sorted(staged, key=lambda pair: (pair[0].kind != FileKind.SUBTITLE, pair[0].key))
    placed: list[ImportFile] = []
    try:
        for item, path in order:
            try:
                files.place(path, library.path, item.key)
            except files.PathRefused as exc:
                raise ImportRefused(exc.code, exc.message, params={"names": [item.key]}) from None
            placed.append(
                ImportFile(
                    job=job,
                    library=library,
                    storage_key=item.key,
                    remote_path=item.remote[:1024],
                    kind=item.kind,
                    size=item.size,
                )
            )
    finally:
        ImportFile.objects.bulk_create(placed, batch_size=500)
    return placed


def _scan(library: Library, placed: list[ImportFile]) -> None:
    """Queue a scan of each folder that received a video (the watcher would too, a
    minute later; scans are idempotent). Many folders: one scan of the library."""
    from apps.library import tasks as library_tasks  # noqa: PLC0415

    folders: dict[str, None] = {}
    for record in placed:
        if record.kind == FileKind.VIDEO:
            folders.setdefault(record.storage_key.rpartition("/")[0] or record.storage_key, None)
    if len(folders) > SCAN_PATHS_MAX:
        library_tasks.scan_path.delay(str(library.pk), "")
        return
    for path in folders:
        library_tasks.scan_path.delay(str(library.pk), path)


def _finish(
    job: ImportJob,
    status: str,
    *,
    code: str = "",
    message: str = "",
    params: Mapping[str, Any] | None = None,
) -> None:
    now = timezone.now()
    _set(
        job,
        status=status,
        phase="",
        finished_at=now,
        speed_bps=None,
        eta_s=None,
        error_code=code[:32],
        error=message[:500],
        error_params=dict(params or {}),
    )
    if status in {JobStatus.DONE, JobStatus.FAILED}:
        audit.record(
            "import.finish",
            actor=None,
            target=job,
            after={
                "status": status,
                "error_code": code,
                "files": job.files_done if status == JobStatus.DONE else 0,
                "bytes": job.bytes_done,
            },
        )


def run(job_id: UUID | str, dispatch_no: int) -> str | None:  # noqa: PLR0912 (one outcome each)
    """Run one dispatched job to its end; None for a stale or duplicate message."""
    job = _claim(job_id, dispatch_no)
    if job is None:
        return None
    library = job.library
    staging: Path | None = None
    try:
        if library is None or not Path(library.path).is_dir():
            raise ImportRefused("library_missing", "The library or its folder is gone.")
        staging = files.staging_dir(library.path, job.pk.hex)
        reporter = Reporter(job, library.path)
        if job.origin == JobOrigin.LINK:
            staged = _copy_link(job, library, staging, reporter)
        else:
            staged = _copy_source(job, library, staging, reporter)
        if (
            reporter.should_stop()
            or ImportJob.objects.filter(pk=job.pk, cancel_requested=True).exists()
        ):
            raise rclone.Stopped
        placed = _move(job, library, staged)
        _set(job, files_done=len(placed), bytes_done=sum(record.size for record in placed))
        _finish(job, JobStatus.DONE)
        try:
            _scan(library, placed)
        except Exception:
            # The files are in place: the watcher and the reconciliation scan find them.
            logger.exception("import scan not queued", extra={"job": str(job.pk)})
        files.remove_staging(staging)
        return JobStatus.DONE
    except (rclone.Stopped, direct.Stopped):
        _finish(job, JobStatus.CANCELLED)
        if staging is not None:
            files.remove_staging(staging)
        return JobStatus.CANCELLED
    except (ImportRefused, rclone.RcloneError) as exc:
        params = getattr(exc, "params", {}) or {}
        if exc.transient and job.attempts < MAX_ATTEMPTS:
            _set(
                job,
                status=JobStatus.QUEUED,
                phase="",
                retry_at=timezone.now() + RETRY_AFTER,
                error_code=exc.code[:32],
                error=exc.message[:500],
                error_params=dict(params),
                speed_bps=None,
                eta_s=None,
            )
            logger.info("import retried", extra={"job": str(job.pk), "code": exc.code})
            return JobStatus.QUEUED
        _finish(job, JobStatus.FAILED, code=exc.code, message=exc.message, params=params)
        if staging is not None:
            files.remove_staging(staging)
        return JobStatus.FAILED
    except Exception:
        logger.exception("import failed", extra={"job": str(job.pk)})
        _finish(
            job,
            JobStatus.FAILED,
            code="unexpected",
            message="The import failed unexpectedly; see the importer's log.",
        )
        if staging is not None:
            files.remove_staging(staging)
        return JobStatus.FAILED
    finally:
        _release(job.pk)
        dispatch()


# --- Sweeping ------------------------------------------------------------------------------------


def _heartbeat() -> None:
    with contextlib.suppress(redis.RedisError):
        state_redis().set(HEARTBEAT_KEY, str(int(time.time())), ex=HEARTBEAT_TTL_S)


def recover_stale_jobs() -> int:
    """Running jobs whose importer stopped (no lease, no progress) go back to the queue
    (resuming from their staged data), or fail once they had their retry."""
    cutoff = timezone.now() - timedelta(seconds=2 * LEASE_TTL_S)
    recovered = 0
    for job in ImportJob.objects.filter(
        status=JobStatus.RUNNING, updated_at__lt=cutoff
    ).select_related("library"):
        try:
            if state_redis().exists(LEASE_KEY.format(job=job.pk)):
                continue
        except redis.RedisError:
            return recovered
        with transaction.atomic():
            locked = ImportJob.objects.select_for_update().get(pk=job.pk)
            if locked.status != JobStatus.RUNNING:
                continue
            if locked.cancel_requested:
                _finish(locked, JobStatus.CANCELLED)
            elif locked.attempts >= MAX_ATTEMPTS:
                _finish(
                    locked,
                    JobStatus.FAILED,
                    code="worker_lost",
                    message="The importer stopped during the import.",
                )
            else:
                _set(
                    locked,
                    status=JobStatus.QUEUED,
                    phase="",
                    retry_at=timezone.now(),
                    error_code="worker_lost",
                    error="The importer stopped; resuming.",
                )
        recovered += 1
    return recovered


def clean_orphans() -> int:
    """Remove staging folders no queued or running job owns (cancelled, failed or
    finished jobs; a job deleted with its library)."""
    active = {
        job.pk.hex for job in ImportJob.objects.filter(status__in=ACTIVE_JOB_STATUSES).only("id")
    }
    removed = 0
    for path in Library.objects.values_list("path", flat=True):
        folder = Path(path, files.STAGING_FOLDER)
        if not folder.is_dir():
            continue
        for entry in folder.iterdir():
            if entry.is_dir() and entry.name not in active:
                files.remove_staging(entry)
                removed += 1
        with contextlib.suppress(OSError):
            folder.rmdir()
    return removed


def sweep() -> dict[str, int]:
    _heartbeat()
    recovered = recover_stale_jobs()
    removed = clean_orphans()
    dispatched = dispatch()
    return {"recovered": recovered, "removed": removed, "dispatched": dispatched}


def free_space(library: Library) -> dict[str, int] | None:
    """The library disk's size and free bytes, as the web process sees them."""
    try:
        usage = shutil.disk_usage(library.path)
    except OSError:
        return None
    return {"total": usage.total, "free": usage.free}
