"""Scheduled jobs of the billing app (beat schedule in config.settings.base)."""

from dataclasses import asdict

from celery import shared_task

from apps.billing import services, subscriptions


@shared_task(name="apps.billing.tasks.advance_subscriptions", ignore_result=True)
def advance_subscriptions() -> dict[str, int]:
    """Every 5 minutes: pending → active, active → grace → expired (sessions stop),
    T-7 and T-1 day reminders, and the `iptv_subscriptions` gauge (SPEC §7.6)."""
    return asdict(subscriptions.advance_states())


@shared_task(name="apps.billing.tasks.expire_checkouts", ignore_result=True)
def expire_checkouts() -> int:
    """Hourly: void checkouts left unpaid for `billing.checkout_ttl_hours`."""
    return services.expire_checkouts()
