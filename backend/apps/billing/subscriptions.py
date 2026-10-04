"""Subscriptions: the only code that changes them (SPEC §7.6, ADR-0012).

Every function runs in a transaction that locks the customer's row, so changes to
one customer's subscriptions are serial. Each change snapshots the plan when it
assigns one, records an audit entry, schedules the entitlement rebuild and the
notifications for after the commit, and sends the session-stopping signals
(`apps.accounts.signals`) when access ends.

- `activate` creates or extends: the new end is `max(now, current end) + period`
  (`apps.billing.renewal`). A customer has at most one current (active, grace or
  suspended) subscription; renewals and plan purchases extend it.
- `advance_states` (beat, every 5 minutes) starts pending subscriptions, moves
  active → grace → expired, sends T-7 and T-1 day reminders once per period, and
  publishes `iptv_subscriptions{status}`.
- Trials last `trials.duration_hours`, have no grace, and are limited per phone
  number or email (`trials.limit_per_phone`, or the plan's own limit). With
  `trials.require_approval`, a customer's request waits for an admin.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from functools import partial
from typing import Any, Final

from django.db import transaction
from django.db.models import (
    Count,
    DateTimeField,
    DurationField,
    ExpressionWrapper,
    F,
    Q,
    QuerySet,
)
from django.utils import timezone

from apps.accounts.models import User, XtreamCredential
from apps.accounts.signals import access_expired, access_suspended
from apps.audit import services as audit
from apps.billing import renewal
from apps.billing.models import (
    CURRENT_STATUSES,
    EndReason,
    Payment,
    Plan,
    Subscription,
    SubscriptionSource,
    SubscriptionStatus,
    plan_snapshot,
)
from apps.core.errors import ErrorCode, ProblemError, field_error
from apps.core.metrics import SUBSCRIPTIONS
from apps.core.services import get_setting
from apps.notifications import services as notifications
from apps.playback import entitlements

logger = logging.getLogger(__name__)

STATE_BATCH: Final = 500
MAX_EXTEND_DAYS: Final = 3660
REMINDERS: Final = (("expiring_1d", timedelta(days=1)), ("expiring_7d", timedelta(days=7)))


# --- Snapshots and helpers ------------------------------------------------------------------


def snapshot_of(plan: Plan) -> dict[str, Any]:
    return plan_snapshot(plan, [str(pk) for pk in plan.categories.values_list("pk", flat=True)])


def audit_snapshot(subscription: Subscription) -> dict[str, Any]:
    return {
        "user_id": str(subscription.user_id),
        "plan": subscription.plan_snapshot.get("code"),
        "plan_version": subscription.plan_snapshot.get("version"),
        "status": subscription.status,
        "starts_at": subscription.starts_at,
        "ends_at": subscription.ends_at,
        "grace_days": subscription.grace_days,
        "source": subscription.source,
        "end_reason": subscription.end_reason,
    }


def _changes(before: dict[str, Any], after: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    keys = [key for key in after if before.get(key) != after.get(key)]
    return {key: before.get(key) for key in keys}, {key: after.get(key) for key in keys}


def _tz(user: User) -> tzinfo:
    return renewal.zone(user.timezone)


def period_end(plan: Plan, start: datetime, tz: tzinfo) -> datetime:
    """When a period of `plan` bought at `start` ends."""
    if plan.is_trial:
        return renewal.trial_end(start, int(get_setting("trials.duration_hours")))
    return renewal.add_period(start, months=plan.duration_months, days=plan.duration_days, tz=tz)


def _grace_days(plan: Plan) -> int:
    return 0 if plan.is_trial else int(get_setting("billing.grace_days"))


def _lock_user(user: User) -> User:
    return User.objects.select_for_update().get(pk=user.pk)


def current_subscription(user: User, *, lock: bool = False) -> Subscription | None:
    """The customer's current (active, grace or suspended) subscription."""
    rows = Subscription.objects.filter(user=user, status__in=CURRENT_STATUSES)
    if lock:
        rows = rows.select_for_update()
    return rows.select_related("plan").order_by("-ends_at").first()


