"""The governing status of customers in SQL, for the admin list and the dashboard (ADR-0023).

ADR-0012's source rule, the one `playback.entitlements` applies per customer: the
governing subscription is the current one (active, grace or suspended; the latest end
first), else the one that ended last. A customer with no such subscription (none at all,
or only pending ones) is governed by their access profile.

The status follows the dates, not only the stored status, exactly like
`entitlements.subscription_status`, so the list is right before the 5-minute job:

- `disabled`, `suspended`: the account's own status first;
- a governing subscription: `suspended`; `expired` unless it is active or in grace and has
  started; `active` before its end; `grace` from its end until `grace_days` later
  (playback still allows it); else `expired`;
- the access profile: `active` while it has no end or ends later, else `expired`;
- `none`: neither a subscription nor a profile.

Every value is an annotation (correlated subqueries), so a page of customers stays one
query and the list can filter and sort on it.
"""

from datetime import datetime, timedelta
from enum import StrEnum

from django.db.models import (
    Case,
    CharField,
    DateTimeField,
    DurationField,
    ExpressionWrapper,
    F,
    IntegerField,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
    UUIDField,
    Value,
    When,
)
from django.db.models.functions import Coalesce

from apps.accounts.models import User, UserStatus
from apps.billing.models import CURRENT_STATUSES, ENDED_STATUSES, Subscription, SubscriptionStatus

__all__ = ["GoverningStatus", "StatusSource", "with_governing"]


class GoverningStatus(StrEnum):
    ACTIVE = "active"
    GRACE = "grace"
    EXPIRED = "expired"
    SUSPENDED = "suspended"
    DISABLED = "disabled"
    NONE = "none"


class StatusSource(StrEnum):
    SUBSCRIPTION = "subscription"
    PROFILE = "profile"
    NONE = "none"


def _of_governing(field: str, output: object) -> Subquery:
    return Subquery(
        Subscription.objects.filter(pk=OuterRef("gov_id")).values(field)[:1],
        output_field=output,  # type: ignore[arg-type]
    )


def with_governing(queryset: QuerySet[User], now: datetime) -> QuerySet[User]:
    """`queryset` annotated with `gov_*`, `governing_status`, `status_source` and
    `effective_ends` (the governing subscription's end, else the profile's)."""
    current = Subscription.objects.filter(
        user=OuterRef("pk"), status__in=CURRENT_STATUSES
    ).order_by("-ends_at", "-pk")
    ended = Subscription.objects.filter(user=OuterRef("pk"), status__in=ENDED_STATUSES).order_by(
        "-ends_at", "-pk"
    )
    annotated = queryset.annotate(
        gov_id=Coalesce(
            Subquery(current.values("pk")[:1]),
            Subquery(ended.values("pk")[:1]),
            output_field=UUIDField(),
        )
    ).annotate(
        gov_status=_of_governing("status", CharField()),
        gov_starts=_of_governing("starts_at", DateTimeField()),
        gov_ends=_of_governing("ends_at", DateTimeField()),
        gov_grace_days=_of_governing("grace_days", IntegerField()),
        gov_plan_en=_of_governing("plan__name_en", CharField()),
        gov_plan_ar=_of_governing("plan__name_ar", CharField()),
    )
    grace_end = ExpressionWrapper(
        F("gov_ends")
        + ExpressionWrapper(
            F("gov_grace_days") * Value(timedelta(days=1)), output_field=DurationField()
        ),
        output_field=DateTimeField(),
    )
    moment = Value(now, output_field=DateTimeField())
    running = (SubscriptionStatus.ACTIVE, SubscriptionStatus.GRACE)
    return annotated.annotate(gov_grace_end=grace_end).annotate(
        governing_status=Case(
            When(status=UserStatus.DISABLED, then=Value(GoverningStatus.DISABLED.value)),
            When(status=UserStatus.SUSPENDED, then=Value(GoverningStatus.SUSPENDED.value)),
            When(
                Q(gov_id__isnull=True, access__isnull=True), then=Value(GoverningStatus.NONE.value)
            ),
            When(
                Q(gov_id__isnull=True)
                & (Q(access__expires_at__isnull=True) | Q(access__expires_at__gt=moment)),
                then=Value(GoverningStatus.ACTIVE.value),
            ),
            When(gov_id__isnull=True, then=Value(GoverningStatus.EXPIRED.value)),
            When(
                gov_status=SubscriptionStatus.SUSPENDED,
                then=Value(GoverningStatus.SUSPENDED.value),
            ),
            When(~Q(gov_status__in=running), then=Value(GoverningStatus.EXPIRED.value)),
            When(gov_starts__gt=moment, then=Value(GoverningStatus.EXPIRED.value)),
            When(gov_ends__gt=moment, then=Value(GoverningStatus.ACTIVE.value)),
            When(gov_grace_end__gt=moment, then=Value(GoverningStatus.GRACE.value)),
            default=Value(GoverningStatus.EXPIRED.value),
            output_field=CharField(),
        ),
        status_source=Case(
            When(gov_id__isnull=False, then=Value(StatusSource.SUBSCRIPTION.value)),
            When(access__isnull=False, then=Value(StatusSource.PROFILE.value)),
            default=Value(StatusSource.NONE.value),
            output_field=CharField(),
        ),
        effective_ends=Coalesce(
            F("gov_ends"), F("access__expires_at"), output_field=DateTimeField()
        ),
    )
