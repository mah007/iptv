"""The admin's Devices and Lockouts pages and the customers' governing status (ADR-0023)."""

from collections.abc import Callable, Iterator
from datetime import timedelta
from typing import Any

import pytest
from axes.models import AccessAttempt
from django.conf import settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts import services
from apps.accounts.models import Device, DeviceKind, User, UserStatus
from apps.audit.models import AuditLog
from apps.billing.models import Plan, Subscription, SubscriptionSource, SubscriptionStatus
from apps.core.services import set_setting
from apps.core.stores import state_redis
from apps.playback import entitlements
from apps.xtream_api import ratelimit

pytestmark = pytest.mark.django_db
ADMIN = {"host": settings.ADMIN_HOST}
CUSTOMERS = "/api/v1/admin/customers"
DEVICES = "/api/v1/admin/devices"
LOCKOUTS = "/api/v1/admin/lockouts"


@pytest.fixture
def plan(db: None) -> Plan:
    from apps.billing import services as billing  # noqa: PLC0415

    return billing.create_plan(
        {
            "code": "standard",
            "name_en": "Standard",
            "name_ar": "القياسية",
            "price": 4900,
            "currency": "SAR",
            "duration_days": 30,
            "max_streams": 2,
            "max_devices": 3,
            "max_quality": 1080,
        },
        category_ids=[],
        actor=None,
    )


def subscribe(
    user: User,
    plan: Plan,
    *,
    status: str = SubscriptionStatus.ACTIVE,
    starts: timedelta = timedelta(days=-20),
    ends: timedelta = timedelta(days=10),
    grace_days: int = 0,
) -> Subscription:
    now = timezone.now()
    return Subscription.objects.create(
        user=user,
        plan=plan,
        plan_snapshot={"code": plan.code},
        status=status,
        starts_at=now + starts,
        ends_at=now + ends,
        grace_days=grace_days,
        source=SubscriptionSource.MANUAL,
    )


# --- The governing status -------------------------------------------------------------------------


def test_the_customer_list_shows_the_governing_status(
    owner_client: APIClient,
    make_customer: Callable[..., User],
    plan: Plan,
    django_assert_max_num_queries: Callable[..., Any],
) -> None:
    now = timezone.now()
    cases: dict[str, tuple[User, str, str]] = {}

    def case(label: str, user: User, status: str, source: str) -> None:
        cases[label] = (user, status, source)

    case("profile", make_customer(name="Profile"), "active", "profile")
    case(
        "profile ended",
        make_customer(name="Ended profile", expires_at=now - timedelta(days=1)),
        "expired",
        "profile",
    )
    paying = make_customer(name="Paying", expires_at=now - timedelta(days=30))
    subscribe(paying, plan)
    case("paying", paying, "active", "subscription")
    graced = make_customer(name="Graced")
    subscribe(graced, plan, ends=timedelta(days=-2), grace_days=3)
    case("grace", graced, "grace", "subscription")
    lapsed = make_customer(name="Lapsed")
    subscribe(lapsed, plan, ends=timedelta(days=-5), grace_days=3)
    case("lapsed", lapsed, "expired", "subscription")
    frozen = make_customer(name="Frozen")
    subscribe(frozen, plan, status=SubscriptionStatus.SUSPENDED)
    case("subscription suspended", frozen, "suspended", "subscription")
    blocked = make_customer(name="Blocked")
    subscribe(blocked, plan)
    services.suspend_customer(blocked, actor=None)
    case("account suspended", blocked, "suspended", "subscription")
    waiting = make_customer(name="Waiting")
    subscribe(waiting, plan, status=SubscriptionStatus.PENDING, starts=timedelta(days=1))
    case("pending only", waiting, "active", "profile")
    cancelled = make_customer(name="Cancelled")
    subscribe(cancelled, plan, status=SubscriptionStatus.CANCELLED, ends=timedelta(days=-1))
    case("cancelled", cancelled, "expired", "subscription")
    nobody = User.objects.create_user(username="no-profile", name="Nobody")
    case("nothing", nobody, "none", "none")

    with django_assert_max_num_queries(3):
        response = owner_client.get(CUSTOMERS, {"page_size": 50}, headers=ADMIN)
    assert response.status_code == 200
    rows = {row["id"]: row for row in response.json()["results"]}
    for label, (user, status, source) in cases.items():
        row = rows[str(user.pk)]
        assert (row["subscription_status"], row["status_source"]) == (status, source), label
    assert rows[str(paying.pk)]["subscription_plan"] == {
        "name_en": "Standard",
        "name_ar": "القياسية",
    }
    assert rows[str(nobody.pk)]["subscription_plan"] is None
    # The same verdict as playback's for subscription-governed customers.
    for user in (paying, graced, lapsed, frozen, cancelled):
        governing = entitlements.governing_subscription(user.subscriptions.all())
        assert governing is not None
        expected = entitlements.subscription_status(user.status, governing, now).value
        verdict = rows[str(user.pk)]["subscription_status"]
        assert ("active" if verdict == "grace" else verdict) == expected

    graced_only = owner_client.get(CUSTOMERS, {"subscription_status": "grace"}, headers=ADMIN)
    assert [row["id"] for row in graced_only.json()["results"]] == [str(graced.pk)]
    expiring = owner_client.get(CUSTOMERS, {"expiring_within_days": 12}, headers=ADMIN)
    assert [row["id"] for row in expiring.json()["results"]] == [str(paying.pk)]