def _locked(subscription: Subscription) -> Subscription:
    return Subscription.objects.select_for_update().select_related("plan").get(pk=subscription.pk)


def _plan_name(subscription: Subscription, locale: str) -> str:
    snapshot = subscription.plan_snapshot
    return str(snapshot.get(f"name_{locale}") or snapshot.get("name_en") or "")


def notify(
    user: User, key: str, subscription: Subscription, *, dedupe_key: str | None = None, **extra: Any
) -> None:
    """Queue `key` for the subscriber with the subscription's variables."""
    locale = user.locale if user.locale in ("ar", "en") else "ar"
    payload: dict[str, Any] = {
        "plan_name": _plan_name(subscription, locale),
        "ends_at": notifications.local_time(subscription.ends_at, user),
        **extra,
    }
    notifications.enqueue(user, key, payload, dedupe_key=dedupe_key)


def _activation_extras(user: User) -> dict[str, Any]:
    from apps.accounts.services import xtream_server_url  # noqa: PLC0415 (import cycle)

    usernames = list(
        XtreamCredential.objects.filter(
            device__user=user, revoked_at__isnull=True, device__revoked_at__isnull=True
        )
        .order_by("created_at")
        .values_list("username", flat=True)
    )
    return {"server_url": xtream_server_url(), "usernames": usernames}


def _on_commit(callback: Callable[[], object]) -> None:
    transaction.on_commit(callback, robust=True)


def _signal_expired(subscription: Subscription) -> None:
    _on_commit(partial(access_expired.send, sender=Subscription, user_id=subscription.user_id))


def _signal_suspended(subscription: Subscription) -> None:
    _on_commit(partial(access_suspended.send, sender=Subscription, user_id=subscription.user_id))


def _refuse_staff(user: User) -> None:
    if user.is_staff:
        raise ProblemError(
            ErrorCode.VALIDATION_ERROR,
            field_errors={
                "user_id": [field_error("Subscriptions are for customers.", code="not_customer")]
            },
        )


# --- Activation -----------------------------------------------------------------------------


def activate(  # noqa: PLR0913 (the SPEC §7.6 signature plus an admin's end-date override)
    user: User,
    plan: Plan,
    *,
    source: str,
    actor: User | None = None,
    payment: Payment | None = None,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    ip: str | None = None,
    note: str = "",
) -> Subscription:
    """Create or extend the customer's subscription (SPEC §7.6); the only way in.

    With a current subscription, it is extended to `max(now, its end) + period`
    and moves to `plan` (a new snapshot); a suspended one stays suspended. Else a
    new one starts at `starts_at` (default now; later means pending). `ends_at`
    overrides the computed end (admins, imports).
    """
    _refuse_staff(user)
    if source not in SubscriptionSource.values:
        msg = f"unknown subscription source {source!r}"
        raise ValueError(msg)
    with transaction.atomic():
        user = _lock_user(user)
        now = timezone.now()
        current = current_subscription(user, lock=True)
        before: dict[str, Any] | None = None
        if current is not None:
            before = audit_snapshot(current)
            subscription = _extend_current(
                current, plan, now=now, ends_at=ends_at, starts_at=starts_at
            )
            action = "subscription.renew"
        else:
            subscription = _create(
                user,
                plan,
                source=source,
                actor=actor,
                now=now,
                starts_at=starts_at,
                ends_at=ends_at,
            )
            action = "subscription.activate"
        if note:
            subscription.note = note[:200]
            subscription.save(update_fields=["note", "updated_at"])
        if payment is not None and payment.subscription_id != subscription.pk:
            payment.subscription = subscription
            payment.save(update_fields=["subscription", "updated_at"])
        after = {**audit_snapshot(subscription), "payment_id": str(payment.pk) if payment else None}
        audit.record(action, actor=actor, target=subscription, before=before, after=after, ip=ip)
        entitlements.schedule_refresh(user.pk)
        _notify_activation(
            user, subscription, renewed=current is not None, paid=payment is not None
        )
    return subscription


