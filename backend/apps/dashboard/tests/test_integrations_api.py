"""The Integrations and System pages' API (CFG, ADR-0019)."""

from typing import Any
from unittest import mock

import httpx
import pytest
from django.conf import settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.core.integration import TestCode, TestResult, use_transport
from apps.core.services import set_setting

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("no_server_config")]

ADMIN = {"host": settings.ADMIN_HOST}
LIST = "/api/v1/admin/integrations"
SYSTEM = "/api/v1/admin/system"
BOT = "123456789:" + "C" * 35
# Built from parts so secret scanners (GitHub push protection) see no key-shaped literal.
STRIPE_KEY = "sk_" + "test_" + "51ABCDEFGHIJKLMNOPQRSTUV"


def check_url(name: str) -> str:
    return f"{LIST}/{name}/test"


@pytest.fixture
def client(staff_user: User) -> APIClient:
    api = APIClient()
    api.force_login(staff_user)
    return api


def cards(client: APIClient) -> dict[str, Any]:
    response = client.get(LIST, headers=ADMIN)
    assert response.status_code == 200
    return {card["name"]: card for card in response.json()}


def test_every_integration_starts_not_set(client: APIClient) -> None:
    listed = cards(client)
    assert list(listed) == ["tmdb", "tvdb", "email", "stripe", "moyasar", "telegram"]
    assert {card["status"] for card in listed.values()} == {"not_set"}
    stripe = listed["stripe"]
    assert stripe["enabled"] is False
    assert stripe["webhook_url"].endswith("/api/v1/webhooks/stripe")
    assert [entry["key"] for entry in stripe["settings"]] == [
        "billing.stripe_secret_key",
        "billing.stripe_webhook_secret",
        "billing.stripe_enabled",
    ]
    assert listed["email"]["enabled"] is None


def test_statuses_follow_the_settings(client: APIClient, settings: Any) -> None:
    set_setting("billing.stripe_secret_key", STRIPE_KEY, actor=None)
    set_setting("notifications.telegram_bot_token", BOT, actor=None)
    set_setting("notifications.telegram_chat_id", "-100123", actor=None)
    settings.TMDB_API_KEY = "0123456789abcdef0123456789abcdef"
    listed = cards(client)
    assert listed["stripe"]["status"] == "partial"
    assert listed["telegram"]["status"] == "configured"
    assert listed["tmdb"]["status"] == "server_config"
    body = client.get(LIST, headers=ADMIN).content.decode()
    assert STRIPE_KEY not in body
    assert BOT not in body
    assert "0123456789abcdef0123456789abcdef" not in body


def test_the_list_needs_settings_view(make_admin: Any) -> None:
    api = APIClient()
    api.force_login(make_admin("support"))
    assert api.get(LIST, headers=ADMIN).status_code == 403
    assert api.get(SYSTEM, headers=ADMIN).status_code == 403
    viewer = make_admin(permissions=["settings.view"])
    api.force_login(viewer)
    assert api.get(LIST, headers=ADMIN).status_code == 200
    assert api.post(check_url("telegram"), headers=ADMIN).status_code == 403


def test_a_telegram_test_runs_with_the_saved_settings(client: APIClient) -> None:
    set_setting("notifications.telegram_bot_token", BOT, actor=None)
    set_setting("notifications.telegram_chat_id", "-100123", actor=None)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getMe"):
            return httpx.Response(200, json={"ok": True, "result": {"username": "iptv_bot"}})
        return httpx.Response(200, json={"ok": True})

    with use_transport(httpx.MockTransport(handler)):
        response = client.post(check_url("telegram"), headers=ADMIN)
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "code": "ok",
        "message": "Sent by @iptv_bot.",
        "details": {"bot": "iptv_bot"},
    }
    assert BOT not in response.content.decode()
    entry = AuditLog.objects.get(action="integration.test")
    assert (entry.target_type, entry.target_id) == ("integration", "telegram")
    assert entry.after == {"ok": True, "code": "ok"}


