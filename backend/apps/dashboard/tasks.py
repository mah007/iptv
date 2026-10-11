from celery import shared_task

from apps.dashboard import metrics


@shared_task(name="apps.dashboard.tasks.publish_metrics", ignore_result=True)
def publish_metrics() -> None:
    """Every minute (beat): the state and business gauges /metrics exports (ADR-0018)."""
    metrics.publish()
