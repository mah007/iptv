"""Personal-data retention (SPEC §17, ADR-0020): delete or anonymise what is no longer
needed, as the admin's settings (group Privacy) say. Runs daily (`ops-apply-retention`).

- ended playback sessions lose their IP address and user agent after
  `privacy.session_ip_days` (country and ASN stay for the statistics), and are deleted
  after `privacy.session_retention_days` (at least 90 days: the dashboard's charts);
- finished titles leave the watch history after `privacy.watch_history_days`, and
  unfinished ones lose their resume point after `privacy.resume_days`;
- audit entries are deleted after `privacy.audit_retention_days`, billing, payment and
  subscription entries after `privacy.audit_billing_retention_days`, by `audit_purge`,
  which refuses less than one and five years;
- sent, failed and skipped messages leave the notification log after
  `privacy.outbox_retention_days`;
- failed admin sign-ins (django-axes) are deleted after `privacy.auth_log_retention_days`;
- payment providers' raw webhook payloads are emptied after `privacy.webhook_payload_days`
  (the payment records stay);
- expired admin and portal sessions are deleted at once (`clearsessions`).

App tokens, password links and monitoring tickets live in redis-state with their own
expiry, so they need no job. Deletes go in batches, so no table is locked for long.
"""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Final

from django.db import connection, transaction
from django.db.models import Model, QuerySet
from django.utils import timezone

from apps.core.services import get_setting

logger = logging.getLogger(__name__)

BATCH: Final = 5000


def _days(key: str) -> timedelta:
    return timedelta(days=int(get_setting(key)))


def delete_in_batches(queryset: QuerySet[Any], batch: int = BATCH) -> int:
    """Delete the matching rows a batch at a time; returns how many."""
    model: type[Model] = queryset.model
    total = 0
    while True:
        ids = list(queryset.values_list("pk", flat=True)[:batch])
        if not ids:
            return total
        with transaction.atomic():
            deleted, _ = model._default_manager.filter(pk__in=ids).delete()
        total += deleted
        if len(ids) < batch:
            return total


def update_in_batches(queryset: QuerySet[Any], values: dict[str, Any], batch: int = BATCH) -> int:
    model: type[Model] = queryset.model
    total = 0
    while True:
        ids = list(queryset.values_list("pk", flat=True)[:batch])
        if not ids:
            return total
        total += model._default_manager.filter(pk__in=ids).update(**values)
        if len(ids) < batch:
            return total


def sessions(now: datetime) -> dict[str, int]:
    from apps.playback.models import PlaybackSession  # noqa: PLC0415

    ended = PlaybackSession.objects.filter(ended_at__isnull=False)
    # Old ones go first, so only the ones that stay are anonymised.
    deleted = delete_in_batches(
        ended.filter(ended_at__lt=now - _days("privacy.session_retention_days"))
    )
    anonymised = update_in_batches(
        ended.filter(ended_at__lt=now - _days("privacy.session_ip_days")).exclude(
            ip__isnull=True, user_agent=""
        ),
        {"ip": None, "user_agent": ""},
    )
    return {"sessions_anonymised": anonymised, "sessions_deleted": deleted}


def watch_history(now: datetime) -> dict[str, int]:
    from apps.engagement.models import WatchProgress  # noqa: PLC0415

    finished = delete_in_batches(
        WatchProgress.objects.filter(
            completed=True, updated_at__lt=now - _days("privacy.watch_history_days")
        )
    )
    stale = delete_in_batches(
        WatchProgress.objects.filter(
            completed=False, updated_at__lt=now - _days("privacy.resume_days")
        )
    )
    return {"history_deleted": finished, "resume_points_deleted": stale}


def audit() -> dict[str, int]:
    """Through the database function: the app role cannot delete audit rows itself."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT audit_purge(%s, %s)",
            [
                int(get_setting("privacy.audit_retention_days")),
                int(get_setting("privacy.audit_billing_retention_days")),
            ],
        )
        row = cursor.fetchone()
    return {"audit_deleted": int(row[0]) if row and row[0] else 0}


def outbox(now: datetime) -> dict[str, int]:
    from apps.notifications.models import NotificationOutbox, OutboxStatus  # noqa: PLC0415

    done = (OutboxStatus.SENT, OutboxStatus.FAILED, OutboxStatus.SKIPPED)
    deleted = delete_in_batches(
        NotificationOutbox.objects.filter(
            status__in=done, updated_at__lt=now - _days("privacy.outbox_retention_days")
        )
    )
    return {"outbox_deleted": deleted}


def auth_log(now: datetime) -> dict[str, int]:
    from axes.models import AccessAttempt, AccessFailureLog, AccessLog  # noqa: PLC0415

    cutoff = now - _days("privacy.auth_log_retention_days")
    deleted = delete_in_batches(AccessAttempt.objects.filter(attempt_time__lt=cutoff))
    deleted += delete_in_batches(AccessFailureLog.objects.filter(attempt_time__lt=cutoff))
    deleted += delete_in_batches(AccessLog.objects.filter(attempt_time__lt=cutoff))
    return {"auth_log_deleted": deleted}


def webhook_payloads(now: datetime) -> dict[str, int]:
    from apps.billing.models import WebhookEvent  # noqa: PLC0415

    emptied = update_in_batches(
        WebhookEvent.objects.filter(
            processed_at__isnull=False,
            created_at__lt=now - _days("privacy.webhook_payload_days"),
        ).exclude(payload={}),
        {"payload": {}},
    )
    return {"webhook_payloads_emptied": emptied}


def django_sessions() -> dict[str, int]:
    from django.contrib.sessions.models import Session  # noqa: PLC0415

    return {
        "sessions_expired_deleted": delete_in_batches(
            Session.objects.filter(expire_date__lt=timezone.now())
        )
    }


def _audit(now: datetime) -> dict[str, int]:
    return audit()


def _django_sessions(now: datetime) -> dict[str, int]:
    return django_sessions()


STEPS: Final[tuple[Callable[[datetime], dict[str, int]], ...]] = (
    sessions,
    watch_history,
    outbox,
    auth_log,
    webhook_payloads,
    _audit,
    _django_sessions,
)


def apply(now: datetime | None = None) -> dict[str, int]:
    """Run every rule; one failing rule does not stop the others."""
    now = now or timezone.now()
    counts: dict[str, int] = {}
    for step in STEPS:
        try:
            counts.update(step(now))
        except Exception:
            logger.exception("retention step failed", extra={"step": step.__name__})
    return counts
