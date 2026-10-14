"""Beat jobs of the anti-sharing app (ADR-0021)."""

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="apps.antisharing.tasks.score_recent", ignore_result=True)
def score_recent() -> None:
    """Every 5 minutes: stream networks into sightings, then the risk score of everyone
    seen in the last 24 hours; and the GeoIP database's health."""
    from apps.antisharing import services  # noqa: PLC0415

    counts = services.score_recent()
    services.check_geoip()
    if counts["flagged"]:
        logger.info("antisharing.scored", extra=counts)


@shared_task(name="apps.antisharing.tasks.update_geoip", ignore_result=True)
def update_geoip(*, force: bool = False, only_if_missing: bool = False) -> None:
    """Daily, at worker start (when missing) and from the admin: the DB-IP Lite databases.

    A no-op unless a newer monthly release exists (or `force`). One update at a time."""
    from apps.core import geoip  # noqa: PLC0415
    from apps.core.services import get_setting  # noqa: PLC0415
    from apps.core.stores import state_redis  # noqa: PLC0415

    if not geoip.enabled():
        return
    if only_if_missing and all(geoip.available(kind) for kind in geoip.KINDS):
        return
    if not force and not only_if_missing and not get_setting("geoip.auto_update"):
        return
    lock = state_redis().lock("antisharing:geoip:update", timeout=900, blocking=False)
    if not lock.acquire(blocking=False):
        return
    try:
        result = geoip.update(force=force)
    except geoip.UpdateError as exc:
        logger.warning("geoip.update_failed", extra={"error": str(exc)[:300]})
        return
    finally:
        try:
            lock.release()
        except Exception:
            logger.debug("geoip: lock already released")
    logger.info(
        "geoip.update",
        extra={
            "installed": list(result.installed),
            "current": list(result.current),
            "failed": list(result.failed),
            "errors": list(result.errors),
        },
    )
