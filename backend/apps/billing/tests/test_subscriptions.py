"""subscriptions: activate (the single writer), admin changes, trials and the state job."""

from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import User, UserStatus
from apps.accounts.signals import access_expired, access_suspended
from apps.audit.models import AuditLog
from apps.billing import renewal, subscriptions
from apps.billing.models import (
    EndReason,
    Plan,
    Subscription,
    SubscriptionSource,
    SubscriptionStatus,
)
from apps.billing.tests.conftest import PlanFactory, configure
from apps.catalog.models import Category
from apps.conftest import CustomerFactory
from apps.core.errors import ErrorCode, ProblemError
from apps.core.metrics import SUBSCRIPTIONS
from apps.notifications.models import NotificationOutbox
from apps.playback import entitlements

pytestmark = pytest.mark.django_db
type Capture = Callable[..., Any]


@pytest.fixture
def customer(make_customer: CustomerFactory) -> User:
    return make_customer(devices=1)


def current(user: User, *, refresh: bool = False) -> entitlements.Entitlement:
    value = entitlements.refresh(user.pk) if refresh else entitlements.get(user.pk)
    assert value is not None
    return value


def activate(user: User, plan: Plan, **kwargs: Any) -> Subscription:
    kwargs.setdefault("source", SubscriptionSource.MANUAL)
    return subscriptions.activate(user, plan, **kwargs)


def shift(subscription: Subscription, **delta: float) -> Subscription:
    """Move the period into the past (as if time went by)."""
    moved = timedelta(**delta)
    Subscription.objects.filter(pk=subscription.pk).update(
        starts_at=subscription.starts_at - moved, ends_at=subscription.ends_at - moved
    )
    subscription.refresh_from_db()
    return subscription


def events(key: str) -> list[NotificationOutbox]:
    return list(NotificationOutbox.objects.filter(template_key=key))


# --- Activation -----------------------------------------------------------------------------


def test_activate_creates_a_running_subscription_with_a_snapshot(
    customer: User, plan: Plan, category: Category, owner: User
) -> None:
    plan.categories.set([category])
    before = timezone.now()
    subscription = activate(customer, plan, actor=owner, ip="203.0.113.5")
    assert subscription.status == SubscriptionStatus.ACTIVE
    assert subscription.starts_at >= before
    local = subscription.starts_at.astimezone(renewal.zone(customer.timezone))
    assert (subscription.ends_at.astimezone(local.tzinfo) - local).days == 30
    assert subscription.grace_days == 3
    assert subscription.plan_snapshot["max_streams"] == 2
    assert subscription.plan_snapshot["category_ids"] == [str(category.pk)]
    assert subscription.created_by == owner
    entry = AuditLog.objects.get(action="subscription.activate")
    assert entry.actor == owner
    assert (entry.after or {})["plan"] == "standard"
    assert entry.actor_ip == "203.0.113.5"
    welcome = events("account_activated")[0]
    assert welcome.user == customer
    assert welcome.payload["usernames"] == [customer.devices.get().credential.username]
    assert welcome.payload["plan_name"] == "القياسية"  # the customer's language (ar)


