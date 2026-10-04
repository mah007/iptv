"""Engagement jobs (SPEC §7.9)."""

import structlog
from celery import shared_task

from apps.engagement import recommend

logger = structlog.get_logger(__name__)


@shared_task(name="apps.engagement.tasks.compute_similar_titles")
def compute_similar_titles() -> dict[str, int]:
    """Nightly: rebuild the similar-titles table."""
    written = recommend.compute_similar_titles()
    logger.info("engagement.similar_titles", **written)
    return written
