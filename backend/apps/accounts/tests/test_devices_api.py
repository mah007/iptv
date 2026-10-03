"""Devices and Xtream credentials: create, reset, block, approve, revoke (SPEC §6, §11)."""

import json
from collections.abc import Callable
from datetime import timedelta
from typing import Any, cast

import argon2
import pytest
from django.conf import settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts import credentials, services
from apps.accounts.models import AccessRule, Device, User, XtreamCredential
from apps.accounts.signals import device_disabled
from apps.audit.models import AuditLog
from apps.conftest import AdminFactory, CustomerFactory
from apps.core.errors import ProblemError
from apps.core.stores import state_redis
from apps.playback.entitlements import entitlement_key

pytestmark = pytest.mark.django_db

ADMIN = {"host": settings.ADMIN_HOST}
type Capture = Callable[..., Any]


def devices_url(user: User) -> str:
    return f"/api/v1/admin/customers/{user.pk}/devices"


def action_url(device: Device, action: str) -> str:
    return f"/api/v1/admin/devices/{device.pk}/{action}"


def test_list_devices_without_n_plus_one(
    owner_client: APIClient,
    make_customer: CustomerFactory,
    django_assert_num_queries: Capture,
) -> None:
    user = make_customer(devices=2, max_devices=5)
    # Permission codes, customer, count, page with credentials.
    with django_assert_num_queries(4):
        response = owner_client.get(devices_url(user), headers=ADMIN)
    assert response.json()["count"] == 2
    services.create_device_credential(user, actor=None)
    with django_assert_num_queries(4):
        response = owner_client.get(devices_url(user), headers=ADMIN)
    rows = response.json()["results"]
    assert len(rows) == 3
    assert all(row["xtream_username"] for row in rows)
    assert rows[2]["name"] == "Device 3"


def test_add_device_respects_max_devices(
    owner_client: APIClient, owner: User, make_customer: CustomerFactory
) -> None:
    user = make_customer(name="Sara Ali", devices=1, max_devices=2)
    response = owner_client.post(
        devices_url(user), {"name": "Phone", "app_hint": "smarters"}, headers=ADMIN
    )
    assert response.status_code == 201, response.json()
    assert response["Cache-Control"] == "no-store"
    body = response.json()
    assert body["username"].startswith("sar-")
    assert body["device"]["app_hint"] == "smarters"
    assert services.authenticate_xtream(body["username"], body["password"]) is not None
    entry = AuditLog.objects.get(action="device.create", actor=owner)
    assert entry.after is not None
    assert entry.after["xtream_username"] == body["username"]

    response = owner_client.post(devices_url(user), {}, headers=ADMIN)
    assert response.status_code == 409
    assert response.json()["code"] == "DEVICE_LIMIT"

    # Revoked devices free their slot.
    services.revoke_device(Device.objects.filter(user=user).first(), actor=None)  # type: ignore[arg-type]
    assert owner_client.post(devices_url(user), {}, headers=ADMIN).status_code == 201


def test_support_may_manage_devices_but_viewers_may_not(
    make_admin: AdminFactory, make_customer: CustomerFactory
) -> None:
    user = make_customer(devices=1)
    device = user.devices.get()
    viewer = APIClient()
    viewer.force_authenticate(make_admin("viewer"))
    assert viewer.get(devices_url(user), headers=ADMIN).status_code == 200
    assert viewer.post(action_url(device, "block"), headers=ADMIN).status_code == 403
    support = APIClient()
    support.force_authenticate(make_admin("support"))
    assert support.post(action_url(device, "block"), headers=ADMIN).status_code == 200


def test_reset_credentials_replaces_the_password(
    owner_client: APIClient, make_customer: CustomerFactory
) -> None:
    user = make_customer(devices=1)
    device = user.devices.get()
    old = XtreamCredential.objects.get(device=device)
    response = owner_client.post(action_url(device, "reset-credentials"), headers=ADMIN)
    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    body = response.json()
    assert body["username"] == old.username
    new = XtreamCredential.objects.get(device=device)
    assert new.password_hash != old.password_hash
    assert credentials.verify_password(new.password_hash, body["password"])
    assert AuditLog.objects.get(action="device.reset_credentials").after == {
        "xtream_username": old.username
    }


