"""The SPEC §7.4 entitlement object, derived from the manual access profile (ADR-0006)."""

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from django.utils import timezone

from apps.accounts import services
from apps.accounts.models import (
    AccessRule,
    AccessRuleType,
    ConcurrencyPolicy,
    CustomerAccess,
    MaxQuality,
    User,
    UserStatus,
)
from apps.catalog.models import Category
from apps.conftest import CustomerFactory
from apps.core.stores import state_redis
from apps.playback import entitlements
from apps.playback.entitlements import EntitlementStatus

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
type Capture = Callable[..., Any]


def build(user: User) -> entitlements.Entitlement:
    loaded = entitlements.load_user(user.pk)
    assert loaded is not None
    result = entitlements.build(loaded, now=NOW)
    assert result is not None
    return result


def test_active_profile_with_defaults(make_customer: CustomerFactory) -> None:
    user = make_customer()
    assert build(user) == {
        "v": 1,
        "user_id": str(user.pk),
        "source": "access_profile",
        "status": "active",
        "ends_at": None,
        "grace_until": None,
        "max_streams": 1,
        "max_devices": 2,
        "max_quality": 1080,
        "policy": "reject",
        "categories": None,
        "allow_movies": True,
        "allow_series": True,
        "allow_live": True,
        "allow_download": False,
        "country_rules": [],
        "ip_rules": [],
    }


@pytest.mark.parametrize(
    ("user_status", "expires_at", "expected"),
    [
        (UserStatus.ACTIVE, NOW + timedelta(seconds=1), EntitlementStatus.ACTIVE),
        (UserStatus.ACTIVE, NOW, EntitlementStatus.EXPIRED),
        (UserStatus.ACTIVE, NOW - timedelta(days=1), EntitlementStatus.EXPIRED),
        # The account is checked first (SPEC §7.4 check 1), then the period.
        (UserStatus.SUSPENDED, NOW - timedelta(days=1), EntitlementStatus.SUSPENDED),
        (UserStatus.SUSPENDED, None, EntitlementStatus.SUSPENDED),
        (UserStatus.DISABLED, NOW + timedelta(days=1), EntitlementStatus.DISABLED),
    ],
)
def test_status(
    user_status: UserStatus,
    expires_at: datetime | None,
    expected: EntitlementStatus,
    make_customer: CustomerFactory,
) -> None:
    user = make_customer(expires_at=expires_at)
    User.objects.filter(pk=user.pk).update(status=user_status)
    entitlement = build(user)
    assert entitlement["status"] == expected
    assert entitlement["ends_at"] == (expires_at.isoformat() if expires_at else None)


def test_content_types_categories_quality_and_policy(
    make_customer: CustomerFactory, category: Category
) -> None:
    other = Category.objects.create(kind="live", slug="news", name_en="News", name_ar="أخبار")
    user = make_customer(
        max_streams=3,
        max_devices=4,
        max_quality=MaxQuality.HD,
        concurrency_policy=ConcurrencyPolicy.KICK_OLDEST,
        allow_movies=False,
        allow_live=False,
        category_ids=[other.pk, category.pk],
    )
    entitlement = build(user)
    assert entitlement["max_streams"] == 3
    assert entitlement["max_devices"] == 4
    assert entitlement["max_quality"] == 720
    assert entitlement["policy"] == "kick_oldest"
    assert (entitlement["allow_movies"], entitlement["allow_series"]) == (False, True)
    assert entitlement["allow_live"] is False
    assert entitlement["categories"] == sorted([str(category.pk), str(other.pk)])