def _extend_current(
    current: Subscription,
    plan: Plan,
    *,
    now: datetime,
    ends_at: datetime | None,
    starts_at: datetime | None,
) -> Subscription:
    if starts_at is not None and starts_at > now:
        raise ProblemError(
            ErrorCode.CONFLICT,
            "This customer has a current subscription; it is extended from its end instead.",
        )
    tz = _tz(current.user)
    new_end = ends_at or period_end(plan, max(now, current.ends_at), tz)
    if new_end <= now:
        raise ProblemError(
            ErrorCode.VALIDATION_ERROR,
            field_errors={"ends_at": [field_error("Must be in the future.", code="past")]},
        )
    current.plan = plan
    current.plan_snapshot = snapshot_of(plan)
    current.ends_at = new_end
    current.grace_days = _grace_days(plan)
    current.reminded_7d_at = None
    current.reminded_1d_at = None
    if current.status == SubscriptionStatus.GRACE:
        current.status = SubscriptionStatus.ACTIVE
    current.save()
    return current


def _create(  # noqa: PLR0913 (keyword-only parts of one subscription)
    user: User,
    plan: Plan,
    *,
    source: str,
    actor: User | None,
    now: datetime,
    starts_at: datetime | None,
    ends_at: datetime | None,
) -> Subscription:
    start = starts_at or now
    end = ends_at or period_end(plan, start, _tz(user))
    if end <= start:
        raise ProblemError(
            ErrorCode.VALIDATION_ERROR,
            field_errors={
                "ends_at": [field_error("Must be after the start.", code="before_start")]
            },
        )
    status = SubscriptionStatus.PENDING if start > now else SubscriptionStatus.ACTIVE
    ended: dict[str, Any] = {}
    if status == SubscriptionStatus.ACTIVE and end <= now:  # an imported past period
        status = SubscriptionStatus.EXPIRED
        ended = {"ended_at": end, "end_reason": EndReason.EXPIRED}
    return Subscription.objects.create(
        user=user,
        plan=plan,
        plan_snapshot=snapshot_of(plan),
        status=status,
        starts_at=start,
        ends_at=end,
        grace_days=_grace_days(plan),
        source=source,
        created_by=actor,
        trial_identity=trial_identity(user) if plan.is_trial else "",
        **ended,
    )


def _notify_activation(
    user: User, subscription: Subscription, *, renewed: bool, paid: bool
) -> None:
    if subscription.status not in (SubscriptionStatus.ACTIVE, SubscriptionStatus.GRACE):
        return
    if renewed:
        if not paid:  # a payment announces itself (payment_succeeded)
            notify(user, "subscription_renewed", subscription)
        return
    notify(user, "account_activated", subscription, **_activation_extras(user))


# --- Admin changes --------------------------------------------------------------------------


def _record(  # noqa: PLR0913 (keyword-only context of one audit entry)
    action: str,
    subscription: Subscription,
    before: dict[str, Any],
    *,
    actor: User | None,
    ip: str | None,
    extra: dict[str, Any] | None = None,
) -> None:
    old, new = _changes(before, audit_snapshot(subscription))
    audit.record(
        action,
        actor=actor,
        target=subscription,
        before=old,
        after={**new, **(extra or {})},
        ip=ip,
    )
    entitlements.schedule_refresh(subscription.user_id)


