"""Admin customer endpoints (plan scope change §3.7): list, create, retrieve,
update, suspend/reactivate and the access profile."""

import json
from collections.abc import Callable
from datetime import timedelta
from typing import Any, cast

import pytest
from django.conf import settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts import services
from apps.accounts.models import AccessRule, AccessRuleType, Device, User, UserStatus
from apps.accounts.signals import access_suspended
from apps.audit.models import AuditLog
from apps.catalog.models import Category
from apps.conftest import AdminFactory, CustomerFactory
from apps.core.stores import state_redis
from apps.playback.entitlements import entitlement_key

pytestmark = pytest.mark.django_db

ADMIN = {"host": settings.ADMIN_HOST}
URL = "/api/v1/admin/customers"
type Capture = Callable[..., Any]


def detail_url(user: User, suffix: str = "") -> str:
    return f"{URL}/{user.pk}{suffix}"


def stored_entitlement(user: User) -> dict[str, Any] | None:
    raw = cast("bytes | None", state_redis().get(entitlement_key(user.pk)))
    return json.loads(raw) if raw else None


def client_for(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user)
    return client


# --- Permissions ---------------------------------------------------------------------------


def test_anonymous_customers_and_roleless_staff_are_refused(
    customer_user: User, make_admin: AdminFactory
) -> None:
    assert APIClient().get(URL, headers=ADMIN).status_code == 401
    assert client_for(customer_user).get(URL, headers=ADMIN).status_code == 403
    assert client_for(make_admin()).get(URL, headers=ADMIN).status_code == 403


def test_viewers_read_but_cannot_change(make_admin: AdminFactory) -> None:
    client = client_for(make_admin("viewer"))
    assert client.get(URL, headers=ADMIN).status_code == 200
    response = client.post(URL, {"name": "X"}, headers=ADMIN)
    assert response.status_code == 403
    assert response.json()["code"] == "PERMISSION_DENIED"


# --- List ---------------------------------------------------------------------------------


def test_list_is_one_query_per_page_whatever_the_size(
    owner_client: APIClient,
    make_customer: CustomerFactory,
    django_assert_num_queries: Capture,
) -> None:
    for _ in range(3):
        make_customer(devices=2)
    # Permission codes, count, page.
    with django_assert_num_queries(3):
        response = owner_client.get(URL, headers=ADMIN)
    assert response.json()["count"] == 3
    for _ in range(3):
        make_customer(devices=1)
    with django_assert_num_queries(3):
        response = owner_client.get(URL, headers=ADMIN)
    assert response.json()["count"] == 6


def test_list_shows_access_and_device_figures(
    owner_client: APIClient, make_customer: CustomerFactory
) -> None:
    ends = timezone.now() + timedelta(days=3)
    user = make_customer(name="Sara", expires_at=ends, devices=2, max_devices=3)
    services.revoke_device(Device.objects.filter(user=user).first(), actor=None)  # type: ignore[arg-type]
    Device.objects.filter(user=user, revoked_at__isnull=True).update(last_seen=ends)
    [row] = owner_client.get(URL, headers=ADMIN).json()["results"]
    assert row["name"] == "Sara"
    assert row["status"] == "active"
    assert row["access_status"] == "active"
    assert row["max_devices"] == 3
    assert row["device_count"] == 1
    assert row["last_seen"] is not None
    assert row["expires_at"] is not None
    assert row["username"].startswith("cus-")


def test_list_filters_and_search(owner_client: APIClient, make_customer: CustomerFactory) -> None:
    now = timezone.now()
    active = make_customer(name="Active Ali", expires_at=now + timedelta(days=30), devices=1)
    make_customer(name="Soon Sami", expires_at=now + timedelta(days=2))
    make_customer(name="Expired Eve", expires_at=now - timedelta(days=1))
    suspended = make_customer(name="Suspended Sue")
    services.suspend_customer(suspended, actor=None)
    make_customer(name="Open Omar")

    def names(query: str) -> set[str]:
        response = owner_client.get(f"{URL}?{query}", headers=ADMIN)
        assert response.status_code == 200, response.json()
        return {row["name"] for row in response.json()["results"]}

    assert names("access_status=active") == {"Active Ali", "Soon Sami", "Open Omar"}
    assert names("access_status=expired") == {"Expired Eve"}
    assert names("access_status=suspended") == {"Suspended Sue"}
    assert names("status=suspended") == {"Suspended Sue"}
    assert names("expiring_within_days=7") == {"Soon Sami"}
    assert names("search=soon") == {"Soon Sami"}
    username = active.devices.get().credential.username
    assert names(f"search={username}") == {"Active Ali"}
    ordered = owner_client.get(f"{URL}?ordering=expires_at", headers=ADMIN).json()["results"]
    assert [row["name"] for row in ordered][:3] == ["Expired Eve", "Soon Sami", "Active Ali"]
    response = owner_client.get(f"{URL}?access_status=bogus", headers=ADMIN)
    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_staff_are_not_customers(owner_client: APIClient, owner: User) -> None:
    assert owner_client.get(URL, headers=ADMIN).json()["count"] == 0
    response = owner_client.get(detail_url(owner), headers=ADMIN)
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


