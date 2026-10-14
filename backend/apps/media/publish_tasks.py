"""Celery tasks of the S3 origin's publisher (M15, ADR-0022), on the worker's `publish`
queue: the worker has internet access (egress); the transcoder doesn't."""

from celery import shared_task

from apps.core.services import get_setting


@shared_task(name="apps.media.publish_tasks.publish_asset", ignore_result=True)
def publish_asset(file_id: str) -> str:
    """Publish one media file's renditions (after a job or a track edit changed them)."""
    if not get_setting("storage.s3_enabled"):
        return "disabled"
    from apps.media import publisher  # noqa: PLC0415

    return publisher.publish_file_id(file_id)


@shared_task(name="apps.media.publish_tasks.reconcile_publications", ignore_result=True)
def reconcile_publications() -> dict[str, int]:
    """Every 5 minutes: files waiting or failed, and replaced copies past their grace."""
    if not get_setting("storage.s3_enabled"):
        return {}
    from apps.media import publisher  # noqa: PLC0415

    counts: dict[str, int] = publisher.reconcile()
    return counts