def extend(
    subscription: Subscription,
    days: int,
    *,
    actor: User | None,
    ip: str | None = None,
) -> Subscription:
    """Add `days` to the period: from `max(now, end)` for a running or ended
    subscription, which an ended one reopens, or from the end of a pending one."""
    if not 1 <= days <= MAX_EXTEND_DAYS:
        raise ProblemError(
            ErrorCode.VALIDATION_ERROR,
            field_errors={
                "days": [field_error(f"Use 1 to {MAX_EXTEND_DAYS} days.", code="out_of_range")]
            },
        )
    with transaction.atomic():
        user = _lock_user(subscription.user)
        subscription = _locked(subscription)
        before = audit_snapshot(subscription)
        now = timezone.now()
        tz = _tz(user)
        if subscription.status == SubscriptionStatus.PENDING:
            subscription.ends_at = renewal.add_period(subscription.ends_at, days=days, tz=tz)
        else:
            # Access ended at `ended_at` (a cancellation may end it before `ends_at`).
            end = min(subscription.ends_at, subscription.ended_at or subscription.ends_at)
            if subscription.status not in CURRENT_STATUSES:
                current = current_subscription(user)
                if current is not None:
                    raise ProblemError(
                        ErrorCode.CONFLICT,
                        "This customer has another current subscription; extend that one.",
                    )
                subscription.status = SubscriptionStatus.ACTIVE
                subscription.ended_at = None
                subscription.end_reason = ""
            subscription.ends_at = renewal.extend_from(now, end, months=0, days=days, tz=tz)
            if subscription.status == SubscriptionStatus.GRACE:
                subscription.status = SubscriptionStatus.ACTIVE
        subscription.reminded_7d_at = None
        subscription.reminded_1d_at = None
        subscription.save()
        _record(
            "subscription.extend", subscription, before, actor=actor, ip=ip, extra={"days": days}
        )
        if subscription.status == SubscriptionStatus.ACTIVE:
            notify(user, "subscription_renewed", subscription)
    return subscription


def change_plan(
    subscription: Subscription, plan: Plan, *, actor: User | None, ip: str | None = None
) -> Subscription:
    """Move the subscription to `plan` (a new snapshot); its dates stay."""
    with transaction.atomic():
        _lock_user(subscription.user)
        subscription = _locked(subscription)
        if subscription.status not in (*CURRENT_STATUSES, SubscriptionStatus.PENDING):
            raise ProblemError(ErrorCode.CONFLICT, "Ended subscriptions keep their plan.")
        before = audit_snapshot(subscription)
        subscription.plan = plan
        subscription.plan_snapshot = snapshot_of(plan)
        subscription.save(update_fields=["plan", "plan_snapshot", "updated_at"])
        _record("subscription.change_plan", subscription, before, actor=actor, ip=ip)
    return subscription


def _end(subscription: Subscription, reason: EndReason, now: datetime) -> bool:
    """Mark ended; True when it held the customer's access until now."""
    held = subscription.status in CURRENT_STATUSES
    subscription.status = (
        SubscriptionStatus.EXPIRED if reason == EndReason.EXPIRED else SubscriptionStatus.CANCELLED
    )
    subscription.ended_at = now
    subscription.end_reason = reason
    subscription.save(update_fields=["status", "ended_at", "end_reason", "updated_at"])
    return held


def cancel(
    subscription: Subscription,
    *,
    reason: EndReason = EndReason.CANCELLED,
    actor: User | None,
    ip: str | None = None,
    note: str = "",
) -> Subscription:
    """End the subscription now; playback stops."""
    with transaction.atomic():
        _lock_user(subscription.user)
        subscription = _locked(subscription)
        if subscription.status not in (*CURRENT_STATUSES, SubscriptionStatus.PENDING):
            raise ProblemError(ErrorCode.CONFLICT, "The subscription has already ended.")
        before = audit_snapshot(subscription)
        held = _end(subscription, reason, timezone.now())
        _record(
            "subscription.cancel",
            subscription,
            before,
            actor=actor,
            ip=ip,
            extra={"note": note} if note else None,
        )
        if held:
            _signal_expired(subscription)
    return subscription