def test_the_list_query_count_does_not_grow_with_customers(
    owner_client: APIClient,
    make_customer: Callable[..., User],
    plan: Plan,
    django_assert_num_queries: Callable[..., Any],
) -> None:
    for _ in range(2):
        subscribe(make_customer(devices=1), plan)
    with django_assert_num_queries(3) as few:
        owner_client.get(CUSTOMERS, headers=ADMIN)
    for _ in range(6):
        subscribe(make_customer(devices=2), plan)
    with django_assert_num_queries(len(few.captured_queries)):
        owner_client.get(CUSTOMERS, headers=ADMIN)


def test_dashboard_kpis_count_grace_and_subscriptions(
    owner_client: APIClient, make_customer: Callable[..., User], plan: Plan
) -> None:
    from apps.dashboard import services as dashboard  # noqa: PLC0415

    now = timezone.now()
    subscribe(make_customer(expires_at=now - timedelta(days=9)), plan, ends=timedelta(days=3))
    subscribe(make_customer(), plan, ends=timedelta(days=-1), grace_days=2)
    make_customer(expires_at=now - timedelta(days=1))
    dashboard.invalidate()
    body = owner_client.get("/api/v1/admin/dashboard/kpis", headers=ADMIN).json()
    assert (body["customers_active"], body["customers_grace"], body["customers_expired"]) == (
        1,
        1,
        1,
    )
    assert body["expiring_7d"] == 1


# --- Devices --------------------------------------------------------------------------------------


@pytest.fixture
def devices(make_customer: Callable[..., User], make_admin: Callable[..., User]) -> dict[str, Any]:
    sara = make_customer(name="Sara", devices=2)
    omar = make_customer(name="Omar", devices=1)
    tivimate = Device.objects.filter(user=sara).order_by("created_at").first()
    assert tivimate is not None
    tivimate.app_hint = "tivimate"
    tivimate.last_seen = timezone.now()
    tivimate.last_country = "SA"
    tivimate.save()
    browser = Device.objects.create(user=omar, kind=DeviceKind.WEB, name="Chrome on Windows")
    staff = make_admin("admin")
    Device.objects.create(user=staff, kind=DeviceKind.WEB, name="Admin's browser")
    return {"sara": sara, "omar": omar, "tivimate": tivimate, "browser": browser}


def test_the_devices_list_shows_every_customer_device(
    owner_client: APIClient,
    devices: dict[str, Any],
    django_assert_num_queries: Callable[..., Any],
) -> None:
    with django_assert_num_queries(3) as queries:  # permissions, count, page
        response = owner_client.get(DEVICES, headers=ADMIN)
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["count"] == 4  # staff devices are not customers'
    first = body["results"][0]
    assert first["id"] == str(devices["tivimate"].pk)  # seen most recently
    assert first["customer"]["name"] == "Sara"
    assert first["xtream_username"]
    assert len(queries.captured_queries) == 3

    def ids(**query: Any) -> set[str]:
        found = owner_client.get(DEVICES, query, headers=ADMIN).json()["results"]
        return {row["id"] for row in found}

    assert ids(kind="web") == {str(devices["browser"].pk)}
    assert ids(app_hint="tivimate") == {str(devices["tivimate"].pk)}
    assert ids(country="sa") == {str(devices["tivimate"].pk)}
    assert ids(seen_within_days=1) == {str(devices["tivimate"].pk)}
    assert len(ids(customer=str(devices["sara"].pk))) == 2
    assert ids(search="Omar") == {
        str(device.pk) for device in Device.objects.filter(user=devices["omar"])
    }
    username = devices["tivimate"].credential.username
    assert ids(search=username) == {str(devices["tivimate"].pk)}
    services.block_device(devices["browser"], actor=None)
    assert ids(status="blocked") == {str(devices["browser"].pk)}
    assert str(devices["browser"].pk) not in ids(status="active")


