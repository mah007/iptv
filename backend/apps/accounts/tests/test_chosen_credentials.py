"""Admin-chosen usernames and passwords for customers and their IPTV app devices."""

import json
from typing import Any

import pytest
from django.conf import settings
from rest_framework.test import APIClient

from apps.accounts import services
from apps.accounts.models import Device, User, XtreamCredential
from apps.audit.models import AuditLog
from apps.conftest import CustomerFactory
from apps.core.services import set_setting

pytestmark = pytest.mark.django_db

ADMIN = {"host": settings.ADMIN_HOST}
CUSTOMERS = "/api/v1/admin/customers"


def devices_url(user: User) -> str:
    return f"/api/v1/admin/customers/{user.pk}/devices"


def reset_url(device: Device) -> str:
    return f"/api/v1/admin/devices/{device.pk}/reset-credentials"


def field_errors(response: Any) -> dict[str, list[str]]:
    assert response.status_code == 400, response.json()
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    return dict(body["field_errors"])


def no_audit_entry_holds(secret: str) -> bool:
    rows = AuditLog.objects.values_list("before", "after")
    return all(secret not in json.dumps(row, ensure_ascii=False) for row in rows)


def test_wizard_uses_the_chosen_usernames_and_password(owner_client: APIClient) -> None:
    payload = {
        "name": "Ahmed Saleh",
        "username": "ahmed.saleh",
        "device": {"name": "TV", "username": "Ahmed_TV", "password": "Tv-Remote~2026"},
    }
    response = owner_client.post(CUSTOMERS, payload, headers=ADMIN, format="json")
    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["customer"]["username"] == "ahmed.saleh"
    assert body["credential"]["username"] == "Ahmed_TV"
    assert body["credential"]["password"] == "Tv-Remote~2026"  # noqa: S105
    assert services.authenticate_xtream("Ahmed_TV", "Tv-Remote~2026") is not None
    # IPTV apps send exactly what was typed: the case must match.
    assert services.authenticate_xtream("ahmed_tv", "Tv-Remote~2026") is None
    assert no_audit_entry_holds("Tv-Remote~2026")


def test_wizard_still_generates_when_nothing_is_chosen(owner_client: APIClient) -> None:
    payload = {"name": "Sara Ali", "username": "", "device": {"username": "", "password": ""}}
    response = owner_client.post(CUSTOMERS, payload, headers=ADMIN, format="json")
    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["customer"]["username"].startswith("cus-")
    assert body["credential"]["username"].startswith("sar-")
    assert len(body["credential"]["password"]) == 16


@pytest.mark.parametrize(
    ("device", "field", "fragment"),
    [
        ({"username": "ab"}, "device.username", "3 to 32"),
        ({"username": "has space"}, "device.username", "3 to 32"),
        ({"username": "slash/name"}, "device.username", "3 to 32"),
        ({"username": "عربي-اسم"}, "device.username", "3 to 32"),
        ({"password": "short1"}, "device.password", "8 to 64"),
        ({"password": "pass word 99"}, "device.password", "only letters"),
        ({"password": "pass?word#99"}, "device.password", "only letters"),
        ({"password": "pass&word=99"}, "device.password", "only letters"),
        ({"username": "samepass1", "password": "SamePass1"}, "device.password", "differ"),
    ],
)
def test_wizard_refuses_unsafe_choices_and_creates_nothing(
    owner_client: APIClient, device: dict[str, str], field: str, fragment: str
) -> None:
    payload = {"name": "Refused", "device": device}
    errors = field_errors(owner_client.post(CUSTOMERS, payload, headers=ADMIN, format="json"))
    assert any(fragment in message for message in errors[field]), errors
    assert not User.objects.filter(name="Refused").exists()


def test_usernames_are_unique_regardless_of_case(
    owner_client: APIClient, make_customer: CustomerFactory
) -> None:
    first = make_customer(name="First")
    services.create_device_credential(
        first,
        username="Living.Room",
        password="first-pass-1",  # noqa: S106
        actor=None,
    )

    payload = {"name": "Second", "username": "FIRST-ACCOUNT"}
    assert owner_client.post(CUSTOMERS, payload, headers=ADMIN, format="json").status_code == 201
    errors = field_errors(
        owner_client.post(
            CUSTOMERS, {"name": "Third", "username": "first-account"}, headers=ADMIN, format="json"
        )
    )
    assert errors["username"] == ["This username is already taken."]

    second = User.objects.get(name="Second")
    errors = field_errors(
        owner_client.post(
            devices_url(second), {"username": "living.room"}, headers=ADMIN, format="json"
        )
    )
    assert errors["username"] == ["This username is already taken."]


