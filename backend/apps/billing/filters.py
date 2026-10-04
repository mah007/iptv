"""Query filters of the admin billing lists (SPEC §8.3 Plans, Subscriptions, Payments, Invoices)."""

from datetime import timedelta
from typing import Any

from django.db.models import QuerySet
from django.utils import timezone
from django_filters import rest_framework as filters

from apps.billing.models import (
    Invoice,
    InvoiceStatus,
    Payment,
    PaymentProviderCode,
    PaymentStatus,
    Plan,
    Subscription,
    SubscriptionSource,
    SubscriptionStatus,
)

MAX_EXPIRING_DAYS = 365

# django-filter ships no type hints, so its base class is Any to mypy.


class PlanFilter(filters.FilterSet):  # type: ignore[misc,no-any-unimported]
    active = filters.BooleanFilter(help_text="Plans on sale (true) or switched off (false).")
    is_trial = filters.BooleanFilter(help_text="Trial plans only (true) or paid ones (false).")

    class Meta:
        model = Plan
        fields = ("active", "is_trial")


class SubscriptionFilter(filters.FilterSet):  # type: ignore[misc,no-any-unimported]
    status = filters.MultipleChoiceFilter(
        choices=SubscriptionStatus.choices, help_text="One or more statuses."
    )
    source = filters.ChoiceFilter(choices=SubscriptionSource.choices)
    plan = filters.UUIDFilter(field_name="plan_id", help_text="Plan id.")
    user = filters.UUIDFilter(field_name="user_id", help_text="Customer id.")
    expiring_within_days = filters.NumberFilter(
        method="filter_expiring",
        min_value=0,
        max_value=MAX_EXPIRING_DAYS,
        help_text="Active subscriptions that end within this many days.",
    )
    ordering = filters.OrderingFilter(
        fields=(
            ("created_at", "created_at"),
            ("starts_at", "starts_at"),
            ("ends_at", "ends_at"),
        ),
        help_text="Sort key; prefix with - for descending. Default: -created_at.",
    )

    class Meta:
        model = Subscription
        fields = ("status", "source", "plan", "user", "expiring_within_days")

    def filter_expiring(
        self, queryset: QuerySet[Subscription], name: str, value: Any
    ) -> QuerySet[Subscription]:
        now = timezone.now()
        return queryset.filter(
            status=SubscriptionStatus.ACTIVE,
            ends_at__gt=now,
            ends_at__lte=now + timedelta(days=int(value)),
        )


class PaymentFilter(filters.FilterSet):  # type: ignore[misc,no-any-unimported]
    status = filters.MultipleChoiceFilter(choices=PaymentStatus.choices)
    provider = filters.ChoiceFilter(choices=PaymentProviderCode.choices)
    user = filters.UUIDFilter(field_name="user_id", help_text="Customer id.")
    paid_after = filters.IsoDateTimeFilter(field_name="paid_at", lookup_expr="gte")
    paid_before = filters.IsoDateTimeFilter(field_name="paid_at", lookup_expr="lt")
    ordering = filters.OrderingFilter(
        fields=(("created_at", "created_at"), ("paid_at", "paid_at"), ("amount", "amount")),
        help_text="Sort key; prefix with - for descending. Default: -created_at.",
    )

    class Meta:
        model = Payment
        fields = ("status", "provider", "user", "paid_after", "paid_before")


class InvoiceFilter(filters.FilterSet):  # type: ignore[misc,no-any-unimported]
    status = filters.MultipleChoiceFilter(choices=InvoiceStatus.choices)
    user = filters.UUIDFilter(field_name="user_id", help_text="Customer id.")
    year = filters.NumberFilter(field_name="year")
    ordering = filters.OrderingFilter(
        fields=(("created_at", "created_at"), ("issued_at", "issued_at"), ("total", "total")),
        help_text="Sort key; prefix with - for descending. Default: -created_at.",
    )

    class Meta:
        model = Invoice
        fields = ("status", "user", "year")
