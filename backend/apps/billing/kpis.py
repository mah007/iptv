"""Billing KPIs for the admin dashboard (SPEC §8.3 Dashboard, ADR-0012).

Four aggregate queries, cached for 30 s in redis-cache (SPEC §8.4); when Redis
is down they are computed on every call.

- **MRR** per currency: every running (active or grace) paid subscription's
  snapshot price, normalised to 30 days (`price * 30 / period days`, a month
  counting 30). Gross as sold; `mrr_net` takes VAT out when prices include it.
- **Revenue** this month and last, per currency: payments received minus what was
  refunded of them, by the day they were paid, on the Riyadh calendar.
"""

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Final

from django.core.cache import cache
from django.db.models import (
    Count,
    ExpressionWrapper,
    F,
    FloatField,
    IntegerField,
    Q,
    Sum,
)
from django.db.models.fields.json import KT
from django.db.models.functions import Cast
from django.utils import timezone

from apps.accounts.models import DEFAULT_TIMEZONE
from apps.billing.models import (
    PAID_STATUSES,
    EndReason,
    Payment,
    Subscription,
    SubscriptionSource,
    SubscriptionStatus,
)
from apps.billing.renewal import zone
from apps.core.services import get_setting

logger = logging.getLogger(__name__)

CACHE_KEY: Final = "dashboard:billing:v1"
CACHE_TTL_S: Final = 30
_RUNNING = (SubscriptionStatus.ACTIVE, SubscriptionStatus.GRACE)


@dataclass(frozen=True, slots=True)
class Amount:
    currency: str
    amount: int


@dataclass(frozen=True, slots=True)
class PlanCount:
    code: str
    name_en: str
    name_ar: str
    subscribers: int


@dataclass(frozen=True, slots=True)
class BillingKpis:
    active_subscribers: int
    grace: int
    suspended: int
    trials_active: int
    trial_requests: int
    expiring_7d: int
    new_subscriptions_30d: int
    churned_30d: int
    mrr: list[Amount]
    mrr_net: list[Amount]
    revenue_mtd: list[Amount]
    revenue_last_month: list[Amount]
    by_plan: list[PlanCount]
    as_of: datetime


def _month_start(moment: datetime) -> datetime:
    local = moment.astimezone(zone(DEFAULT_TIMEZONE))
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _revenue(start: datetime, end: datetime) -> list[Amount]:
    rows = (
        Payment.objects.filter(status__in=PAID_STATUSES, paid_at__gte=start, paid_at__lt=end)
        .values("currency")
        .annotate(total=Sum(F("amount") - F("refunded_amount")))
        .order_by("currency")
    )
    return [Amount(row["currency"], int(row["total"] or 0)) for row in rows]


def _mrr() -> tuple[list[Amount], list[Amount]]:
    days = ExpressionWrapper(
        Cast(KT("plan_snapshot__duration_months"), IntegerField()) * 30
        + Cast(KT("plan_snapshot__duration_days"), IntegerField()),
        output_field=IntegerField(),
    )
    rows = (
        Subscription.objects.filter(status__in=_RUNNING)
        .exclude(source=SubscriptionSource.TRIAL)
        .alias(price=Cast(KT("plan_snapshot__price"), IntegerField()), period=days)
        .annotate(currency_code=KT("plan_snapshot__currency"))
        .filter(period__gt=0)
        .values("currency_code")
        .annotate(
            monthly=Sum(
                ExpressionWrapper(
                    Cast(F("price"), FloatField()) * 30.0 / Cast(F("period"), FloatField()),
                    output_field=FloatField(),
                )
            )
        )
        .order_by("currency_code")
    )
    gross = [Amount(str(row["currency_code"]), round(float(row["monthly"] or 0))) for row in rows]
    rate = Decimal(str(get_setting("billing.vat_rate")))
    inclusive = get_setting("billing.prices_include_vat") is True
    net = [
        Amount(
            item.currency,
            int((Decimal(item.amount) / (1 + rate)).quantize(Decimal(1), ROUND_HALF_UP))
            if inclusive
            else item.amount,
        )
        for item in gross
    ]
    return gross, net


def compute(*, now: datetime | None = None) -> BillingKpis:
    moment = now or timezone.now()
    month = _month_start(moment)
    previous = _month_start(month - timedelta(days=1))
    paid = ~Q(source=SubscriptionSource.TRIAL)
    counts = Subscription.objects.aggregate(
        active=Count("user", filter=Q(status__in=_RUNNING) & paid, distinct=True),
        grace=Count("pk", filter=Q(status=SubscriptionStatus.GRACE)),
        suspended=Count("pk", filter=Q(status=SubscriptionStatus.SUSPENDED)),
        trials=Count("pk", filter=Q(status__in=_RUNNING, source=SubscriptionSource.TRIAL)),
        trial_requests=Count(
            "pk", filter=Q(status=SubscriptionStatus.PENDING, source=SubscriptionSource.TRIAL)
        ),
        expiring=Count(
            "pk",
            filter=Q(
                status=SubscriptionStatus.ACTIVE,
                ends_at__gt=moment,
                ends_at__lte=moment + timedelta(days=7),
            ),
        ),
        new=Count("pk", filter=paid & Q(created_at__gte=moment - timedelta(days=30))),
        churned=Count(
            "pk",
            filter=Q(
                ended_at__gte=moment - timedelta(days=30),
                end_reason__in=(EndReason.EXPIRED, EndReason.CANCELLED, EndReason.REFUNDED),
            )
            & paid,
        ),
    )
    by_plan = (
        Subscription.objects.filter(status__in=_RUNNING)
        .values("plan__code", "plan__name_en", "plan__name_ar")
        .annotate(subscribers=Count("pk"))
        .order_by("-subscribers", "plan__code")
    )
    mrr, mrr_net = _mrr()
    return BillingKpis(
        active_subscribers=int(counts["active"]),
        grace=int(counts["grace"]),
        suspended=int(counts["suspended"]),
        trials_active=int(counts["trials"]),
        trial_requests=int(counts["trial_requests"]),
        expiring_7d=int(counts["expiring"]),
        new_subscriptions_30d=int(counts["new"]),
        churned_30d=int(counts["churned"]),
        mrr=mrr,
        mrr_net=mrr_net,
        revenue_mtd=_revenue(month, moment + timedelta(seconds=1)),
        revenue_last_month=_revenue(previous, month),
        by_plan=[
            PlanCount(
                row["plan__code"], row["plan__name_en"], row["plan__name_ar"], row["subscribers"]
            )
            for row in by_plan
        ],
        as_of=moment,
    )


def _from_cache(data: dict[str, Any]) -> BillingKpis:
    amounts = ("mrr", "mrr_net", "revenue_mtd", "revenue_last_month")
    values = {
        **data,
        **{key: [Amount(**item) for item in data[key]] for key in amounts},
        "by_plan": [PlanCount(**item) for item in data["by_plan"]],
    }
    return BillingKpis(**values)


def kpis() -> BillingKpis:
    """The KPIs, at most 30 s old."""
    try:
        cached: dict[str, Any] | None = cache.get(CACHE_KEY)
    except Exception:  # a cache outage must not take the dashboard down
        logger.warning("billing KPI cache unavailable", exc_info=True)
        cached = None
    if cached is not None:
        return _from_cache(cached)
    fresh = compute()
    try:
        cache.set(CACHE_KEY, asdict(fresh), CACHE_TTL_S)
    except Exception:
        logger.warning("billing KPI cache unavailable", exc_info=True)
    return fresh


def invalidate() -> None:
    cache.delete(CACHE_KEY)