def test_add_device_with_chosen_credentials(
    owner_client: APIClient, make_customer: CustomerFactory
) -> None:
    user = make_customer(name="Mona", max_devices=3)
    response = owner_client.post(
        devices_url(user),
        {"name": "Phone", "username": "mona-phone", "password": "Mona@Phone*1"},
        headers=ADMIN,
        format="json",
    )
    assert response.status_code == 201, response.json()
    assert response.json()["username"] == "mona-phone"
    assert services.authenticate_xtream("mona-phone", "Mona@Phone*1") is not None
    assert no_audit_entry_holds("Mona@Phone*1")


def test_minimum_password_length_is_a_setting(
    owner_client: APIClient, make_customer: CustomerFactory
) -> None:
    user = make_customer(name="Long", max_devices=3)
    set_setting("xtream.password_min_length", 12, actor=None)
    errors = field_errors(
        owner_client.post(
            devices_url(user), {"password": "ten-chars1"}, headers=ADMIN, format="json"
        )
    )
    assert errors["password"] == ["Use 12 to 64 characters."]
    response = owner_client.post(
        devices_url(user), {"password": "twelve-chars"}, headers=ADMIN, format="json"
    )
    assert response.status_code == 201, response.json()


def test_reset_with_a_chosen_password_keeps_the_username(
    owner_client: APIClient, make_customer: CustomerFactory
) -> None:
    user = make_customer(devices=1)
    device = user.devices.get()
    username = XtreamCredential.objects.get(device=device).username
    old = owner_client.post(reset_url(device), headers=ADMIN).json()["password"]

    response = owner_client.post(
        reset_url(device), {"password": "Chosen-Pass.9"}, headers=ADMIN, format="json"
    )
    assert response.status_code == 200, response.json()
    assert response.json()["username"] == username
    assert services.authenticate_xtream(username, "Chosen-Pass.9") is not None
    assert services.authenticate_xtream(username, old) is None


def test_reset_can_rename_the_login(
    owner_client: APIClient, make_customer: CustomerFactory
) -> None:
    user = make_customer(devices=1)
    device = user.devices.get()
    credential = XtreamCredential.objects.get(device=device)
    old_username = credential.username
    password = owner_client.post(reset_url(device), headers=ADMIN).json()["password"]
    assert services.authenticate_xtream(old_username, password) is not None  # now cached

    response = owner_client.post(
        reset_url(device),
        {"username": "Renamed.Box", "password": "Renamed-Pass1"},
        headers=ADMIN,
        format="json",
    )
    assert response.status_code == 200, response.json()
    assert response.json()["username"] == "Renamed.Box"
    assert services.authenticate_xtream(old_username, password) is None
    assert services.authenticate_xtream("Renamed.Box", "Renamed-Pass1") is not None
    entry = AuditLog.objects.filter(action="device.reset_credentials").latest("at")
    assert entry.before == {"xtream_username": old_username}
    assert entry.after == {"xtream_username": "Renamed.Box"}

    # Changing only the case of its own name is allowed; another device's name is not.
    other = make_customer(devices=1).devices.get()
    taken = XtreamCredential.objects.get(device=other).username
    errors = field_errors(
        owner_client.post(
            reset_url(device), {"username": taken.upper()}, headers=ADMIN, format="json"
        )
    )
    assert errors["username"] == ["This username is already taken."]
    response = owner_client.post(
        reset_url(device), {"username": "renamed.box"}, headers=ADMIN, format="json"
    )
    assert response.status_code == 200, response.json()
    assert response.json()["username"] == "renamed.box"


def test_password_never_reaches_the_audit_log_on_reset(
    owner_client: APIClient, make_customer: CustomerFactory
) -> None:
    device = make_customer(devices=1).devices.get()
    owner_client.post(
        reset_url(device), {"password": "Secret-Reset-7"}, headers=ADMIN, format="json"
    )
    assert no_audit_entry_holds("Secret-Reset-7")
