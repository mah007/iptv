"""The admin OpenAPI schema (SPEC §8.4): served to staff, generated without warnings."""

import io
from typing import Any

import pytest
import yaml
from django.conf import settings
from django.core.management import call_command
from django.test import override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.core.errors import ErrorCode
from apps.core.pagination import PageNumberPagination

pytestmark = pytest.mark.django_db

ADMIN = {"host": settings.ADMIN_HOST}
SCHEMA_URL = "/api/v1/schema"


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    """Exactly what `make api-client` generates, with its strict flags."""
    out = io.StringIO()
    call_command(
        "spectacular", "--urlconf", "config.urls_admin", "--validate", "--fail-on-warn", stdout=out
    )
    loaded: dict[str, Any] = yaml.safe_load(out.getvalue())
    return loaded


def test_schema_is_openapi_3_1_without_warnings(schema: dict[str, Any]) -> None:
    assert schema["openapi"] == "3.1.0"
    assert schema["info"]["title"] == "Smart IPTV Admin API"
    assert schema["info"]["version"] == "1.0.0"


def test_operations_have_stable_ids(schema: dict[str, Any]) -> None:
    operation_ids = {
        operation["operationId"]
        for path_item in schema["paths"].values()
        for operation in path_item.values()
    }
    assert {"settings_list", "settings_update", "audit_list"} <= operation_ids


def test_every_operation_declares_problem_errors(schema: dict[str, Any]) -> None:
    for path_item in schema["paths"].values():
        for operation in path_item.values():
            default = operation["responses"]["default"]
            assert default["content"]["application/problem+json"]["schema"] == {
                "$ref": "#/components/schemas/Problem"
            }
            assert operation["security"] == [{"sessionAuth": []}]


def test_problem_and_error_codes_are_components(schema: dict[str, Any]) -> None:
    components = schema["components"]
    assert components["schemas"]["ErrorCode"]["enum"] == [code.value for code in ErrorCode]
    assert components["schemas"]["Problem"]["required"] == [
        "type",
        "title",
        "status",
        "code",
        "detail",
    ]
    assert "SettingKind" in components["schemas"]
    assert components["securitySchemes"]["sessionAuth"] == {
        "type": "apiKey",
        "in": "cookie",
        "name": settings.SESSION_COOKIE_NAME,
    }


def test_admin_schema_holds_only_the_admin_api(schema: dict[str, Any]) -> None:
    assert all(path.startswith("/api/v1/admin/") for path in schema["paths"])


def test_pagination_envelope_is_openapi_3_1() -> None:
    envelope = PageNumberPagination().get_paginated_response_schema({"type": "array"})
    assert envelope["required"] == ["count", "next", "previous", "results"]
    assert envelope["properties"]["next"]["type"] == ["string", "null"]


def test_schema_endpoint_is_staff_only(api_client: APIClient, customer_user: User) -> None:
    assert api_client.get(SCHEMA_URL, headers=ADMIN).status_code == 401
    api_client.force_login(customer_user)
    assert api_client.get(SCHEMA_URL, headers=ADMIN).status_code == 403


def test_staff_read_the_schema(api_client: APIClient, staff_user: User) -> None:
    api_client.force_login(staff_user)
    response = api_client.get(f"{SCHEMA_URL}?format=json", headers=ADMIN)
    assert response.status_code == 200
    assert response.json()["openapi"] == "3.1.0"
    assert "/api/v1/admin/settings" in response.json()["paths"]


@override_settings(DEBUG=True)
def test_debug_serves_the_schema_to_local_tooling(api_client: APIClient) -> None:
    assert api_client.get(SCHEMA_URL, headers=ADMIN).status_code == 200
