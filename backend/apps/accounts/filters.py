"""Query filters of the admin customer and access-rule lists (SPEC §8.3 Customers)."""

from datetime import timedelta

from django.db.models import Q, QuerySet
from django.utils import timezone
from django_filters import rest_framework as filters

from apps.accounts.models import AccessRule, AccessRuleType, User, UserStatus
from apps.playback.entitlements import EntitlementStatus

MAX_EXPIRING_DAYS = 365


def _not_expired(now: object) -> Q:
    return Q(access__expires_at__isnull=True) | Q(access__expires_at__gt=now)


class CustomerFilter(filters.FilterSet):
    status = filters.ChoiceFilter(choices=UserStatus.choices, help_text="Account status.")
    access_status = filters.ChoiceFilter(
        choices=[(status.value, status.name) for status in EntitlementStatus],
        method="filter_access_status",
        help_text="What the customer may do right now: active, expired, suspended, disabled.",
    )
    expiring_within_days = filters.NumberFilter(
        method="filter_expiring",
        min_value=0,
        max_value=MAX_EXPIRING_DAYS,
        help_text="Active customers whose access ends within this many days.",
    )
    ordering = filters.OrderingFilter(
        fields=(
            ("created_at", "created_at"),
            ("name", "name"),
            ("username", "username"),
            ("access__expires_at", "expires_at"),
        ),
        help_text="Sort key; prefix with - for descending. Default: -created_at.",
    )

    class Meta:
        model = User
        fields = ("status", "access_status", "expiring_within_days")

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
        return queryset.filter(
            status=UserStatus.ACTIVE,
            access__expires_at__gt=now,
            access__expires_at__lte=now + timedelta(days=int(value)),
        )


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