def test_rules_split_by_kind_and_expired_ones_dropped(make_customer: CustomerFactory) -> None:
    user = make_customer()
    for rule_type, value, expires_at in [
        (AccessRuleType.IP_ALLOW, "198.51.100.7", None),
        (AccessRuleType.CIDR_DENY, "203.0.113.0/24", NOW + timedelta(hours=1)),
        (AccessRuleType.COUNTRY_DENY, "IR", None),
        (AccessRuleType.IP_DENY, "192.0.2.1", NOW - timedelta(hours=1)),
    ]:
        AccessRule.objects.create(user=user, type=rule_type, value=value, expires_at=expires_at)
    AccessRule.objects.create(user=None, type=AccessRuleType.COUNTRY_DENY, value="KP")
    entitlement = build(user)
    assert [rule["value"] for rule in entitlement["ip_rules"]] == [
        "198.51.100.7",
        "203.0.113.0/24",
    ]
    assert entitlement["ip_rules"][1]["expires_at"] == (NOW + timedelta(hours=1)).isoformat()
    # Global rules stay out: playback checks them directly.
    assert entitlement["country_rules"] == [
        {"type": "country_deny", "value": "IR", "expires_at": None}
    ]


def test_build_reads_prefetched_data_only(
    make_customer: CustomerFactory, category: Category, django_assert_num_queries: Capture
) -> None:
    user = make_customer(category_ids=[category.pk])
    AccessRule.objects.create(user=user, type=AccessRuleType.IP_DENY, value="192.0.2.1")
    with django_assert_num_queries(3):
        loaded = entitlements.load_user(user.pk)
    assert loaded is not None
    with django_assert_num_queries(0):
        entitlements.build(loaded)


def test_users_without_a_profile_have_no_entitlement(customer_user: User) -> None:
    assert entitlements.build(customer_user) is None
    state_redis().set(entitlements.entitlement_key(customer_user.pk), "stale")
    assert entitlements.refresh(customer_user.pk) is None
    assert state_redis().get(entitlements.entitlement_key(customer_user.pk)) is None
    assert entitlements.refresh("0190f2c6-0000-7000-8000-000000000000") is None


def test_ttl_is_the_time_left_capped_at_an_hour() -> None:
    def ttl(status: str, ends_at: datetime | None) -> int:
        entitlement: Any = {
            "status": status,
            "ends_at": ends_at.isoformat() if ends_at else None,
        }
        return entitlements.ttl_seconds(entitlement, NOW)

    assert ttl("active", None) == 3600
    assert ttl("active", NOW + timedelta(days=3)) == 3600
    assert ttl("active", NOW + timedelta(minutes=10)) == 600
    assert ttl("active", NOW + timedelta(milliseconds=10)) == 1
    assert ttl("expired", NOW - timedelta(days=1)) == 3600
    assert ttl("suspended", NOW + timedelta(minutes=10)) == 3600


def test_refresh_stores_and_get_reads_or_rebuilds(make_customer: CustomerFactory) -> None:
    user = make_customer(expires_at=timezone.now() + timedelta(minutes=30))
    key = entitlements.entitlement_key(user.pk)
    state_redis().delete(key)

    stored = entitlements.get(user.pk)  # missing: rebuilt from the database
    assert stored is not None
    assert json.loads(state_redis().get(key))["status"] == "active"  # type: ignore[arg-type]
    assert 1700 < cast("int", state_redis().ttl(key)) <= 1800

    CustomerAccess.objects.filter(user=user).update(max_streams=5)
    assert entitlements.get(user.pk)["max_streams"] == 1  # type: ignore[index]  # cached
    entitlements.refresh(user.pk)
    assert entitlements.get(user.pk)["max_streams"] == 5  # type: ignore[index]


def test_changes_refresh_after_commit_only(
    make_customer: CustomerFactory, django_capture_on_commit_callbacks: Capture
) -> None:
    user = make_customer()
    key = entitlements.entitlement_key(user.pk)
    state_redis().delete(key)
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        services.update_access(user, {"max_streams": 4}, actor=None)
    assert state_redis().get(key) is None  # nothing before the commit
    for callback in callbacks:
        callback()
    assert json.loads(state_redis().get(key))["max_streams"] == 4  # type: ignore[arg-type]
