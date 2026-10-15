"""Scheduled jobs of the accounts app (beat schedule in config.settings.base)."""

from celery import shared_task

from apps.accounts import services


@shared_task(name="apps.accounts.tasks.expire_access", ignore_result=True)
def expire_access() -> int:
    """Every 5 minutes: customers whose access period ended get an `expired`
    entitlement, an audit entry and the `access_expired` signal (ADR-0006)."""
    return services.process_expired_access()


@shared_task(name="apps.accounts.tasks.expire_idle_devices", ignore_result=True)
def expire_idle_devices() -> int:
    """Daily: browsers and app sign-ins nobody uses any more leave the device lists
    (ADR-0023)."""
    return services.expire_idle_devices()
