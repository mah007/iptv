"""GET/PATCH /api/v1/admin/settings on the admin host (plan §2.5)."""

from types import MappingProxyType
from typing import Any

import pytest
from django.conf import settings
from django.test import override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.core import registry
from apps.core.models import Setting
from apps.core.registry import SettingDef, SettingKind
from apps.core.services import get_setting

pytestmark = pytest.mark.django_db

ADMIN = {"host": settings.ADMIN_HOST}
LIST_URL = "/api/v1/admin/settings"
CSRF_TOKEN = "a" * 32  # a valid CSRF secret; Django compares header and cookie


def url(key: str) -> str:
    return f"{LIST_URL}/{key}"


@pytest.fixture
def staff_client(staff_user: User) -> APIClient:
    client = APIClient()
    client.force_login(staff_user)
    return client


def test_anonymous_requests_get_a_401_problem(api_client: APIClient) -> None:
    response = api_client.get(LIST_URL, headers=ADMIN)
    assert response.status_code == 401
    assert response["Content-Type"] == "application/problem+json"
    assert response["WWW-Authenticate"] == 'Session realm="api"'
    assert response.json()["code"] == "NOT_AUTHENTICATED"


def test_non_staff_users_are_refused(api_client: APIClient, customer_user: User) -> None:
    api_client.force_login(customer_user)
    response = api_client.get(LIST_URL, headers=ADMIN)
    assert response.status_code == 403
    assert response.json()["code"] == "PERMISSION_DENIED"
    response = api_client.patch(url("xtream.port"), {"value": 8080}, headers=ADMIN)
    assert response.status_code == 403
    assert not Setting.objects.exists()


def test_staff_list_every_setting(staff_client: APIClient) -> None:
    response = staff_client.get(LIST_URL, headers=ADMIN)
    assert response.status_code == 200
    entries = {entry["key"]: entry for entry in response.json()}
    assert list(entries) == list(registry.REGISTRY)
    assert entries["xtream.port"] == {
        "key": "xtream.port",
        "group": "xtream",
        "kind": "int",
        "description": "HTTP port reported to IPTV apps.",
        "default": 80,
        "value": 80,
        "is_default": True,
        "sensitive": False,
        "min_value": 1.0,
        "max_value": 65535.0,
        "choices": None,
        "updated_at": None,
        "updated_by": None,
    }


def test_the_list_costs_two_queries(
    api_client: APIClient, staff_user: User, django_assert_num_queries: Any
) -> None:
    api_client.force_authenticate(staff_user)
    # The admin's permission codes, then the settings.
    with django_assert_num_queries(2):
        assert api_client.get(LIST_URL, headers=ADMIN).status_code == 200