def test_the_devices_list_needs_customers_view(make_admin: Callable[..., User]) -> None:
    client = APIClient()
    client.force_authenticate(make_admin(permissions=["dashboard.view"]))
    assert client.get(DEVICES, headers=ADMIN).status_code == 403


def test_bulk_actions_change_and_audit_each_device(
    owner_client: APIClient, owner: User, devices: dict[str, Any]
) -> None:
    sara_devices = list(Device.objects.filter(user=devices["sara"]))
    services.revoke_device(sara_devices[1], actor=None)
    ids = [str(device.pk) for device in sara_devices] + [str(devices["browser"].pk)]
    response = owner_client.post(
        f"{DEVICES}/bulk",
        {
            "ids": [*ids, "01a00000-0000-7000-8000-000000000000"],
            "action": "block",
            "reason": "shared",
        },
        format="json",
        headers=ADMIN,
    )
    assert response.status_code == 200, response.content
    assert response.json() == {"changed": 2, "skipped": 2}
    blocked = Device.objects.filter(pk__in=ids, blocked=True)
    assert blocked.count() == 2
    assert set(blocked.values_list("blocked_reason", flat=True)) == {"shared"}
    assert AuditLog.objects.filter(action="device.block", actor=owner).count() == 2
    again = owner_client.post(
        f"{DEVICES}/bulk", {"ids": ids, "action": "unblock"}, format="json", headers=ADMIN
    )
    assert again.json() == {"changed": 2, "skipped": 1}
    revoked = owner_client.post(
        f"{DEVICES}/bulk", {"ids": ids, "action": "revoke"}, format="json", headers=ADMIN
    )
    assert revoked.json() == {"changed": 2, "skipped": 1}


def test_bulk_actions_are_checked(
    owner_client: APIClient, make_admin: Callable[..., User], devices: dict[str, Any]
) -> None:
    viewer = APIClient()
    viewer.force_authenticate(make_admin("viewer"))
    body = {"ids": [str(devices["browser"].pk)], "action": "block"}
    assert viewer.post(f"{DEVICES}/bulk", body, format="json", headers=ADMIN).status_code == 403
    for bad in ({"ids": [], "action": "block"}, {"ids": body["ids"], "action": "delete"}):
        assert (
            owner_client.post(f"{DEVICES}/bulk", bad, format="json", headers=ADMIN).status_code
            == 400
        )


# --- Lockouts -------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_counters() -> Iterator[None]:
    def clear() -> None:
        for pattern in ("xc:fail:*", "xc:block:*", "xc:strike:*", "mfa:fail:*", "stepup:fail:*"):
            for key in state_redis().scan_iter(pattern):
                state_redis().delete(key)

    clear()
    yield
    clear()


def lock_sign_in(login: str, ip: str = "203.0.113.7", failures: int = 5) -> AccessAttempt:
    return AccessAttempt.objects.create(
        username=login,
        ip_address=ip,
        user_agent="test",
        http_accept="*/*",
        path_info="/api/v1/auth/login",
        failures_since_start=failures,
        attempt_time=timezone.now(),
    )


def test_lockouts_list_every_kind(
    owner_client: APIClient, make_customer: Callable[..., User], make_admin: Callable[..., User]
) -> None:
    set_setting("xtream.auth_failures_per_username", 3, actor=None)
    set_setting("xtream.auth_failures_per_ip", 5, actor=None)
    customer = make_customer(name="Sara", devices=1)
    admin = make_admin("admin", username="ops")
    lock_sign_in(customer.email.upper())
    lock_sign_in("ops", ip="198.51.100.4")
    lock_sign_in("someone-else", failures=2)  # under the limit: not locked
    old = lock_sign_in("old", failures=5)
    AccessAttempt.objects.filter(pk=old.pk).update(attempt_time=timezone.now() - timedelta(days=2))
    state_redis().set(f"mfa:fail:{admin.pk}", 10, ex=600)
    state_redis().set(f"stepup:fail:{customer.pk}", 5, ex=600)
    username = Device.objects.get(user=customer).credential.username
    for _ in range(3):
        ratelimit.record_failure(username, "192.0.2.10")
    for attempt in range(5):
        ratelimit.record_failure(f"guess-{attempt}", "192.0.2.99")
    response = owner_client.get(LOCKOUTS, headers=ADMIN)
    assert response.status_code == 200, response.content
    assert response["Cache-Control"] == "no-store"
    rows = response.json()
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_kind.setdefault(row["kind"], []).append(row)
    assert {row["login"] for row in by_kind["sign_in"]} == {customer.email.upper(), "ops"}
    accounts = {row["login"]: row["account"] for row in by_kind["sign_in"]}
    assert accounts["ops"]["is_staff"] is True
    assert accounts[customer.email.upper()]["id"] == str(customer.pk)
    assert by_kind["second_factor"][0]["account"]["username"] == "ops"
    assert by_kind["confirmation"][0]["account"]["id"] == str(customer.pk)
    tv = by_kind["xtream_login"]
    assert [(row["login"], row["network"]) for row in tv] == [(username, "192.0.2.0")]
    assert tv[0]["account"]["id"] == str(customer.pk)
    assert [row["ip"] for row in by_kind["xtream_address"]] == ["192.0.2.99"]
    # One customer's.
    mine = owner_client.get(LOCKOUTS, {"customer": str(customer.pk)}, headers=ADMIN).json()
    assert {row["kind"] for row in mine} == {"sign_in", "confirmation", "xtream_login"}


