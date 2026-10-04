"""Celery tasks of the media pipeline (ADR-0010, ADR-0014).

`prepare_media_file` and `reprocess_media_file` run on the worker's `scan` queue (they
probe the source); `run_transcode_job` is sent to `transcode.<backend>` explicitly, so
only a transcoder with that backend takes it; `reconcile_media` is beat's safety net
every 5 minutes and `cleanup_renditions` deletes what no rendition explains every 6
hours. The admin's title actions that touch the media volume (`publish_presentations`,
`store_title_image`, a cleanup dry run) run here too: the web process has no media
volume.
"""

import base64
import json
from typing import Any, Final

import structlog
from celery import shared_task
from django.utils import timezone

from apps.catalog.models import MediaFile
from apps.core.stores import cache_redis
from apps.media import cleanup, presentations, services

logger = structlog.get_logger(__name__)

#: redis-cache key of the latest cleanup report (recomputable: a dry run makes a new one).
CLEANUP_REPORT_KEY: Final = "media:cleanup:last"
CLEANUP_REPORT_TTL_S: Final = 7 * 24 * 3600
CLEANUP_REPORT_MAX_ENTRIES: Final = 500


@shared_task(name="apps.media.tasks.prepare_media_file")
def prepare_media_file(file_id: str, urgent: bool = False) -> str:
    """Plan a matched file: a direct-play source rendition or a compat MP4 job, and the
    other outputs beside it."""
    return services.prepare_file(file_id, urgent=urgent).value


@shared_task(name="apps.media.tasks.run_transcode_job", acks_late=True)
def run_transcode_job(job_id: str, dispatch_no: int) -> str | None:
    """Run one transcode job of any profile (transcoders only: ffmpeg, a writable /data)."""
    status = services.run_job(job_id, dispatch_no)
    return status.value if status is not None else None


@shared_task(name="apps.media.tasks.reconcile_media")
def reconcile_media() -> int:
    """Queue files that never got planned and requeue jobs whose transcoder died."""
    files = services.files_to_prepare()
    for file_id in files:
        prepare_media_file.delay(str(file_id))
    recovered = services.recover_stale_jobs()
    if files or recovered:
        logger.info("media.reconciled", planned=len(files), recovered=recovered)
    return len(files) + recovered


@shared_task(name="apps.media.tasks.cleanup_renditions")
def cleanup_renditions(dry_run: bool = False) -> int:
    """Delete (or with `dry_run`, list) orphaned, expired and superseded renditions;
    stores the report for the admin and returns its bytes."""
    report = cleanup.cleanup(dry_run=dry_run)
    document: dict[str, Any] = {
        "dry_run": report.dry_run,
        "finished_at": timezone.now().isoformat(),
        "bytes": report.bytes,
        "entries": len(report.removals),
        "removals": [
            {"path": r.path, "reason": r.reason, "bytes": r.bytes}
            for r in report.removals[:CLEANUP_REPORT_MAX_ENTRIES]
        ],
    }
    try:
        cache_redis().set(CLEANUP_REPORT_KEY, json.dumps(document), ex=CLEANUP_REPORT_TTL_S)
    except Exception:
        logger.warning("media.cleanup_report_not_stored")
    return report.bytes


@shared_task(name="apps.media.tasks.publish_presentations")
def publish_presentations(file_id: str) -> list[str]:
    """Rewrite a file's HLS masters and links (after a track edit or a deletion)."""
    file = MediaFile.objects.select_related("library").filter(pk=file_id).first()
    if file is None:
        return []
    return presentations.publish(file)


@shared_task(name="apps.media.tasks.reprocess_media_file")
def reprocess_media_file(file_id: str, outputs: list[str]) -> dict[str, str]:
    """The admin's Reprocess for one file: queue each output again."""
    file = MediaFile.objects.select_related("library").filter(pk=file_id).first()
    if file is None or file.removed_at is not None:
        return {}
    result = services.reprocess(file, outputs)
    logger.info("media.reprocess", file=file_id, result=result)
    return result


@shared_task(name="apps.media.tasks.store_title_image")
def store_title_image(request: dict[str, Any]) -> bool:
    """Store an artwork the admin chose (`tmdb_path`) or uploaded (`upload_b64`).
    `request`: `{title_kind, title_id, kind, tmdb_path, upload_b64, primary}`."""
    from apps.media import artwork  # noqa: PLC0415 (metadata imports are heavy)

    upload_b64 = str(request.get("upload_b64") or "")
    return artwork.store(
        str(request["title_kind"]),
        str(request["title_id"]),
        kind=str(request["kind"]),
        tmdb_path=str(request.get("tmdb_path") or "") or None,
        upload=base64.b64decode(upload_b64) if upload_b64 else None,
        primary=bool(request.get("primary")),
    )
