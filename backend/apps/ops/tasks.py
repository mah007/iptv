"""Beat jobs of the operations app (ADR-0020)."""

import logging

from celery import shared_task

from apps.audit import services as audit

logger = logging.getLogger(__name__)


@shared_task(name="apps.ops.tasks.schedule_backups", ignore_result=True)
def schedule_backups() -> None:
    """Every minute: queue the backups, offsite copies and drills that are due; fail
    runs the backup service lost; evaluate the backup alerts."""
    from apps.ops import alerting, backups  # noqa: PLC0415

    backups.expire_lost_runs()
    created = backups.schedule_due()
    if created:
        logger.info("ops.backups_queued", extra={"runs": [run.kind for run in created]})
    alerting.check()


@shared_task(name="apps.ops.tasks.apply_retention", ignore_result=True)
def apply_retention() -> None:
    """Daily: personal-data retention (Settings → Privacy)."""
    from apps.ops import retention  # noqa: PLC0415

    counts = retention.apply()
    audit.record("privacy.retention", actor=None, after=counts)
    logger.info("ops.retention", extra=counts)


@shared_task(name="apps.ops.tasks.render_crowdsec", ignore_result=True)
def render_crowdsec() -> None:
    """Every minute and when its settings change: Traefik's CrowdSec middleware file."""
    from apps.ops import crowdsec  # noqa: PLC0415

    crowdsec.write()
