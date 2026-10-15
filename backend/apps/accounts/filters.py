"""Query filters of the admin customer and access-rule lists (SPEC §8.3 Customers)."""

from datetime import timedelta

from django.db.models import Q, QuerySet
from django.utils import timezone
from django_filters import rest_framework as filters

from apps.accounts.governing import GoverningStatus
from apps.accounts.models import (
    AccessRule,
    AccessRuleType,
    AppHint,
    Device,
    DeviceKind,
    User,
    UserStatus,
)
from apps.playback.entitlements import EntitlementStatus

MAX_EXPIRING_DAYS = 365


def _not_expired(now: object) -> Q:
    return Q(access__expires_at__isnull=True) | Q(access__expires_at__gt=now)


class CustomerFilter(filters.FilterSet):
    status = filters.ChoiceFilter(choices=UserStatus.choices, help_text="Account status.")
    subscription_status = filters.ChoiceFilter(
        field_name="governing_status",
        choices=[(status.value, status.name) for status in GoverningStatus],
        help_text="The governing status: the subscription's from its dates, else the access "
        "profile's (ADR-0023).",
    )
    access_status = filters.ChoiceFilter(
        choices=[(status.value, status.name) for status in EntitlementStatus],
        method="filter_access_status",
        help_text="What the customer may do right now: active, expired, suspended, disabled.",
    )
    expiring_within_days = filters.NumberFilter(
        method="filter_expiring",
        min_value=0,
        max_value=MAX_EXPIRING_DAYS,
        help_text="Active customers whose subscription (else access profile) ends within this "
        "many days.",
    )
    risk = filters.ChoiceFilter(
        choices=[("flagged", "flagged")],
        method="filter_risk",
        help_text="flagged: customers with an open anti-sharing flag (ADR-0021).",
    )
    ordering = filters.OrderingFilter(
        fields=(
            ("created_at", "created_at"),
            ("name", "name"),
            ("username", "username"),
            ("effective_ends", "expires_at"),
        ),
        help_text="Sort key; prefix with - for descending. Default: -created_at.",
    )

    class Meta:
        model = User
        fields = ("status", "subscription_status", "access_status", "expiring_within_days", "risk")

    def filter_risk(self, queryset: QuerySet[User], name: str, value: str) -> QuerySet[User]:
        from apps.antisharing.models import FlagStatus, RiskFlag  # noqa: PLC0415

        open_flags = RiskFlag.objects.filter(status=FlagStatus.OPEN).values("user_id")
        return queryset.filter(pk__in=open_flags) if value == "flagged" else queryset

    def filter_access_status(
        self, queryset: QuerySet[User], name: str, value: str
    ) -> QuerySet[User]:
        now = timezone.now()
        active = Q(status=UserStatus.ACTIVE)
        if value == EntitlementStatus.ACTIVE:
            return queryset.filter(active & _not_expired(now))
        if value == EntitlementStatus.EXPIRED:
            return queryset.filter(active, access__expires_at__lte=now)
        return queryset.filter(status=value)

    def filter_expiring(self, queryset: QuerySet[User], name: str, value: int) -> QuerySet[User]:
        now = timezone.now()
        # Annotations of `governing.with_governing` (the customer list's queryset).
        conditions: dict[str, object] = {
            "governing_status": GoverningStatus.ACTIVE,
            "effective_ends__gt": now,
            "effective_ends__lte": now + timedelta(days=int(value)),
        }
        return queryset.filter(**conditions)


class AccessRuleFilter(filters.FilterSet):
    user = filters.UUIDFilter(field_name="user_id", help_text="Rules of one customer.")
    scope = filters.ChoiceFilter(
        choices=(("global", "global"), ("customer", "customer")),
        method="filter_scope",
        help_text="global: rules for everyone; customer: per-customer rules.",
    )
    type = filters.ChoiceFilter(choices=AccessRuleType.choices)

    class Meta:
        model = AccessRule
        fields = ("user", "scope", "type")

    def filter_scope(
        self, queryset: QuerySet[AccessRule], name: str, value: str
    ) -> QuerySet[AccessRule]:
        return queryset.filter(user__isnull=value == "global")


class DeviceFilter(filters.FilterSet):
    """The Devices page's filters (ADR-0023)."""

    kind = filters.ChoiceFilter(choices=DeviceKind.choices)
    app_hint = filters.ChoiceFilter(choices=AppHint.choices)
    status = filters.ChoiceFilter(
        choices=(
            ("active", "active"),
            ("pending", "pending"),
            ("blocked", "blocked"),
            ("revoked", "revoked"),
        ),
        method="filter_status",
        help_text="active, pending (waiting for approval), blocked or revoked.",
    )
    seen_within_days = filters.NumberFilter(
        method="filter_seen",
        min_value=0,
        max_value=MAX_EXPIRING_DAYS,
        help_text="Devices seen within this many days.",
    )
    country = filters.CharFilter(field_name="last_country", lookup_expr="iexact", max_length=2)
    customer = filters.UUIDFilter(field_name="user_id")
    ordering = filters.OrderingFilter(
        fields=(
            ("last_seen", "last_seen"),
            ("created_at", "created_at"),
            ("name", "name"),
        ),
        help_text="Sort key; prefix with - for descending. Default: last seen first.",
    )

    class Meta:
        model = Device
        fields = ("kind", "app_hint", "status", "seen_within_days", "country", "customer")

    def filter_status(self, queryset: QuerySet[Device], name: str, value: str) -> QuerySet[Device]:
        live = queryset.filter(revoked_at__isnull=True)
        if value == "revoked":
            return queryset.filter(revoked_at__isnull=False)
        if value == "blocked":
            return live.filter(blocked=True)
        if value == "pending":
            return live.filter(blocked=False, approved=False)
        return live.filter(blocked=False, approved=True)

    def filter_seen(self, queryset: QuerySet[Device], name: str, value: int) -> QuerySet[Device]:
        return queryset.filter(last_seen__gte=timezone.now() - timedelta(days=int(value)))