def test_sensitive_values_are_never_sent(
    staff_client: APIClient, staff_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = "metadata.tmdb_api_key"
    definition = SettingDef(key, SettingKind.STR, "", "TMDB API key.", "metadata", sensitive=True)
    monkeypatch.setattr(
        registry, "REGISTRY", MappingProxyType({**registry.REGISTRY, key: definition})
    )
    Setting.objects.create(key=key, value="tmdb-secret", updated_by=staff_user)

    listed = {entry["key"]: entry for entry in staff_client.get(LIST_URL, headers=ADMIN).json()}
    assert listed[key]["value"] is None
    assert listed[key]["default"] is None
    assert listed[key]["sensitive"] is True
    assert listed[key]["is_default"] is False
    patched = staff_client.patch(url(key), {"value": "tmdb-new"}, headers=ADMIN)
    assert patched.status_code == 200
    assert "tmdb-new" not in patched.content.decode()


def test_staff_change_a_setting(staff_client: APIClient, staff_user: User) -> None:
    response = staff_client.patch(
        url("playback.token_ttl_vod_s"),
        {"value": 3600},
        headers={**ADMIN, "x-forwarded-for": "198.51.100.23"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["value"] == 3600
    assert body["is_default"] is False
    assert body["updated_by"] == "staff"
    assert body["updated_at"] is not None
    assert get_setting("playback.token_ttl_vod_s") == 3600
    entry = AuditLog.objects.get()
    assert entry.actor == staff_user
    assert entry.actor_ip == "198.51.100.23"
    assert entry.after == {"value": 3600}


def test_an_empty_patch_changes_nothing(staff_client: APIClient) -> None:
    response = staff_client.patch(url("billing.grace_days"), {}, headers=ADMIN)
    assert response.status_code == 200
    assert response.json()["value"] == 3
    assert not AuditLog.objects.exists()


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("xtream.port", "8080", "Expected a value of type int."),
        ("xtream.port", 0, "Must be at least 1."),
        ("billing.vat_rate", 1.5, "Must be at most 1."),
        ("branding.accent_color", "teal", "Enter a colour as #RRGGBB."),
        ("playback.ip_binding", "yes", "Expected a value of type bool."),
        ("xtream.port", None, "This field may not be null."),
    ],
)
def test_invalid_values_are_validation_problems(
    staff_client: APIClient, key: str, value: object, message: str
) -> None:
    response = staff_client.patch(url(key), {"value": value}, headers=ADMIN)
    assert response.status_code == 400
    assert response["Content-Type"] == "application/problem+json"
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["detail"] == message
    assert body["field_errors"] == {"value": [message]}
    assert not Setting.objects.exists()


def test_unknown_settings_are_404s(staff_client: APIClient) -> None:
    for payload in ({"value": 1}, {}):
        response = staff_client.patch(url("billing.nope"), payload, headers=ADMIN)
        assert response.status_code == 404
        assert response.json() == {
            "type": "urn:smart-iptv:problem:not-found",
            "title": "Not found",
            "status": 404,
            "code": "NOT_FOUND",
            "detail": "Unknown setting billing.nope.",
        }


def test_only_get_and_patch_are_allowed(staff_client: APIClient) -> None:
    assert staff_client.post(LIST_URL, {}, headers=ADMIN).json()["code"] == "METHOD_NOT_ALLOWED"
    assert staff_client.delete(url("xtream.port"), headers=ADMIN).status_code == 405


def test_the_settings_api_lives_only_on_the_admin_host(staff_client: APIClient) -> None:
    for host in (settings.API_HOST, settings.APP_HOST, settings.TV_HOST, "web"):
        assert staff_client.get(LIST_URL, headers={"host": host}).status_code == 404


# --- CSRF (ADR-0004: session cookie + CSRF, same origin) ---------------------------------


@pytest.fixture
def csrf_client(staff_user: User) -> APIClient:
    client = APIClient(enforce_csrf_checks=True)
    client.force_login(staff_user)
    client.cookies[settings.CSRF_COOKIE_NAME] = CSRF_TOKEN
    return client


def test_unsafe_requests_need_the_csrf_header(csrf_client: APIClient) -> None:
    response = csrf_client.patch(url("billing.grace_days"), {"value": 4}, headers=ADMIN)
    assert response.status_code == 403
    assert response["Content-Type"] == "application/problem+json"
    assert response.json()["code"] == "PERMISSION_DENIED"
    assert "CSRF" in response.json()["detail"]
    assert not Setting.objects.exists()


def test_the_csrf_header_from_the_cookie_is_accepted(csrf_client: APIClient) -> None:
    response = csrf_client.patch(
        url("billing.grace_days"), {"value": 4}, headers={**ADMIN, "x-csrftoken": CSRF_TOKEN}
    )
    assert response.status_code == 200
    assert response.json()["value"] == 4


def test_reads_need_no_csrf_header(csrf_client: APIClient) -> None:
    assert csrf_client.get(LIST_URL, headers=ADMIN).status_code == 200


def test_cross_site_origins_are_refused(csrf_client: APIClient) -> None:
    response = csrf_client.patch(
        url("billing.grace_days"),
        {"value": 4},
        headers={**ADMIN, "x-csrftoken": CSRF_TOKEN, "origin": "https://evil.example.com"},
    )
    assert response.status_code == 403
    assert "Origin" in response.json()["detail"]


@override_settings(CSRF_TRUSTED_ORIGINS=["http://admin.localhost:8080"])
def test_trusted_origins_cover_the_dev_port(csrf_client: APIClient) -> None:
    # A proxy that drops the port from Host still passes with the configured origin.
    response = csrf_client.patch(
        url("billing.grace_days"),
        {"value": 4},
        headers={
            **ADMIN,
            "x-csrftoken": CSRF_TOKEN,
            "origin": "http://admin.localhost:8080",
        },
    )
    assert response.status_code == 200
