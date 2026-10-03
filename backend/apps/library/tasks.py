"""Celery tasks of the library pipeline, on the `scan` queue (CELERY_TASK_ROUTES)."""

import logging

from celery import shared_task
from celery.app.task import Task

from apps.library import pipeline, services
from apps.library.models import Library, ScanJob, ScanStatus, ScanTrigger

logger = logging.getLogger(__name__)

#: How long a scan waits for another scan of the same library, between attempts.
BUSY_RETRY_S = 30
BUSY_MAX_RETRIES = 120


@shared_task(bind=True, name="apps.library.tasks.scan_library", max_retries=BUSY_MAX_RETRIES)
def scan_library(self: Task, job_id: str) -> None:
    """Run a queued scan; waits (by retrying) while another scan holds the library."""
    job = ScanJob.objects.select_related("library").filter(pk=job_id).first()
    if job is None or job.status != ScanStatus.QUEUED:
        return
    try:
        services.run_scan(job)
    except services.ScanBusyError as exc:
        raise self.retry(countdown=BUSY_RETRY_S, exc=exc) from None


@shared_task(name="apps.library.tasks.scan_path")
def scan_path(library_id: str, path: str) -> None:
    """The watcher's event: scan one file or folder of a library (relative `path`)."""
    library = Library.objects.filter(pk=library_id, enabled=True).first()
    if library is None:
        return
    services.request_scan(library, trigger=ScanTrigger.WATCHER, path=path.strip("/"))


@shared_task(name="apps.library.tasks.process_media_file")
def process_media_file(file_id: str) -> None:
    """Probe and parse a new or changed file, then queue its metadata match."""
    pipeline.process_file(file_id)


@shared_task(name="apps.library.tasks.reconcile_libraries")
def reconcile_libraries() -> int:
    """Beat, every minute: queue the scans that `scan_interval_min` makes due."""
    started = 0
    for library in services.due_libraries():
        _job, created = services.request_scan(library, trigger=ScanTrigger.SCHEDULE)
        started += int(created)
    if started:
        logger.info("scheduled library scans queued", extra={"count": started})
    return started