def test_a_failed_test_is_still_a_200_with_a_code(client: APIClient) -> None:
    response = client.post(check_url("stripe"), headers=ADMIN)
    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["code"] == "not_configured"


def test_the_email_test_goes_to_the_signed_in_admin(client: APIClient, staff_user: User) -> None:
    with mock.patch(
        "apps.notifications.mail.send_test_email",
        return_value=TestResult.passed("Sent.", reference="abcd1234"),
    ) as send:
        response = client.post(check_url("email"), headers=ADMIN)
    send.assert_called_once_with(staff_user.email)
    assert response.json()["details"] == {"reference": "abcd1234"}


@pytest.mark.parametrize(
    ("name", "target"),
    [
        ("tmdb", "apps.metadata.checks.check_tmdb"),
        ("tvdb", "apps.metadata.checks.check_tvdb"),
        ("stripe", "apps.billing.providers.stripe.StripeProvider.check"),
        ("moyasar", "apps.billing.providers.moyasar.MoyasarProvider.check"),
    ],
)
def test_each_card_runs_its_own_check(client: APIClient, name: str, target: str) -> None:
    result = TestResult.failed(TestCode.AUTH_FAILED, "refused")
    with mock.patch(target, return_value=result) as check:
        response = client.post(check_url(name), headers=ADMIN)
    check.assert_called_once()
    assert response.json()["code"] == "auth_failed"


def test_tests_are_rate_limited(client: APIClient) -> None:
    for _ in range(5):
        assert client.post(check_url("stripe"), headers=ADMIN).status_code == 200
    limited = client.post(check_url("stripe"), headers=ADMIN)
    assert limited.status_code == 429
    assert limited.json()["code"] == "RATE_LIMITED"
    assert client.post(check_url("tvdb"), headers=ADMIN).status_code == 200  # per integration


def test_unknown_integrations_are_404s(client: APIClient) -> None:
    assert client.post(check_url("paypal"), headers=ADMIN).status_code == 404


# --- System -------------------------------------------------------------------------------------


def test_the_system_page_lists_facts_without_secrets(
    client: APIClient, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.APP_COMMIT = "abc1234"
    monkeypatch.setenv("MEDIA_ROOT", "/srv/media")
    monkeypatch.setenv("POSTGRES_PASSWORD", "pg-secret-value")
    monkeypatch.delenv("MEDIAMTX_READ_PASS", raising=False)
    settings.EMAIL_HOST = "smtp.env.example.com"
    response = client.get(SYSTEM, headers=ADMIN)
    assert response.status_code == 200
    body = response.json()
    assert body["commit"] == "abc1234"
    assert body["media_root"] == "/srv/media"
    assert body["domain"] == settings.DOMAIN
    assert {host["role"] for host in body["hosts"]} >= {"tv", "admin", "app", "api", "media"}
    assert body["from_server_config"] == [{"key": "email.smtp_host", "env": ["EMAIL_HOST"]}]
    secrets = {secret["name"]: secret for secret in body["secrets"]}
    assert secrets["POSTGRES_PASSWORD"]["location"] == "environment"
    assert secrets["POSTGRES_PASSWORD"]["reason"] == "datastore"
    assert secrets["FIELD_ENCRYPTION_KEY"]["reason"] == "encryption"
    assert secrets["MEDIAMTX_READ_PASS"]["location"] == "missing"
    assert "pg-secret-value" not in response.content.decode()
    assert settings.FIELD_ENCRYPTION_KEY not in response.content.decode()
    assert body["key_rotations"] == []


def test_key_rotations_show_the_latest_of_each_kind(client: APIClient, staff_user: User) -> None:
    from apps.audit import services as audit  # noqa: PLC0415
    from apps.audit.services import AuditTarget  # noqa: PLC0415

    for kind in ("media_token", "media_token", "secret_key"):
        audit.record("keys.rotate", actor=staff_user, target=AuditTarget("keys", kind))
    rotations = client.get(SYSTEM, headers=ADMIN).json()["key_rotations"]
    assert [(item["kind"], item["actor"]) for item in rotations] == [
        ("media_token", "staff"),
        ("secret_key", "staff"),
    ]