# --- Create ----------------------------------------------------------------------------------


def test_create_with_access_profile_and_first_device(
    owner_client: APIClient,
    owner: User,
    category: Category,
    django_capture_on_commit_callbacks: Capture,
) -> None:
    payload = {
        "name": "محمد الحربي",
        "email": "Mohammed@Example.com",
        "phone": "0501234567",
        "locale": "ar",
        "notes": "VIP",
        "access": {
            "expires_at": (timezone.now() + timedelta(days=30)).isoformat(),
            "max_streams": 2,
            "max_devices": 3,
            "max_quality": 720,
            "concurrency_policy": "kick_oldest",
            "allow_live": False,
            "category_ids": [str(category.pk)],
        },
        "device": {"name": "Living room", "app_hint": "tivimate"},
    }
    with django_capture_on_commit_callbacks(execute=True):
        response = owner_client.post(URL, payload, headers=ADMIN)
    assert response.status_code == 201, response.json()
    assert response["Cache-Control"] == "no-store"
    body = response.json()
    customer = body["customer"]
    assert customer["email"] == "mohammed@example.com"
    assert customer["phone"] == "+966501234567"
    assert customer["access"]["max_quality"] == 720
    assert customer["access"]["status"] == "active"
    assert [item["name_en"] for item in customer["access"]["categories"]] == ["Action"]
    [device] = customer["devices"]
    assert device["name"] == "Living room"
    assert device["status"] == "active"

    credential = body["credential"]
    assert credential["username"].startswith("moh-") or credential["username"].startswith("usr-")
    assert credential["username"] == device["xtream_username"]
    assert len(credential["password"]) == 16
    assert credential["server_url"].startswith("http")
    user = User.objects.get(pk=customer["id"])
    assert not user.has_usable_password()
    login = services.authenticate_xtream(credential["username"], credential["password"])
    assert login is not None
    assert login.user == user

    # The password is never stored or audited in clear.
    assert credential["password"] not in json.dumps(list(AuditLog.objects.values()), default=str)
    actions = set(AuditLog.objects.filter(actor=owner).values_list("action", flat=True))
    assert actions == {"customer.create", "device.create"}

    entitlement = stored_entitlement(user)
    assert entitlement is not None
    assert entitlement["status"] == "active"
    assert entitlement["max_streams"] == 2
    assert entitlement["allow_live"] is False
    assert entitlement["policy"] == "kick_oldest"
    assert entitlement["categories"] == [str(category.pk)]


def test_create_with_defaults_and_no_device(owner_client: APIClient) -> None:
    response = owner_client.post(URL, {"name": "Plain"}, headers=ADMIN)
    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["credential"] is None
    access = body["customer"]["access"]
    assert access["expires_at"] is None
    assert access["max_streams"] == 1
    assert access["max_devices"] == 2
    assert access["max_quality"] == 1080
    assert access["concurrency_policy"] == "reject"
    assert access["categories"] == []


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"name": ""}, "name"),
        ({"name": "A", "phone": "12"}, "phone"),
        ({"name": "A", "timezone": "Mars/Base"}, "timezone"),
        ({"name": "A", "access": {"max_streams": 0}}, "access.max_streams"),
        ({"name": "A", "access": {"max_quality": 999}}, "access.max_quality"),
        ({"name": "A", "device": {"app_hint": "nope"}}, "device.app_hint"),
    ],
)
def test_create_validates(owner_client: APIClient, payload: dict[str, Any], field: str) -> None:
    response = owner_client.post(URL, payload, headers=ADMIN)
    assert response.status_code == 400
    assert field in response.json()["field_errors"]
    assert not User.objects.filter(is_staff=False).exists()


def test_create_refuses_duplicate_emails_and_unknown_categories(
    owner_client: APIClient, make_customer: CustomerFactory
) -> None:
    make_customer()  # customer1@example.com
    response = owner_client.post(
        URL, {"name": "B", "email": "CUSTOMER1@example.com"}, headers=ADMIN
    )
    assert response.status_code == 400
    assert "email" in response.json()["field_errors"]
    response = owner_client.post(
        URL,
        {"name": "B", "access": {"category_ids": ["0190f2c6-0000-7000-8000-000000000000"]}},
        headers=ADMIN,
    )
    assert response.status_code == 400
    assert "category_ids" in response.json()["field_errors"]
    assert User.objects.filter(is_staff=False).count() == 1


# --- Retrieve and update ---------------------------------------------------------------------


def test_retrieve_has_access_and_devices(
    owner_client: APIClient,
    make_customer: CustomerFactory,
    django_assert_num_queries: Capture,
) -> None:
    user = make_customer(devices=2)
    # Permission codes, customer + access, categories, devices + credentials.
    with django_assert_num_queries(4):
        response = owner_client.get(detail_url(user), headers=ADMIN)
    body = response.json()
    assert len(body["devices"]) == 2
    assert all(device["xtream_username"] for device in body["devices"])
    assert body["access"]["max_devices"] == 2


