"""Stable `field_error_codes` on the account forms, so the admin can translate them (ADR-0015)."""

from datetime import timedelta
from typing import Any

import pytest
from django.conf import settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts import services
from apps.accounts.models import User
from apps.conftest import CustomerFactory

pytestmark = pytest.mark.django_db

ADMIN = {"host": settings.ADMIN_HOST}
CUSTOMERS = "/api/v1/admin/customers"
RULES = "/api/v1/admin/access-rules"


def codes(response: Any) -> dict[str, list[str]]:
    assert response.status_code == 400, response.json()
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert set(body["field_error_codes"]) == set(body["field_errors"])
    for name, messages in body["field_errors"].items():
        assert len(body["field_error_codes"][name]) == len(messages)
    return dict(body["field_error_codes"])


def post(client: APIClient, url: str, payload: dict[str, Any]) -> Any:
    return client.post(url, payload, headers=ADMIN, format="json")


@pytest.mark.parametrize(
    ("device", "field", "expected"),
    [
        ({"username": "ab"}, "device.username", ["username_rule"]),
        ({"password": "short1"}, "device.password", ["password_length"]),
        ({"password": "pass word 99"}, "device.password", ["password_characters"]),
        (
            {"password": "pw?"},
            "device.password",
            ["password_length", "password_characters"],
        ),
        (
            {"username": "samepass1", "password": "SamePass1"},
            "device.password",
            ["password_equals_username"],
        ),
    ],
)
def test_chosen_credentials_have_codes(
    owner_client: APIClient, device: dict[str, str], field: str, expected: list[str]
) -> None:
    assert codes(post(owner_client, CUSTOMERS, {"name": "Refused", "device": device}))[field] == (
        expected
    )


def test_taken_and_malformed_usernames_have_codes(
    owner_client: APIClient, make_customer: CustomerFactory
) -> None:
    first = make_customer(name="First")
    services.create_device_credential(
        first,
        username="living.room",
        password="first-pass-1",  # noqa: S106
        actor=None,
    )
    assert post(owner_client, CUSTOMERS, {"name": "A", "username": "taken.name"}).status_code == 201
    assert codes(post(owner_client, CUSTOMERS, {"name": "B", "username": "TAKEN.NAME"})) == {
        "username": ["username_taken"]
    }
    assert codes(post(owner_client, CUSTOMERS, {"name": "C", "username": "no spaces"})) == {
        "username": ["customer_username_rule"]
    }
    user = User.objects.get(name="A")
    devices = f"/api/v1/admin/customers/{user.pk}/devices"
    assert codes(post(owner_client, devices, {"username": "Living.Room"})) == {
        "username": ["username_taken"]
    }


def test_profile_fields_use_drf_and_own_codes(
    owner_client: APIClient, make_customer: CustomerFactory
) -> None:
    make_customer(name="Holder")  # customer1@example.com
    result = codes(
        post(
            owner_client,
            CUSTOMERS,
            {
                "email": "CUSTOMER1@example.com",
                "phone": "12",
                "timezone": "Mars/Base",
                "locale": "fr",
            },
        )
    )
    assert result == {
        "name": ["required"],
        "email": ["email_taken"],
        "phone": ["invalid_phone"],
        "timezone": ["invalid_timezone"],
        "locale": ["invalid_choice"],
    }


def test_access_rules_have_codes(owner_client: APIClient, make_customer: CustomerFactory) -> None:
    assert codes(post(owner_client, RULES, {"type": "ip_deny", "value": "999.1.1.1"})) == {
        "value": ["invalid_ip"]
    }
    assert codes(post(owner_client, RULES, {"type": "cidr_deny", "value": "10.0.0.0/99"})) == {
        "value": ["invalid_cidr"]
    }
    assert codes(post(owner_client, RULES, {"type": "country_deny", "value": "XX"})) == {
        "value": ["invalid_country"]
    }
    past = (timezone.now() - timedelta(days=1)).isoformat()
    assert codes(
        post(owner_client, RULES, {"type": "ip_deny", "value": "10.0.0.1", "expires_at": past})
    ) == {"expires_at": ["not_in_future"]}
    unknown = "01890000-0000-7000-8000-000000000000"
    assert codes(
        post(owner_client, RULES, {"type": "ip_deny", "value": "10.0.0.1", "user": unknown})
    ) == {"user": ["does_not_exist"]}


def test_unknown_categories_and_roles_have_codes(owner_client: APIClient) -> None:
    unknown = "01890000-0000-7000-8000-000000000000"
    assert codes(
        post(owner_client, CUSTOMERS, {"name": "D", "access": {"category_ids": [unknown]}})
    ) == {"category_ids": ["does_not_exist"]}
    assert codes(
        post(owner_client, "/api/v1/admin/roles", {"name": "9bad", "permissions": []})
    ) == {"name": ["role_name_rule"]}
    assert codes(
        post(owner_client, "/api/v1/admin/roles", {"name": "owner", "permissions": []})
    ) == {"name": ["role_name_taken"]}