def test_unlocking_lifts_each_lockout_and_is_audited(
    owner_client: APIClient, owner: User, make_customer: Callable[..., User]
) -> None:
    set_setting("xtream.auth_failures_per_username", 3, actor=None)
    set_setting("xtream.auth_failures_per_ip", 5, actor=None)
    customer = make_customer(devices=1)
    lock_sign_in(customer.email)
    state_redis().set(f"stepup:fail:{customer.pk}", 5, ex=600)
    username = Device.objects.get(user=customer).credential.username
    for _ in range(3):
        ratelimit.record_failure(username, "192.0.2.10")
    for attempt in range(5):
        ratelimit.record_failure(f"guess-{attempt}", "192.0.2.99")
    rows = owner_client.get(LOCKOUTS, headers=ADMIN).json()
    assert len(rows) == 4
    for row in rows:
        response = owner_client.post(
            f"{LOCKOUTS}/unlock", {"id": row["id"]}, format="json", headers=ADMIN
        )
        assert response.status_code == 204, (row["kind"], response.content)
    assert owner_client.get(LOCKOUTS, headers=ADMIN).json() == []
    assert not ratelimit.failures(username, "192.0.2.10").blocked
    assert not ratelimit.failures("guess-9", "192.0.2.99").blocked
    assert AuditLog.objects.filter(action="lockout.unlock", actor=owner).count() == 4
    # A sign-in lockout that ended is gone; the counters' unlocks are idempotent.
    sign_in = next(row["id"] for row in rows if row["kind"] == "sign_in")
    missing = owner_client.post(f"{LOCKOUTS}/unlock", {"id": sign_in}, format="json", headers=ADMIN)
    assert missing.status_code == 404
    for bogus in ("nope:1", "sign_in:x", "xtream_login:zz:1", "xtream_address:not-an-ip"):
        response = owner_client.post(
            f"{LOCKOUTS}/unlock", {"id": bogus}, format="json", headers=ADMIN
        )
        assert response.status_code == 404


def test_unlocking_an_admin_needs_admins_manage(
    make_admin: Callable[..., User], make_customer: Callable[..., User]
) -> None:
    supporter = APIClient()
    supporter.force_authenticate(make_admin("support"))
    make_admin("admin", username="ops")
    staff_lock = lock_sign_in("ops")
    customer_lock = lock_sign_in(make_customer().email)
    refused = supporter.post(
        f"{LOCKOUTS}/unlock", {"id": f"sign_in:{staff_lock.pk}"}, format="json", headers=ADMIN
    )
    assert refused.status_code == 403
    allowed = supporter.post(
        f"{LOCKOUTS}/unlock", {"id": f"sign_in:{customer_lock.pk}"}, format="json", headers=ADMIN
    )
    assert allowed.status_code == 204
    viewer = APIClient()
    viewer.force_authenticate(make_admin("viewer"))
    assert viewer.get(LOCKOUTS, headers=ADMIN).status_code == 200
    assert (
        viewer.post(
            f"{LOCKOUTS}/unlock", {"id": f"sign_in:{staff_lock.pk}"}, format="json", headers=ADMIN
        ).status_code
        == 403
    )


@pytest.mark.step_up
def test_unlocking_needs_a_recent_confirmation(owner_client: APIClient) -> None:
    lock = lock_sign_in("someone")
    response = owner_client.post(
        f"{LOCKOUTS}/unlock", {"id": f"sign_in:{lock.pk}"}, format="json", headers=ADMIN
    )
    assert response.status_code == 403
    assert response.json()["code"] == "STEP_UP_REQUIRED"
    assert AccessAttempt.objects.filter(pk=lock.pk).exists()


def test_a_suspended_account_still_counts_as_suspended(
    owner_client: APIClient, make_customer: Callable[..., User]
) -> None:
    user = make_customer()
    User.objects.filter(pk=user.pk).update(status=UserStatus.DISABLED)
    rows = owner_client.get(CUSTOMERS, headers=ADMIN).json()["results"]
    assert rows[0]["subscription_status"] == "disabled"