def test_activate_rebuilds_the_entitlement_after_commit(
    customer: User, plan: Plan, django_capture_on_commit_callbacks: Capture
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        subscription = activate(customer, plan)
    stored = entitlements.get(customer.pk)
    assert stored is not None
    assert stored["source"] == "subscription"
    assert stored["status"] == "active"
    assert stored["max_streams"] == 2
    assert stored["ends_at"] == subscription.ends_at.isoformat()


def test_renewal_extends_from_the_current_end_and_takes_the_new_plan(
    customer: User, plan: Plan, make_plan: PlanFactory
) -> None:
    first = activate(customer, plan)
    premium = make_plan(code="premium", max_streams=4)
    renewed = activate(customer, premium)
    assert renewed.pk == first.pk
    assert renewed.plan == premium
    assert renewed.plan_snapshot["max_streams"] == 4
    assert renewed.ends_at - first.ends_at >= timedelta(days=30) - timedelta(hours=1)
    assert Subscription.objects.count() == 1
    assert AuditLog.objects.filter(action="subscription.renew").exists()
    assert len(events("subscription_renewed")) == 1


def test_renewal_in_grace_runs_from_now_and_reactivates(customer: User, plan: Plan) -> None:
    subscription = shift(activate(customer, plan), days=31)
    subscriptions.advance_states()
    subscription.refresh_from_db()
    assert subscription.status == SubscriptionStatus.GRACE
    renewed = activate(customer, plan)
    assert renewed.status == SubscriptionStatus.ACTIVE
    assert renewed.ends_at > timezone.now() + timedelta(days=29)


def test_a_suspended_subscription_is_extended_but_stays_suspended(
    customer: User, plan: Plan
) -> None:
    subscription = subscriptions.suspend(activate(customer, plan), actor=None)
    renewed = activate(customer, plan)
    assert renewed.pk == subscription.pk
    assert renewed.status == SubscriptionStatus.SUSPENDED


def test_future_starts_are_pending_and_start_with_the_job(customer: User, plan: Plan) -> None:
    start = timezone.now() + timedelta(days=2)
    pending = activate(customer, plan, starts_at=start)
    assert pending.status == SubscriptionStatus.PENDING
    assert current(customer, refresh=True)["source"] == "access_profile"
    subscriptions.advance_states(now=start + timedelta(minutes=1))
    pending.refresh_from_db()
    assert pending.status == SubscriptionStatus.ACTIVE
    assert len(events("account_activated")) == 1


def test_a_pending_period_merges_into_a_subscription_bought_meanwhile(
    customer: User, plan: Plan
) -> None:
    start = timezone.now() + timedelta(days=2)
    pending = activate(customer, plan, starts_at=start)
    current = activate(customer, plan)  # no current one yet: a second row starts now
    assert current.pk != pending.pk
    end_before = current.ends_at
    subscriptions.advance_states(now=start + timedelta(minutes=1))
    pending.refresh_from_db()
    current.refresh_from_db()
    assert pending.status == SubscriptionStatus.CANCELLED
    assert pending.end_reason == EndReason.MERGED
    assert current.ends_at == end_before + (pending.ends_at - pending.starts_at)


def test_with_a_current_subscription_a_future_start_is_refused(customer: User, plan: Plan) -> None:
    activate(customer, plan)
    with pytest.raises(ProblemError) as excinfo:
        activate(customer, plan, starts_at=timezone.now() + timedelta(days=3))
    assert excinfo.value.problem_code == ErrorCode.CONFLICT


def test_end_overrides_and_imported_past_periods(customer: User, plan: Plan) -> None:
    now = timezone.now()
    past = activate(
        customer,
        plan,
        source=SubscriptionSource.IMPORT,
        starts_at=now - timedelta(days=60),
        ends_at=now - timedelta(days=30),
    )
    assert past.status == SubscriptionStatus.EXPIRED
    assert past.end_reason == EndReason.EXPIRED
    with pytest.raises(ProblemError):
        activate(customer, plan, starts_at=now, ends_at=now - timedelta(days=1))
    override = activate(customer, plan, ends_at=now + timedelta(days=90))
    assert override.ends_at == now + timedelta(days=90)
    with pytest.raises(ProblemError):  # an override into the past while extending
        activate(customer, plan, ends_at=now - timedelta(days=1))


def test_staff_and_unknown_sources_are_refused(
    staff_user: User, customer: User, plan: Plan
) -> None:
    with pytest.raises(ProblemError):
        activate(staff_user, plan)
    with pytest.raises(ValueError, match="source"):
        activate(customer, plan, source="gift")


def test_the_database_refuses_overlapping_current_subscriptions(customer: User, plan: Plan) -> None:
    subscription = activate(customer, plan)
    with pytest.raises(IntegrityError), transaction.atomic():
        Subscription.objects.create(
            user=customer,
            plan=plan,
            plan_snapshot=subscription.plan_snapshot,
            status=SubscriptionStatus.GRACE,
            starts_at=subscription.starts_at + timedelta(days=1),
            ends_at=subscription.ends_at + timedelta(days=1),
            source=SubscriptionSource.MANUAL,
        )
    # Ended and pending rows may overlap.
    Subscription.objects.create(
        user=customer,
        plan=plan,
        plan_snapshot=subscription.plan_snapshot,
        status=SubscriptionStatus.EXPIRED,
        starts_at=subscription.starts_at,
        ends_at=subscription.ends_at,
        source=SubscriptionSource.MANUAL,
    )


# --- Admin changes ----------------------------------------------------------------------------


def test_extend_adds_days_from_the_end(customer: User, plan: Plan, owner: User) -> None:
    subscription = activate(customer, plan)
    end = subscription.ends_at
    extended = subscriptions.extend(subscription, 10, actor=owner)
    assert extended.ends_at - end == timedelta(days=10)
    entry = AuditLog.objects.get(action="subscription.extend")
    assert (entry.after or {})["days"] == 10
    with pytest.raises(ProblemError):
        subscriptions.extend(subscription, 0, actor=owner)


def test_extend_reopens_an_ended_subscription_from_now(customer: User, plan: Plan) -> None:
    subscription = subscriptions.cancel(activate(customer, plan), actor=None)
    reopened = subscriptions.extend(subscription, 5, actor=None)
    assert reopened.status == SubscriptionStatus.ACTIVE
    assert reopened.ended_at is None
    assert reopened.end_reason == ""
    assert timedelta(days=4) < reopened.ends_at - timezone.now() <= timedelta(days=5, seconds=5)


def test_extend_refuses_an_ended_one_while_another_is_current(customer: User, plan: Plan) -> None:
    old = subscriptions.cancel(activate(customer, plan), actor=None)
    activate(customer, plan)
    with pytest.raises(ProblemError) as excinfo:
        subscriptions.extend(old, 5, actor=None)
    assert excinfo.value.problem_code == ErrorCode.CONFLICT


def test_extend_a_pending_one_moves_its_end(customer: User, plan: Plan) -> None:
    pending = activate(customer, plan, starts_at=timezone.now() + timedelta(days=3))
    end = pending.ends_at
    assert subscriptions.extend(pending, 2, actor=None).ends_at == end + timedelta(days=2)


def test_change_plan_keeps_the_dates(
    customer: User, plan: Plan, make_plan: PlanFactory, django_capture_on_commit_callbacks: Capture
) -> None:
    subscription = activate(customer, plan)
    premium = make_plan(code="premium", max_streams=4)
    with django_capture_on_commit_callbacks(execute=True):
        changed = subscriptions.change_plan(subscription, premium, actor=None)
    assert (changed.starts_at, changed.ends_at) == (subscription.starts_at, subscription.ends_at)
    assert changed.plan_snapshot["code"] == "premium"
    assert current(customer)["max_streams"] == 4
    subscriptions.cancel(changed, actor=None)
    with pytest.raises(ProblemError):
        subscriptions.change_plan(changed, plan, actor=None)


def test_cancel_ends_now_and_stops_sessions(
    customer: User, plan: Plan, django_capture_on_commit_callbacks: Capture
) -> None:
    stopped: list[Any] = []
    access_expired.connect(lambda **kw: stopped.append(kw["user_id"]), weak=False, dispatch_uid="t")
    try:
        with django_capture_on_commit_callbacks(execute=True):
            subscription = subscriptions.cancel(activate(customer, plan), actor=None, note="fraud")
    finally:
        access_expired.disconnect(dispatch_uid="t")
    assert subscription.status == SubscriptionStatus.CANCELLED
    assert subscription.ended_at is not None
    assert stopped == [customer.pk]
    assert current(customer)["status"] == "expired"
    assert (AuditLog.objects.get(action="subscription.cancel").after or {})["note"] == "fraud"
    with pytest.raises(ProblemError):
        subscriptions.cancel(subscription, actor=None)


def test_suspend_and_resume(
    customer: User, plan: Plan, django_capture_on_commit_callbacks: Capture
) -> None:
    stopped: list[Any] = []
    access_suspended.connect(
        lambda **kw: stopped.append(kw["user_id"]), weak=False, dispatch_uid="t"
    )
    try:
        with django_capture_on_commit_callbacks(execute=True):
            subscription = subscriptions.suspend(
                activate(customer, plan), actor=None, reason="chargeback"
            )
    finally:
        access_suspended.disconnect(dispatch_uid="t")
    assert subscription.status == SubscriptionStatus.SUSPENDED
    assert stopped == [customer.pk]
    assert current(customer)["status"] == "suspended"
    with pytest.raises(ProblemError):
        subscriptions.suspend(subscription, actor=None)
    assert subscriptions.resume(subscription, actor=None).status == SubscriptionStatus.ACTIVE
    with pytest.raises(ProblemError):
        subscriptions.resume(subscription, actor=None)


@pytest.mark.parametrize(
    ("days_ago", "expected"),
    [(1, SubscriptionStatus.GRACE), (10, SubscriptionStatus.EXPIRED)],
)
def test_resume_after_the_end(
    customer: User, plan: Plan, days_ago: int, expected: SubscriptionStatus
) -> None:
    subscription = subscriptions.suspend(activate(customer, plan), actor=None)
    shift(subscription, days=30 + days_ago)
    assert subscriptions.resume(subscription, actor=None).status == expected


# --- Trials -----------------------------------------------------------------------------------


def test_an_admin_starts_a_trial_at_once(customer: User, trial_plan: Plan, owner: User) -> None:
    subscription = subscriptions.request_trial(customer, trial_plan, actor=owner)
    assert subscription.status == SubscriptionStatus.ACTIVE
    assert subscription.source == SubscriptionSource.TRIAL
    assert subscription.ends_at - subscription.starts_at == timedelta(hours=24)
    assert subscription.grace_days == 0
    assert subscription.trial_identity == customer.email


def test_a_customer_request_waits_for_approval(
    customer: User, trial_plan: Plan, owner: User
) -> None:
    request = subscriptions.request_trial(customer, trial_plan)
    assert request.status == SubscriptionStatus.PENDING
    assert subscriptions.request_trial(customer, trial_plan) == request  # asking again
    subscriptions.advance_states(now=timezone.now() + timedelta(days=1))  # never auto-starts
    request.refresh_from_db()
    assert request.status == SubscriptionStatus.PENDING
    approved = subscriptions.approve_trial(request, actor=owner)
    assert approved.status == SubscriptionStatus.ACTIVE
    with pytest.raises(ProblemError):
        subscriptions.approve_trial(approved, actor=owner)


def test_without_approval_a_customer_trial_starts_at_once(customer: User, trial_plan: Plan) -> None:
    configure("trials.require_approval", False)
    assert subscriptions.request_trial(customer, trial_plan).status == SubscriptionStatus.ACTIVE


def test_trials_are_limited_per_phone_and_email(
    make_customer: CustomerFactory, trial_plan: Plan, owner: User
) -> None:
    first = make_customer()
    first.phone = "+966501234567"
    first.save()
    subscriptions.request_trial(first, trial_plan, actor=owner)
    second = make_customer()
    second.phone = "+966501234567"
    second.save()
    with pytest.raises(ProblemError) as excinfo:
        subscriptions.request_trial(second, trial_plan, actor=owner)
    assert excinfo.value.problem_code == ErrorCode.TRIAL_NOT_ELIGIBLE
    # The plan's own limit wins over the setting.
    trial_plan.trial_limit_per_phone = 2
    trial_plan.save()
    assert subscriptions.request_trial(second, trial_plan, actor=owner).status == "active"


def test_rejected_requests_do_not_count(customer: User, trial_plan: Plan, owner: User) -> None:
    request = subscriptions.request_trial(customer, trial_plan)
    subscriptions.cancel(request, reason=EndReason.REJECTED, actor=owner)
    assert subscriptions.trials_used(customer) == 0
    assert subscriptions.request_trial(customer, trial_plan, actor=owner).status == "active"


def test_trials_are_refused_to_subscribers_and_for_paid_plans(
    customer: User, plan: Plan, trial_plan: Plan
) -> None:
    with pytest.raises(ProblemError):
        subscriptions.request_trial(customer, plan)
    activate(customer, plan)
    with pytest.raises(ProblemError) as excinfo:
        subscriptions.request_trial(customer, trial_plan)
    assert excinfo.value.problem_code == ErrorCode.TRIAL_NOT_ELIGIBLE


def test_approval_needs_no_current_subscription(
    customer: User, plan: Plan, trial_plan: Plan, owner: User
) -> None:
    request = subscriptions.request_trial(customer, trial_plan)
    activate(customer, plan)
    with pytest.raises(ProblemError):
        subscriptions.approve_trial(request, actor=owner)


# --- The state job ----------------------------------------------------------------------------


def test_active_to_grace_to_expired(
    customer: User, plan: Plan, django_capture_on_commit_callbacks: Capture
) -> None:
    subscription = activate(customer, plan)
    stopped: list[Any] = []
    access_expired.connect(lambda **kw: stopped.append(kw["user_id"]), weak=False, dispatch_uid="t")
    try:
        end = subscription.ends_at
        counts = subscriptions.advance_states(now=end + timedelta(minutes=1))
        subscription.refresh_from_db()
        assert (counts.grace, counts.expired) == (1, 0)
        assert subscription.status == SubscriptionStatus.GRACE
        assert stopped == []  # grace still plays
        with django_capture_on_commit_callbacks(execute=True):
            counts = subscriptions.advance_states(now=end + timedelta(days=3, minutes=1))
        subscription.refresh_from_db()
        assert counts.expired == 1
        assert subscription.status == SubscriptionStatus.EXPIRED
        assert subscription.end_reason == EndReason.EXPIRED
        assert stopped == [customer.pk]
    finally:
        access_expired.disconnect(dispatch_uid="t")
    assert len(events("expired")) == 1
    assert AuditLog.objects.filter(action="subscription.expire").count() == 1
    # Running again changes nothing.
    assert subscriptions.advance_states(now=end + timedelta(days=4)).expired == 0


def test_without_grace_days_expiry_is_immediate(customer: User, plan: Plan) -> None:
    configure("billing.grace_days", 0)
    subscription = activate(customer, plan)
    counts = subscriptions.advance_states(now=subscription.ends_at + timedelta(seconds=1))
    assert (counts.grace, counts.expired) == (0, 1)


def test_reminders_once_per_period(customer: User, plan: Plan) -> None:
    subscription = activate(customer, plan)
    end = subscription.ends_at
    assert subscriptions.advance_states(now=end - timedelta(days=8)).reminded == 0
    assert subscriptions.advance_states(now=end - timedelta(days=6)).reminded == 1
    assert subscriptions.advance_states(now=end - timedelta(days=5)).reminded == 0
    assert subscriptions.advance_states(now=end - timedelta(hours=20)).reminded == 1
    assert [
        row.template_key
        for row in NotificationOutbox.objects.filter(template_key__startswith="expiring").order_by(
            "created_at"
        )
    ] == ["expiring_7d", "expiring_1d"]
    # Renewing re-arms them for the new end.
    renewed = activate(customer, plan)
    assert renewed.reminded_7d_at is None
    assert subscriptions.advance_states(now=renewed.ends_at - timedelta(days=6)).reminded == 1


def test_a_missed_7_day_reminder_is_not_sent_after_the_1_day_one(
    customer: User, plan: Plan
) -> None:
    subscription = activate(customer, plan)
    subscriptions.advance_states(now=subscription.ends_at - timedelta(hours=12))
    assert [
        row.template_key
        for row in NotificationOutbox.objects.filter(template_key__startswith="expiring")
    ] == ["expiring_1d"]
    subscription.refresh_from_db()
    assert subscription.reminded_7d_at is not None


def test_short_periods_get_no_reminders(customer: User, trial_plan: Plan, owner: User) -> None:
    trial = subscriptions.request_trial(customer, trial_plan, actor=owner)
    assert subscriptions.advance_states(now=trial.ends_at - timedelta(hours=1)).reminded == 0


def test_a_failing_row_does_not_stop_the_others(
    make_customer: CustomerFactory, plan: Plan, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = activate(make_customer(), plan)
    second = activate(make_customer(), plan)
    original = subscriptions._to_grace

    def flaky(subscription: Subscription) -> None:
        if subscription.pk == first.pk:
            raise RuntimeError("boom")
        original(subscription)

    monkeypatch.setattr(subscriptions, "_to_grace", flaky)
    counts = subscriptions.advance_states(now=second.ends_at + timedelta(minutes=1))
    assert counts.grace == 1
    first.refresh_from_db()
    second.refresh_from_db()
    assert (first.status, second.status) == ("active", "grace")


def test_the_gauge_counts_subscriptions_by_status(customer: User, plan: Plan) -> None:
    activate(customer, plan)
    subscriptions.advance_states()
    samples = {
        sample.labels["status"]: sample.value
        for family in SUBSCRIPTIONS.collect()
        for sample in family.samples
    }
    assert samples["active"] == 1
    assert samples["expired"] == 0


def test_user_suspension_wins_over_the_subscription(customer: User, plan: Plan) -> None:
    activate(customer, plan)
    User.objects.filter(pk=customer.pk).update(status=UserStatus.SUSPENDED)
    assert current(customer, refresh=True)["status"] == "suspended"