def test_reset_gives_a_credential_to_a_device_without_one(
    owner_client: APIClient, make_customer: CustomerFactory
) -> None:
    user = make_customer(devices=1)
    device = user.devices.get()
    XtreamCredential.objects.filter(device=device).delete()
    body = owner_client.post(action_url(device, "reset-credentials"), headers=ADMIN).json()
    assert body["username"].startswith("cus-")
    assert XtreamCredential.objects.get(device=device).username == body["username"]


def test_block_unblock_approve_and_revoke(
    owner_client: APIClient,
    make_customer: CustomerFactory,
    django_capture_on_commit_callbacks: Capture,
) -> None:
    user = make_customer(devices=1)
    device = user.devices.get()
    received: list[dict[str, Any]] = []

    def receiver(**kwargs: Any) -> None:
        received.append(kwargs)

    device_disabled.connect(receiver)
    try:
        with django_capture_on_commit_callbacks(execute=True):
            response = owner_client.post(
                action_url(device, "block"), {"reason": "Shared"}, headers=ADMIN
            )
        assert response.json()["status"] == "blocked"
        assert response.json()["blocked_reason"] == "Shared"
        with django_capture_on_commit_callbacks(execute=True):
            # Blocking again changes nothing and signals nothing.
            owner_client.post(action_url(device, "block"), {"reason": "Shared"}, headers=ADMIN)
        assert owner_client.post(action_url(device, "unblock"), headers=ADMIN).json()["status"] == (
            "active"
        )

        Device.objects.filter(pk=device.pk).update(approved=False)
        assert owner_client.get(devices_url(user), headers=ADMIN).json()["results"][0][
            "status"
        ] == ("pending")
        assert owner_client.post(action_url(device, "approve"), headers=ADMIN).json()["status"] == (
            "active"
        )

        with django_capture_on_commit_callbacks(execute=True):
            response = owner_client.post(action_url(device, "revoke"), headers=ADMIN)
        assert response.json()["status"] == "revoked"
        # Revoking twice is a no-op.
        owner_client.post(action_url(device, "revoke"), headers=ADMIN)
    finally:
        device_disabled.disconnect(receiver)

    assert [(item["user_id"], item["device_id"]) for item in received] == [
        (user.pk, device.pk),
        (user.pk, device.pk),
    ]
    assert XtreamCredential.objects.get(device=device).revoked_at is not None
    actions = list(
        AuditLog.objects.filter(target_id=str(device.pk))
        .order_by("at", "id")
        .values_list("action", flat=True)
    )
    assert actions == [
        "device.create",
        "device.block",
        "device.unblock",
        "device.approve",
        "device.revoke",
    ]
    response = owner_client.post(action_url(device, "block"), headers=ADMIN)
    assert response.status_code == 409
    response = owner_client.post(action_url(device, "reset-credentials"), headers=ADMIN)
    assert response.status_code == 409


def test_unknown_devices_are_404(owner_client: APIClient, owner: User) -> None:
    response = owner_client.post(
        "/api/v1/admin/devices/0190f2c6-0000-7000-8000-000000000000/block", headers=ADMIN
    )
    assert response.status_code == 404
    response = owner_client.get(devices_url(owner), headers=ADMIN)
    assert response.status_code == 404


# --- Xtream sign-in (used by the Xtream API from M5) -------------------------------------------