def suspend(
    subscription: Subscription, *, actor: User | None, ip: str | None = None, reason: str = ""
) -> Subscription:
    """Stop playback, keeping the period (e.g. a payment dispute); `resume` undoes it."""
    with transaction.atomic():
        _lock_user(subscription.user)
        subscription = _locked(subscription)
        if subscription.status not in (SubscriptionStatus.ACTIVE, SubscriptionStatus.GRACE):
            raise ProblemError(ErrorCode.CONFLICT, "Only running subscriptions can be suspended.")
        before = audit_snapshot(subscription)
        subscription.status = SubscriptionStatus.SUSPENDED
        subscription.save(update_fields=["status", "updated_at"])
        _record(
            "subscription.suspend",
            subscription,
            before,
            actor=actor,
            ip=ip,
            extra={"reason": reason} if reason else None,
        )
        _signal_suspended(subscription)
    return subscription


def resume(
    subscription: Subscription, *, actor: User | None, ip: str | None = None
) -> Subscription:
    """Undo `suspend`: active, in grace or expired, depending on the dates."""
    with transaction.atomic():
        _lock_user(subscription.user)
        subscription = _locked(subscription)
        if subscription.status != SubscriptionStatus.SUSPENDED:
            raise ProblemError(ErrorCode.CONFLICT, "The subscription is not suspended.")
        before = audit_snapshot(subscription)
        now = timezone.now()
        grace_end = subscription.ends_at + timedelta(days=subscription.grace_days)
        if subscription.ends_at > now:
            subscription.status = SubscriptionStatus.ACTIVE
            subscription.save(update_fields=["status", "updated_at"])
        elif grace_end > now:
            subscription.status = SubscriptionStatus.GRACE
            subscription.save(update_fields=["status", "updated_at"])
        else:
            _end(subscription, EndReason.EXPIRED, now)
        _record("subscription.resume", subscription, before, actor=actor, ip=ip)
    return subscription


# --- Trials ---------------------------------------------------------------------------------


def trial_identity(user: User) -> str:
    """What a trial is counted against: the phone number, else the email."""
    return user.phone or user.email.lower()


def trial_limit(plan: Plan) -> int:
    if plan.trial_limit_per_phone is not None:
        return plan.trial_limit_per_phone
    return int(get_setting("trials.limit_per_phone"))


def trials_used(user: User) -> int:
    identities = [value for value in (user.phone, user.email.lower()) if value]
    return (
        Subscription.objects.filter(source=SubscriptionSource.TRIAL)
        .exclude(end_reason=EndReason.REJECTED)
        .filter(Q(user=user) | Q(trial_identity__in=identities))
        .count()
    )


def _trial_refusal(message: str) -> ProblemError:
    return ProblemError(ErrorCode.TRIAL_NOT_ELIGIBLE, message)


def request_trial(
    user: User, plan: Plan, *, actor: User | None = None, ip: str | None = None
) -> Subscription:
    """A customer asks for the trial plan (or an admin starts one for them).

    Refused with TRIAL_NOT_ELIGIBLE past the limit per phone or email, or while the
    customer has a current subscription. A customer's request waits for an admin
    when `trials.require_approval` is on; an admin's starts at once.
    """
    _refuse_staff(user)
    if not plan.is_trial:
        raise ProblemError(
            ErrorCode.VALIDATION_ERROR,
            field_errors={"plan_id": [field_error("Not a trial plan.", code="not_trial")]},
        )
    with transaction.atomic():
        user = _lock_user(user)
        waiting = Subscription.objects.filter(
            user=user, source=SubscriptionSource.TRIAL, status=SubscriptionStatus.PENDING
        ).first()
        if waiting is not None:
            return waiting
        if current_subscription(user) is not None:
            raise _trial_refusal("Trials are for customers without a current subscription.")
        if trials_used(user) >= trial_limit(plan):
            raise _trial_refusal("The free trial has already been used.")
        if actor is None and get_setting("trials.require_approval") is True:
            now = timezone.now()
            subscription = Subscription.objects.create(
                user=user,
                plan=plan,
                plan_snapshot=snapshot_of(plan),
                status=SubscriptionStatus.PENDING,
                starts_at=now,
                ends_at=period_end(plan, now, _tz(user)),
                source=SubscriptionSource.TRIAL,
                trial_identity=trial_identity(user),
            )
            audit.record(
                "subscription.trial_request",
                actor=None,
                target=subscription,
                after=audit_snapshot(subscription),
                ip=ip,
            )
            return subscription
        return activate(user, plan, source=SubscriptionSource.TRIAL, actor=actor, ip=ip)