def test_update_profile_is_audited(
    owner_client: APIClient, owner: User, make_customer: CustomerFactory
) -> None:
    user = make_customer(name="Old")
    response = owner_client.patch(
        detail_url(user), {"name": "New", "phone": "+966 50 123 4567"}, headers=ADMIN
    )
    assert response.status_code == 200, response.json()
    assert response.json()["name"] == "New"
    assert response.json()["phone"] == "+966501234567"
    entry = AuditLog.objects.get(action="customer.update")
    assert entry.actor == owner
    assert entry.before == {"name": "Old", "phone": ""}
    assert entry.after == {"name": "New", "phone": "+966501234567"}
    # Unchanged values record nothing.
    owner_client.patch(detail_url(user), {"name": "New"}, headers=ADMIN)
    assert AuditLog.objects.filter(action="customer.update").count() == 1


# --- Suspend and reactivate -------------------------------------------------------------------


def test_suspend_and_reactivate(
    owner_client: APIClient,
    make_customer: CustomerFactory,
    django_capture_on_commit_callbacks: Capture,
) -> None:
    user = make_customer()
    received: list[dict[str, Any]] = []

    def receiver(**kwargs: Any) -> None:
        received.append(kwargs)

    access_suspended.connect(receiver)
    try:
        with django_capture_on_commit_callbacks(execute=True):
            response = owner_client.post(
                detail_url(user, "/suspend"), {"reason": "Sharing"}, headers=ADMIN
            )
    finally:
        access_suspended.disconnect(receiver)
    assert response.status_code == 200
    assert response.json()["status"] == "suspended"
    assert response.json()["access"]["status"] == "suspended"
    assert [item["user_id"] for item in received] == [user.pk]
    assert stored_entitlement(user)["status"] == "suspended"  # type: ignore[index]
    entry = AuditLog.objects.get(action="customer.suspend")
    assert entry.after == {"status": "suspended", "reason": "Sharing"}

    with django_capture_on_commit_callbacks(execute=True):
        response = owner_client.post(detail_url(user, "/reactivate"), headers=ADMIN)
    assert response.json()["status"] == "active"
    assert stored_entitlement(user)["status"] == "active"  # type: ignore[index]
    # Repeating a no-op changes and audits nothing.
    owner_client.post(detail_url(user, "/reactivate"), headers=ADMIN)
    assert AuditLog.objects.filter(action="customer.reactivate").count() == 1


def test_disabled_customers_cannot_be_suspended(
    owner_client: APIClient, make_customer: CustomerFactory
) -> None:
    user = make_customer()
    User.objects.filter(pk=user.pk).update(status=UserStatus.DISABLED)
    response = owner_client.post(detail_url(user, "/suspend"), headers=ADMIN)
    assert response.status_code == 409
    assert response.json()["code"] == "CONFLICT"


# --- Access profile ------------------------------------------------------------------------------


def test_access_profile_update_refreshes_the_entitlement(
    owner_client: APIClient,
    owner: User,
    make_customer: CustomerFactory,
    category: Category,
    django_capture_on_commit_callbacks: Capture,
) -> None:
    user = make_customer()
    AccessRule.objects.create(user=user, type=AccessRuleType.COUNTRY_ALLOW, value="SA")
    response = owner_client.get(detail_url(user, "/access"), headers=ADMIN)
    assert response.json()["categories"] == []

    past = (timezone.now() - timedelta(minutes=1)).isoformat()
    with django_capture_on_commit_callbacks(execute=True):
        response = owner_client.patch(
            detail_url(user, "/access"),
            {"expires_at": past, "max_quality": 2160, "category_ids": [str(category.pk)]},
            headers=ADMIN,
        )
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["status"] == "expired"
    assert body["max_quality"] == 2160
    assert [item["id"] for item in body["categories"]] == [str(category.pk)]
    entitlement = stored_entitlement(user)
    assert entitlement is not None
    assert entitlement["status"] == "expired"
    assert entitlement["max_quality"] == 2160
    assert entitlement["country_rules"] == [
        {"type": "country_allow", "value": "SA", "expires_at": None}
    ]
    entry = AuditLog.objects.get(action="customer.access.update")
    assert entry.actor == owner
    assert set(entry.after or {}) == {"expires_at", "max_quality", "category_ids"}

    # An empty list means every category again; omitting it keeps them.
    owner_client.patch(detail_url(user, "/access"), {"max_streams": 3}, headers=ADMIN)
    assert (
        len(owner_client.get(detail_url(user, "/access"), headers=ADMIN).json()["categories"]) == 1
    )
    owner_client.patch(detail_url(user, "/access"), {"category_ids": []}, headers=ADMIN)
    assert owner_client.get(detail_url(user, "/access"), headers=ADMIN).json()["categories"] == []


def test_access_profile_is_created_on_demand(owner_client: APIClient, customer_user: User) -> None:
    response = owner_client.get(detail_url(customer_user, "/access"), headers=ADMIN)
    assert response.status_code == 200
    assert response.json()["max_streams"] == 1
    response = owner_client.patch(
        detail_url(customer_user, "/access"), {"max_streams": 99}, headers=ADMIN
    )
    assert response.status_code == 400
    assert "max_streams" in response.json()["field_errors"]