def test_authenticate_xtream(
    make_customer: CustomerFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = make_customer(devices=0)
    issued = services.create_device_credential(user, actor=None)
    calls: list[str] = []
    real_verify = credentials.verify_password

    def counting_verify(password_hash: str, password: str) -> bool:
        calls.append(password)
        return real_verify(password_hash, password)

    monkeypatch.setattr(credentials, "verify_password", counting_verify)

    login = services.authenticate_xtream(issued.username, issued.password)
    assert login is not None
    assert (login.user, login.device) == (user, issued.device)
    assert XtreamCredential.objects.get(username=issued.username).last_used_at is not None
    # A cached success skips Argon2id; the cache key never holds the password.
    assert services.authenticate_xtream(issued.username, issued.password) is not None
    assert len(calls) == 1
    keys = [key.decode() for key in cast("list[bytes]", state_redis().keys("xauth:*"))]
    assert len(keys) == 1
    assert issued.password not in keys[0]
    assert issued.username not in keys[0]

    assert services.authenticate_xtream(issued.username, "wrong-password-xx") is None
    assert services.authenticate_xtream("nobody-123456", issued.password) is None
    assert len(calls) == 3  # unknown usernames still pay for one verification

    # A reset invalidates the cached success at once.
    reset = services.reset_credential(issued.device, actor=None)
    assert services.authenticate_xtream(issued.username, issued.password) is None
    assert services.authenticate_xtream(issued.username, reset.password) is not None

    services.revoke_device(issued.device, actor=None)
    assert services.authenticate_xtream(issued.username, reset.password) is None


def test_authenticate_xtream_upgrades_old_hashes(make_customer: CustomerFactory) -> None:
    user = make_customer()
    issued = services.create_device_credential(user, actor=None)
    weak = argon2.PasswordHasher(time_cost=1, memory_cost=8 * 1024, parallelism=1)
    XtreamCredential.objects.filter(username=issued.username).update(
        password_hash=weak.hash(issued.password)
    )
    assert services.authenticate_xtream(issued.username, issued.password) is not None
    stored = XtreamCredential.objects.get(username=issued.username).password_hash
    assert not credentials.needs_rehash(stored)


# --- Access rules -------------------------------------------------------------------------------

RULES = "/api/v1/admin/access-rules"


def test_access_rules_lifecycle(
    owner_client: APIClient,
    owner: User,
    make_customer: CustomerFactory,
    django_capture_on_commit_callbacks: Capture,
    django_assert_num_queries: Capture,
) -> None:
    user = make_customer()
    with django_capture_on_commit_callbacks(execute=True):
        response = owner_client.post(
            RULES,
            {"user": str(user.pk), "type": "cidr_deny", "value": "203.0.113.77/24"},
            headers=ADMIN,
        )
    assert response.status_code == 201, response.json()
    rule = response.json()
    assert rule["value"] == "203.0.113.0/24"
    assert rule["user"] == str(user.pk)
    raw = state_redis().get(entitlement_key(user.pk))
    assert json.loads(raw)["ip_rules"][0]["value"] == "203.0.113.0/24"  # type: ignore[arg-type]

    future = (timezone.now() + timedelta(days=1)).isoformat()
    response = owner_client.post(
        RULES,
        {"type": "country_deny", "value": "ir", "reason": "Licence", "expires_at": future},
        headers=ADMIN,
    )
    assert response.status_code == 201
    assert response.json()["user"] is None
    assert response.json()["value"] == "IR"

    with django_assert_num_queries(3):
        response = owner_client.get(RULES, headers=ADMIN)
    assert response.json()["count"] == 2
    assert owner_client.get(f"{RULES}?scope=global", headers=ADMIN).json()["count"] == 1
    assert owner_client.get(f"{RULES}?user={user.pk}", headers=ADMIN).json()["count"] == 1

    with django_capture_on_commit_callbacks(execute=True):
        response = owner_client.delete(f"{RULES}/{rule['id']}", headers=ADMIN)
    assert response.status_code == 204
    assert json.loads(state_redis().get(entitlement_key(user.pk)))["ip_rules"] == []  # type: ignore[arg-type]
    assert set(AuditLog.objects.filter(actor=owner).values_list("action", flat=True)) == {
        "access_rule.create",
        "access_rule.delete",
    }
    assert AccessRule.objects.count() == 1


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"type": "ip_deny", "value": "999.1.1.1"}, "value"),
        ({"type": "cidr_deny", "value": "10.0.0.0/99"}, "value"),
        ({"type": "country_allow", "value": "XX"}, "value"),
        ({"type": "nope", "value": "SA"}, "type"),
        (
            {"type": "ip_allow", "value": "10.0.0.1", "expires_at": "2000-01-01T00:00:00Z"},
            "expires_at",
        ),
        (
            {
                "type": "ip_allow",
                "value": "10.0.0.1",
                "user": "0190f2c6-0000-7000-8000-000000000000",
            },
            "user",
        ),
    ],
)
def test_access_rules_are_validated(
    owner_client: APIClient, payload: dict[str, Any], field: str
) -> None:
    response = owner_client.post(RULES, payload, headers=ADMIN)
    assert response.status_code == 400
    assert field in response.json()["field_errors"]
    assert not AccessRule.objects.exists()


def test_access_rules_apply_to_customers_only(owner: User) -> None:
    with pytest.raises(ProblemError):
        services.create_access_rule(user=owner, rule_type="ip_deny", value="10.0.0.1", actor=None)
