"""Celery tasks of imports, on the `import` queue: only the importer service consumes it
(rclone, the media root writable; ADR-0025)."""

import logging

from celery import shared_task

from apps.imports import services

logger = logging.getLogger(__name__)


# Acknowledged on receipt: a long copy outlives the broker's visibility timeout, and the
# job row (claim by dispatch number, lease, sweep) already makes a lost run recoverable.
@shared_task(name="apps.imports.tasks.run_import", acks_late=False)
def run_import(job_id: str, dispatch_no: int) -> str | None:
    return services.run(job_id, dispatch_no)


@shared_task(name="apps.imports.tasks.remote_request", acks_late=False)
def remote_request(request_id: str, kind: str, source_id: str, path: str = "") -> bool:
    """A source's Test or one folder of its browser; the admin polls the answer."""
    result = services.run_request(request_id, kind, source_id, path)
    return bool(result.get("ok"))


@shared_task(name="apps.imports.tasks.sweep_imports")
def sweep_imports() -> dict[str, int]:
    """Beat, every minute: the importer's heartbeat, jobs of a stopped importer back in
    the queue, unused staging folders removed, queued jobs started."""
    result = services.sweep()
    if any(result.values()):
        logger.info("imports swept", extra=result)
    return result
