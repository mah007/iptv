"""Celery tasks of the media pipeline (ADR-0010).

`prepare_media_file` runs on the worker's `scan` queue (it probes the source);
`run_transcode_job` is sent to `transcode.<backend>` explicitly, so only a transcoder
with that backend takes it; `reconcile_media` is beat's safety net every 5 minutes.
"""

import structlog
from celery import shared_task

from apps.media import services

logger = structlog.get_logger(__name__)


@shared_task(name="apps.media.tasks.prepare_media_file")
def prepare_media_file(file_id: str, urgent: bool = False) -> str:
    """Plan a matched file: a direct-play source rendition, or a compat MP4 job."""
    return services.prepare_file(file_id, urgent=urgent).value


@shared_task(name="apps.media.tasks.run_transcode_job", acks_late=True)
def run_transcode_job(job_id: str, dispatch_no: int) -> str | None:
    """Run one compat MP4 job (transcoders only: needs ffmpeg and a writable /data)."""
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
