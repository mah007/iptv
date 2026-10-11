"""Personal-data retention (apps/ops/retention.py; SPEC §17)."""

from datetime import timedelta
from typing import Any

import pytest
from axes.models import AccessAttempt
from django.contrib.sessions.models import Session
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.billing.models import WebhookEvent
from apps.catalog.models import Movie
from apps.conftest import CustomerFactory
from apps.core.services import set_setting
from apps.engagement.models import WatchProgress
from apps.notifications.models import NotificationOutbox, OutboxStatus
from apps.ops import retention, tasks
from apps.playback.models import EndReason, PlaybackSession, TitleKind

pytestmark = pytest.mark.django_db


def ago(days: float) -> Any:
    return timezone.now() - timedelta(days=days)


def session(user: Any, *, ended_days: float | None) -> PlaybackSession:
    ended = ago(ended_days) if ended_days is not None else None
    return PlaybackSession.objects.create(
        session_key=f"k{PlaybackSession.objects.count()}",
        user=user,
        title_kind=TitleKind.MOVIE,
        title_id="01920000-0000-7000-8000-000000000001",
        rendition="compat",
        ip="203.0.113.7",
        country="SA",
        user_agent="IPTV Smarters",
        started_at=ended or timezone.now(),
        last_heartbeat_at=ended or timezone.now(),
        ended_at=ended,
        end_reason=EndReason.STOPPED if ended else "",
    )


def test_sessions_lose_their_ip_then_go(make_customer: CustomerFactory) -> None:
    user = make_customer()
    live = session(user, ended_days=None)
    fresh = session(user, ended_days=10)
    month_old = session(user, ended_days=40)
    year_old = session(user, ended_days=400)
    counts = retention.sessions(timezone.now())
    assert counts == {"sessions_anonymised": 1, "sessions_deleted": 1}
    month_old.refresh_from_db()
    assert month_old.ip is None
    assert month_old.user_agent == ""
    assert month_old.country == "SA"
    fresh.refresh_from_db()
    live.refresh_from_db()
    assert fresh.ip == "203.0.113.7"
    assert live.ip == "203.0.113.7"
    assert not PlaybackSession.objects.filter(pk=year_old.pk).exists()
    assert retention.sessions(timezone.now()) == {"sessions_anonymised": 0, "sessions_deleted": 0}


def test_watch_history_and_stale_resume_points(make_customer: CustomerFactory) -> None:
    user = make_customer()
    movies = [Movie.objects.create(title=f"Movie {n}") for n in range(4)]
    rows = [
        WatchProgress.objects.create(user=user, movie=movies[0], completed=True),
        WatchProgress.objects.create(user=user, movie=movies[1], completed=True),
        WatchProgress.objects.create(user=user, movie=movies[2], completed=False),
        WatchProgress.objects.create(user=user, movie=movies[3], completed=False),
    ]
    WatchProgress.objects.filter(pk=rows[1].pk).update(updated_at=ago(400))
    WatchProgress.objects.filter(pk=rows[3].pk).update(updated_at=ago(800))
    WatchProgress.objects.filter(pk=rows[2].pk).update(updated_at=ago(400))
    assert retention.watch_history(timezone.now()) == {
        "history_deleted": 1,
        "resume_points_deleted": 1,
    }
    left = set(WatchProgress.objects.values_list("pk", flat=True))
    assert left == {rows[0].pk, rows[2].pk}


def test_outbox_auth_log_and_webhook_payloads() -> None:
    sent = NotificationOutbox.objects.create(
        template_key="t", locale="en", status=OutboxStatus.SENT
    )
    queued = NotificationOutbox.objects.create(
        template_key="t", locale="en", status=OutboxStatus.QUEUED
    )
    NotificationOutbox.objects.filter(pk__in=[sent.pk, queued.pk]).update(updated_at=ago(100))
    assert retention.outbox(timezone.now()) == {"outbox_deleted": 1}
    assert NotificationOutbox.objects.filter(pk=queued.pk).exists()

    attempt = AccessAttempt.objects.create(
        username="x", ip_address="203.0.113.9", failures_since_start=1
    )
    AccessAttempt.objects.filter(pk=attempt.pk).update(attempt_time=ago(100))
    assert retention.auth_log(timezone.now())["auth_log_deleted"] == 1

    event = WebhookEvent.objects.create(
        provider="stripe",
        event_id="evt_1",
        type="x",
        payload={"email": "a@b.c"},
        processed_at=ago(400),
    )
    WebhookEvent.objects.filter(pk=event.pk).update(created_at=ago(400))
    pending = WebhookEvent.objects.create(
        provider="stripe", event_id="evt_2", type="x", payload={"k": 1}
    )
    WebhookEvent.objects.filter(pk=pending.pk).update(created_at=ago(400))
    assert retention.webhook_payloads(timezone.now()) == {"webhook_payloads_emptied": 1}
    event.refresh_from_db()
    pending.refresh_from_db()
    assert event.payload == {}
    assert pending.payload == {"k": 1}


def test_settings_change_the_periods(make_customer: CustomerFactory) -> None:
    user = make_customer()
    old = session(user, ended_days=100)
    set_setting("privacy.session_retention_days", 90, actor=None)
    assert retention.sessions(timezone.now())["sessions_deleted"] == 1
    assert not PlaybackSession.objects.filter(pk=old.pk).exists()


def test_the_daily_job_runs_everything_and_audits(make_customer: CustomerFactory) -> None:
    Session.objects.create(session_key="expired", session_data="x", expire_date=ago(1))
    AuditLog.objects.create(action="customer.update", at=ago(800))
    tasks.apply_retention()
    assert not Session.objects.filter(session_key="expired").exists()
    entry = AuditLog.objects.get(action="privacy.retention")
    assert entry.actor is None
    assert entry.after is not None
    assert entry.after["audit_deleted"] == 1
    assert entry.after["sessions_expired_deleted"] == 1


def test_one_failing_step_does_not_stop_the_others(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(now: Any) -> dict[str, int]:
        raise RuntimeError("boom")

    broken.__name__ = "broken"
    monkeypatch.setattr(retention, "STEPS", (broken, retention._django_sessions))
    assert "sessions_expired_deleted" in retention.apply()
