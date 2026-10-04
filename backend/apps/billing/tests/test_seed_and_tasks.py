"""`make seed` billing demo data, and the beat tasks."""

import io
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.accounts.models import User
from apps.billing import tasks
from apps.billing.models import Invoice, Payment, Plan, Subscription, SubscriptionStatus
from apps.conftest import CustomerFactory
from apps.notifications import tasks as notification_tasks
from apps.playback import entitlements

pytestmark = pytest.mark.django_db


def test_seed_demo_adds_plans_and_subscriptions_once() -> None:
    out = io.StringIO()
    call_command("seed_demo", stdout=out)
    assert "Billing demo data ready: 4 plans, 12 new subscriptions" in out.getvalue()
    assert sorted(Plan.objects.values_list("code", flat=True)) == [
        "basic",
        "premium",
        "standard",
        "trial",
    ]
    statuses = set(Subscription.objects.values_list("status", flat=True))
    assert {"active", "grace", "expired", "pending"} <= statuses
    assert Payment.objects.filter(status="succeeded").count() == 5
    assert Invoice.objects.filter(status="paid").count() == 5
    paid = User.objects.get(email="mohammed.harbi@demo.smart-iptv.test")
    entitlement = entitlements.refresh(paid.pk)
    assert entitlement is not None
    assert entitlement["source"] == "subscription"
    assert entitlement["max_streams"] == 2
    profile_only = User.objects.get(email="sara.otaibi@demo.smart-iptv.test")
    assert entitlements.refresh(profile_only.pk)["source"] == "access_profile"  # type: ignore[index]
    trial_request = Subscription.objects.get(user__email="james.carter@demo.smart-iptv.test")
    assert trial_request.status == SubscriptionStatus.PENDING
    again = io.StringIO()
    call_command("seed_demo", stdout=again)
    assert "4 plans, 0 new subscriptions" in again.getvalue()
    assert Subscription.objects.count() == 12


def test_beat_tasks(make_customer: CustomerFactory, plan: Plan) -> None:
    from apps.billing import services, subscriptions  # noqa: PLC0415

    subscription = subscriptions.activate(make_customer(), plan, source="manual")
    Subscription.objects.filter(pk=subscription.pk).update(
        starts_at=timezone.now() - timedelta(days=40), ends_at=timezone.now() - timedelta(days=10)
    )
    assert tasks.advance_subscriptions()["expired"] == 1
    services.checkout(make_customer(), plan, "manual")
    Invoice.objects.update(created_at=timezone.now() - timedelta(days=2))
    assert tasks.expire_checkouts() == 1
    assert notification_tasks.dispatch_queued() >= 1  # the activation and expiry emails