def approve_trial(
    subscription: Subscription, *, actor: User | None, ip: str | None = None
) -> Subscription:
    """Start a waiting trial request now."""
    with transaction.atomic():
        user = _lock_user(subscription.user)
        subscription = _locked(subscription)
        if (
            subscription.status != SubscriptionStatus.PENDING
            or subscription.source != SubscriptionSource.TRIAL
        ):
            raise ProblemError(ErrorCode.CONFLICT, "This is not a trial request.")
        if current_subscription(user) is not None:
            raise ProblemError(ErrorCode.CONFLICT, "The customer already has a subscription.")
        before = audit_snapshot(subscription)
        now = timezone.now()
        subscription.starts_at = now
        subscription.ends_at = period_end(subscription.plan, now, _tz(user))
        subscription.status = SubscriptionStatus.ACTIVE
        subscription.created_by = actor
        subscription.save()
        _record("subscription.trial_approve", subscription, before, actor=actor, ip=ip)
        notify(user, "account_activated", subscription, **_activation_extras(user))
    return subscription


# --- The state job ---------------------------------------------------------------------------


@dataclass(slots=True)
class StateCounts:
    started: int = 0
    grace: int = 0
    expired: int = 0
    reminded: int = 0


def _batches(
    rows: Callable[[], QuerySet[Subscription]], handle: Callable[[Subscription], None]
) -> int:
    """Lock and handle rows in batches, skipping rows another run holds.

    Each row gets a savepoint: one that fails is logged and left for the next run,
    and the others go ahead.
    """
    total = 0
    failed: set[object] = set()
    while True:
        with transaction.atomic():
            batch = list(
                rows()
                .exclude(pk__in=failed)
                .select_for_update(skip_locked=True, of=("self",))
                .select_related("user")[:STATE_BATCH]
            )
            for subscription in batch:
                try:
                    with transaction.atomic():
                        handle(subscription)
                except Exception:
                    logger.exception("subscription %s: state change failed", subscription.pk)
                    failed.add(subscription.pk)
                else:
                    total += 1
        if len(batch) < STATE_BATCH:
            return total


def _start_pending(now: datetime) -> Callable[[Subscription], None]:
    def handle(subscription: Subscription) -> None:
        user = _lock_user(subscription.user)
        before = audit_snapshot(subscription)
        current = current_subscription(user, lock=True)
        if current is not None:  # bought or extended meanwhile: add this period to it
            current_before = audit_snapshot(current)
            current.ends_at = max(now, current.ends_at) + (
                subscription.ends_at - subscription.starts_at
            )
            current.reminded_7d_at = current.reminded_1d_at = None
            if current.status == SubscriptionStatus.GRACE:
                current.status = SubscriptionStatus.ACTIVE
            current.save()
            _record("subscription.renew", current, current_before, actor=None, ip=None)
            _end(subscription, EndReason.MERGED, now)
            _record("subscription.merge", subscription, before, actor=None, ip=None)
            return
        if subscription.ends_at <= now:
            _end(subscription, EndReason.EXPIRED, now)
        else:
            subscription.status = SubscriptionStatus.ACTIVE
            subscription.save(update_fields=["status", "updated_at"])
            notify(user, "account_activated", subscription, **_activation_extras(user))
        _record("subscription.start", subscription, before, actor=None, ip=None)

    return handle


def _to_grace(subscription: Subscription) -> None:
    before = audit_snapshot(subscription)
    subscription.status = SubscriptionStatus.GRACE
    subscription.save(update_fields=["status", "updated_at"])
    _record("subscription.grace", subscription, before, actor=None, ip=None)


