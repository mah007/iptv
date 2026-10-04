"""Notification delivery on the `notify` queue (SPEC §7.7, §13)."""

from celery import shared_task

from apps.notifications import services


@shared_task(name="apps.notifications.tasks.send_notification", ignore_result=True)
def send_notification(outbox_id: str) -> str | None:
    """First attempt, right after the message was queued; retries are the dispatcher's."""
    return services.deliver(outbox_id)


@shared_task(name="apps.notifications.tasks.dispatch_queued", ignore_result=True)
def dispatch_queued() -> int:
    """Every minute: messages due for a retry, and everything waiting for SMTP settings."""
    return services.dispatch_due()
