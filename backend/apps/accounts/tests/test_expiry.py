"""The access-expiry beat job (plan scope change: every 5 minutes, exactly once per period)."""

import json
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from django.conf import settings
from django.utils import timezone

from apps.accounts import services, tasks
from apps.accounts.models import CustomerAccess
from apps.accounts.signals import access_expired
from apps.audit.models import AuditLog
from apps.conftest import CustomerFactory
from apps.core import metrics
from apps.core.stores import state_redis
from apps.playback.entitlements import entitlement_key

pytestmark = pytest.mark.django_db
type Capture = Callable[..., Any]


@pytest.fixture
def received() -> Any:
    calls: list[dict[str, Any]] = []

    def receiver(**kwargs: Any) -> None:
        calls.append(kwargs)

    access_expired.connect(receiver)
    yield calls
    access_expired.disconnect(receiver)


def test_beat_runs_the_job_every_five_minutes() -> None:
    entry = settings.CELERY_BEAT_SCHEDULE["accounts-expire-access"]
    assert entry["task"] == tasks.expire_access.name
    assert entry["schedule"] == 300.0


def test_ended_periods_are_processed_exactly_once(
    make_customer: CustomerFactory,
    received: list[dict[str, Any]],
    django_capture_on_commit_callbacks: Capture,
) -> None:
    now = timezone.now()
    ended = make_customer(expires_at=now - timedelta(minutes=1))
    make_customer(expires_at=now + timedelta(days=1))
    make_customer(expires_at=None)
    state_redis().set(entitlement_key(ended.pk), json.dumps({"status": "active"}))

    with django_capture_on_commit_callbacks(execute=True):
        assert tasks.expire_access() == 1
    assert [call["user_id"] for call in received] == [ended.pk]
    stored = json.loads(state_redis().get(entitlement_key(ended.pk)))  # type: ignore[arg-type]
    assert stored["status"] == "expired"
    entry = AuditLog.objects.get(action="customer.access.expire")
    assert entry.actor is None
    assert entry.target_id == str(ended.pk)

    with django_capture_on_commit_callbacks(execute=True):
        assert services.process_expired_access() == 0
    assert len(received) == 1


def test_extending_rearms_the_job(make_customer: CustomerFactory) -> None:
    now = timezone.now()
    user = make_customer(expires_at=now - timedelta(minutes=1))
    assert services.process_expired_access(now=now) == 1
    services.update_access(user, {"expires_at": now + timedelta(minutes=5)}, actor=None)
    assert services.process_expired_access(now=now) == 0
    assert services.process_expired_access(now=now + timedelta(minutes=6)) == 1
    access = CustomerAccess.objects.get(user=user)
    assert access.expiry_processed_for == access.expires_at


def test_batches_cover_every_row(make_customer: CustomerFactory) -> None:
    past = timezone.now() - timedelta(hours=1)
    for _ in range(5):
        make_customer(expires_at=past)
    assert services.process_expired_access(batch_size=2) == 5
    assert AuditLog.objects.filter(action="customer.access.expire").count() == 5


def test_job_publishes_access_counts(
    make_customer: CustomerFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = timezone.now()
    make_customer(expires_at=now - timedelta(days=1))
    make_customer(expires_at=now + timedelta(days=1))
    suspended = make_customer()
    services.suspend_customer(suspended, actor=None)
    published: list[dict[tuple[str, ...], float]] = []
    monkeypatch.setattr(metrics.SUBSCRIPTIONS, "publish", published.append)
    services.process_expired_access(now=now)
    assert published == [{("active",): 1, ("expired",): 1, ("suspended",): 1, ("disabled",): 0}]


def test_metrics_failures_never_break_the_job(
    make_customer: CustomerFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(_samples: object) -> None:
        raise ConnectionError

    monkeypatch.setattr(metrics.SUBSCRIPTIONS, "publish", broken)
    make_customer(expires_at=timezone.now() - timedelta(days=1))
    assert services.process_expired_access() == 1
