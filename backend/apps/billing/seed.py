"""Billing demo data for `make seed` (called from `seed_demo`; idempotent).

The SPEC plans (Basic, Standard, Premium, Trial) and, for some demo customers,
subscriptions across the states the admin shows: paid by bank transfer or cash
(with invoices), active, expiring within 7 days, in grace, expired, a running
trial and a trial request waiting for approval. Everything goes through the
billing services, so audit entries, invoices, entitlements and notifications are
real. Demo customers without a subscription keep their manual access profile.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import ConcurrencyPolicy, MaxQuality, User
from apps.billing import services, subscriptions
from apps.billing.models import PaymentMethod, Plan, Subscription, SubscriptionSource

DEMO_EMAIL_DOMAIN = "demo.smart-iptv.test"


@dataclass(frozen=True, slots=True)
class DemoPlan:
    code: str
    name_en: str
    name_ar: str
    description_en: str
    description_ar: str
    price: int
    max_streams: int
    max_devices: int
    max_quality: int
    policy: str = ConcurrencyPolicy.REJECT
    duration_days: int = 30
    is_trial: bool = False


PLANS: tuple[DemoPlan, ...] = (
    DemoPlan("basic", "Basic", "الأساسية", "One screen in HD.", "شاشة واحدة بدقة HD.",
             2900, 1, 2, MaxQuality.HD),
    DemoPlan("standard", "Standard", "القياسية", "Two screens in Full HD.",
             "شاشتان بدقة Full HD.", 4900, 2, 3, MaxQuality.FHD),
    DemoPlan("premium", "Premium", "المميزة", "Four screens in 4K; the oldest stream makes way.",
             "أربع شاشات بدقة 4K، ويتوقف أقدم بث عند تجاوز الحد.", 7900, 4, 5, MaxQuality.UHD,
             policy=ConcurrencyPolicy.KICK_OLDEST),
    DemoPlan("trial", "Free trial", "تجربة مجانية", "24 hours, one screen in HD.",
             "24 ساعة، شاشة واحدة بدقة HD.", 0, 1, 1, MaxQuality.HD, duration_days=1,
             is_trial=True),
)  # fmt: skip


def _plans() -> dict[str, Plan]:
    by_code: dict[str, Plan] = {}
    for index, demo in enumerate(PLANS):
        plan = Plan.objects.filter(code=demo.code).first()
        if plan is None:
            plan = services.create_plan(
                {
                    "code": demo.code,
                    "name_en": demo.name_en,
                    "name_ar": demo.name_ar,
                    "description_en": demo.description_en,
                    "description_ar": demo.description_ar,
                    "price": demo.price,
                    "currency": "SAR",
                    "duration_days": demo.duration_days,
                    "max_streams": demo.max_streams,
                    "max_devices": demo.max_devices,
                    "max_quality": demo.max_quality,
                    "concurrency_policy": demo.policy,
                    "is_trial": demo.is_trial,
                    "sort": (index + 1) * 10,
                },
                actor=None,
            )
        by_code[demo.code] = plan
    return by_code


def _paid(plan: str, method: str = PaymentMethod.BANK_TRANSFER) -> Callable[..., None]:
    def run(user: User, plans: dict[str, Plan]) -> None:
        services.record_manual_payment(
            user=user,
            plan=plans[plan],
            method=method,
            reference=f"DEMO-{user.username[-6:].upper()}",
            idempotency_key=f"demo-seed-{user.pk}",
            actor=None,
        )

    return run


def _manual(plan: str, days_left: float) -> Callable[..., None]:
    def run(user: User, plans: dict[str, Plan]) -> None:
        subscriptions.activate(
            user,
            plans[plan],
            source=SubscriptionSource.MANUAL,
            ends_at=timezone.now() + timedelta(days=days_left),
            note="Demo data",
        )

    return run


def _ended(plan: str, days_ago: float) -> Callable[..., None]:
    """A subscription that ended `days_ago` days ago: in grace while within grace_days,
    then expired (the state job moves it)."""

    def run(user: User, plans: dict[str, Plan]) -> None:
        now = timezone.now()
        subscription = subscriptions.activate(
            user,
            plans[plan],
            source=SubscriptionSource.IMPORT,
            starts_at=now - timedelta(days=30 + days_ago),
            ends_at=now + timedelta(hours=1),
            note="Demo data",
        )
        # Demo only: move the end into the past for the state job to pick up.
        Subscription.objects.filter(pk=subscription.pk).update(
            ends_at=now - timedelta(days=days_ago)
        )

    return run


def _trial(*, approved: bool) -> Callable[..., None]:
    def run(user: User, plans: dict[str, Plan]) -> None:
        if approved:
            subscriptions.request_trial(user, plans["trial"], actor=user_admin())
        else:
            subscriptions.request_trial(user, plans["trial"])

    return run


def user_admin() -> User | None:
    return User.objects.filter(is_staff=True, username="admin").first()


# Demo customer key (seed_demo's email local part) -> what they have.
SCENARIOS: dict[str, Callable[..., None]] = {
    "mohammed.harbi": _paid("standard"),
    "abdullah.qahtani": _paid("premium"),
    "khalid.dossari": _paid("premium", PaymentMethod.CASH),
    "emily.walsh": _paid("standard"),
    "lina.haddad": _paid("basic"),
    "reem.mutairi": _manual("standard", 40),
    "hessa.subaie": _manual("basic", 3),
    "daniel.reyes": _manual("standard", 6),
    "maha.rashidi": _ended("basic", 1),
    "sophie.martin": _ended("standard", 20),
    "mark.evans": _trial(approved=True),
    "james.carter": _trial(approved=False),
}


def seed_billing() -> str:
    """Create the demo plans and subscriptions that don't exist yet; a summary line."""
    with transaction.atomic():
        plans = _plans()
    created = 0
    for key, scenario in SCENARIOS.items():
        user = User.objects.filter(email=f"{key}@{DEMO_EMAIL_DOMAIN}", is_staff=False).first()
        if user is None or Subscription.objects.filter(user=user).exists():
            continue
        with transaction.atomic():
            scenario(user, plans)
        created += 1
    counts = subscriptions.advance_states()
    return (
        f"Billing demo data ready: {len(plans)} plans, {created} new subscriptions "
        f"({counts.grace} in grace, {counts.expired} expired)."
    )