def _expire(now: datetime) -> Callable[[Subscription], None]:
    def handle(subscription: Subscription) -> None:
        before = audit_snapshot(subscription)
        _end(subscription, EndReason.EXPIRED, now)
        _record("subscription.expire", subscription, before, actor=None, ip=None)
        _signal_expired(subscription)
        notify(
            subscription.user,
            "expired",
            subscription,
            dedupe_key=f"expired:{subscription.pk}:{subscription.ends_at.isoformat()}",
        )

    return handle


def _grace_end() -> "ExpressionWrapper[Any]":
    return ExpressionWrapper(
        F("ends_at") + F("grace_days") * timedelta(days=1), output_field=DateTimeField()
    )


def _remind(key: str, window: timedelta, now: datetime) -> int:
    field = "reminded_1d_at" if key == "expiring_1d" else "reminded_7d_at"

    def rows() -> QuerySet[Subscription]:
        return (
            Subscription.objects.filter(
                status=SubscriptionStatus.ACTIVE,
                ends_at__gt=now,
                ends_at__lte=now + window,
                **{f"{field}__isnull": True},
            )
            .alias(
                length=ExpressionWrapper(
                    F("ends_at") - F("starts_at"), output_field=DurationField()
                )
            )
            .filter(length__gt=window)
            .order_by("ends_at")
        )

    def handle(subscription: Subscription) -> None:
        stamp = {field: now}
        if key == "expiring_1d":  # a missed T-7 reminder is not sent after the T-1 one
            stamp["reminded_7d_at"] = subscription.reminded_7d_at or now
        Subscription.objects.filter(pk=subscription.pk).update(**stamp, updated_at=now)
        notify(
            subscription.user,
            key,
            subscription,
            dedupe_key=f"{key}:{subscription.pk}:{subscription.ends_at.isoformat()}",
        )

    return _batches(rows, handle)


def advance_states(*, now: datetime | None = None) -> StateCounts:
    """The 5-minute job: start, grace, expire and remind; then publish the gauge."""
    moment = now or timezone.now()
    counts = StateCounts()
    counts.started = _batches(
        lambda: (
            Subscription.objects.filter(status=SubscriptionStatus.PENDING, starts_at__lte=moment)
            .exclude(source=SubscriptionSource.TRIAL)
            .order_by("starts_at")
        ),
        _start_pending(moment),
    )
    counts.expired = _batches(
        lambda: (
            Subscription.objects.filter(
                status__in=(SubscriptionStatus.ACTIVE, SubscriptionStatus.GRACE)
            )
            .alias(grace_end=_grace_end())
            .filter(ends_at__lte=moment, grace_end__lte=moment)
            .order_by("ends_at")
        ),
        _expire(moment),
    )
    counts.grace = _batches(
        lambda: Subscription.objects.filter(
            status=SubscriptionStatus.ACTIVE, ends_at__lte=moment
        ).order_by("ends_at"),
        _to_grace,
    )
    counts.reminded = sum(_remind(key, window, moment) for key, window in REMINDERS)
    publish_metrics()
    if counts.started or counts.grace or counts.expired:
        logger.info(
            "subscriptions: %d started, %d in grace, %d expired",
            counts.started,
            counts.grace,
            counts.expired,
        )
    return counts


def status_counts() -> dict[str, int]:
    rows = Subscription.objects.values("status").annotate(count=Count("pk")).order_by()
    counts = dict.fromkeys(SubscriptionStatus.values, 0)
    for row in rows:
        counts[row["status"]] = int(row["count"])
    return counts


def publish_metrics() -> None:
    """`iptv_subscriptions{status}` from the subscriptions table (ADR-0012)."""
    try:
        SUBSCRIPTIONS.publish({(status,): count for status, count in status_counts().items()})
    except Exception:  # metrics must never break the job
        logger.exception("could not publish the subscription metrics")
