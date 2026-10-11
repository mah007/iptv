"""Monitoring alerts delivered by Django (ADR-0018): Alertmanager's webhook, the settings
that choose recipients and severities, email and Telegram, and failures."""

import json
from collections.abc import Iterator
from typing import Any
from unittest import mock

import httpx
import pytest
from django.core import mail
from django.test import Client
from prometheus_client import REGISTRY

from apps.core.integration import use_transport
from apps.core.services import set_setting
from apps.notifications import alerts
from apps.notifications.mail import SETTINGS_BACKEND

pytestmark = pytest.mark.django_db

INTERNAL = {"host": "web"}
TOKEN = "test-webhook-token-0123456789"  # noqa: S105 (a fake shared secret)
BOT = "123456789:" + "A" * 35


def alert(name: str, severity: str, status: str = "firing", **labels: str) -> dict[str, Any]:
    return {
        "status": status,
        "labels": {"alertname": name, "severity": severity, **labels},
        "annotations": {
            "summary": f"{name} summary",
            "description": f"{name} description <b>",
            "runbook": f"docs/runbooks/alerts.md#{name.lower()}",
        },
        "startsAt": "2026-10-11T10:00:00Z",
        "endsAt": "0001-01-01T00:00:00Z",
        "generatorURL": "http://grafana.localhost/prometheus/graph?g0.expr=x",
    }


def payload(*items: dict[str, Any], status: str = "firing") -> dict[str, Any]:
    first = items[0]["labels"]
    return {
        "version": "4",
        "status": status,
        "receiver": "django",
        "commonLabels": {"alertname": first["alertname"], "severity": first["severity"]},
        "externalURL": "http://grafana.localhost/alertmanager",
        "alerts": list(items),
    }


def deliveries(channel: str, result: str) -> float:
    labels = {"channel": channel, "result": result}
    return REGISTRY.get_sample_value("iptv_alert_deliveries_total", labels) or 0.0


@pytest.fixture(autouse=True)
def _email(settings: Any) -> None:
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.DEFAULT_FROM_EMAIL = "Smart IPTV <alerts@example.com>"
    settings.ALERT_WEBHOOK_TOKEN = TOKEN


@pytest.fixture
def recipients() -> None:
    set_setting("monitoring.alert_emails", "ops@example.com, oncall@example.com", actor=None)


class FakeTelegram:
    """The Bot API behind a mock transport: records requests, answers `response`."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.response = httpx.Response(200, json={"ok": True})
        self.error: Exception | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.response


@pytest.fixture
def telegram() -> Iterator[FakeTelegram]:
    set_setting("notifications.telegram_bot_token", BOT, actor=None)
    set_setting("notifications.telegram_chat_id", "-1001234567890", actor=None)
    fake = FakeTelegram()
    with use_transport(httpx.MockTransport(fake)):
        yield fake


def post(body: object, token: str = TOKEN) -> Any:
    return Client().post(
        "/internal/alerts",
        data=json.dumps(body),
        content_type="application/json",
        headers={**INTERNAL, "authorization": f"Bearer {token}"},
    )


# --- The webhook ----------------------------------------------------------------------------


def test_an_alert_goes_to_email_and_telegram(recipients: None, telegram: FakeTelegram) -> None:
    response = post(payload(alert("FireDrill", "critical", drill="true")))
    assert response.status_code == 200
    assert response.json() == {"email": "sent", "telegram": "sent"}
    (message,) = mail.outbox
    assert message.subject == "[FIRING:1] FireDrill (critical) - Smart IPTV"
    assert message.to == ["ops@example.com", "oncall@example.com"]
    assert "FireDrill summary" in message.body
    assert "Labels: drill=true" in message.body
    assert (
        "Runbook: https://github.com/mah007/iptv/blob/main/docs/runbooks/alerts.md#firedrill"
        in message.body
    )
    assert "Silence or inspect: http://grafana.localhost/alertmanager" in message.body
    (request,) = telegram.requests
    assert str(request.url) == f"https://api.telegram.org/bot{BOT}/sendMessage"
    sent = json.loads(request.content)
    assert sent["chat_id"] == "-1001234567890"
    assert sent["parse_mode"] == "HTML"
    assert "<b>FIRING: FireDrill</b>" in sent["text"]
    assert "description &lt;b&gt;" in sent["text"]  # escaped for Telegram's HTML
    runbook = "https://github.com/mah007/iptv/blob/main/docs/runbooks/alerts.md#firedrill"
    assert f'<a href="{runbook}">Runbook</a>' in sent["text"]


def test_a_resolved_alert_says_so(recipients: None) -> None:
    resolved = alert("DiskSpaceLow", "warning", status="resolved", mountpoint="/")
    resolved["endsAt"] = "2026-10-11T10:30:00Z"
    post(payload(resolved, status="resolved"))
    (message,) = mail.outbox
    assert message.subject == "[RESOLVED] DiskSpaceLow (warning) - Smart IPTV"
    assert "Resolved: 2026-10-11 10:30 UTC" in message.body


@pytest.mark.parametrize(
    "case",
    [
        ("all", "all", "warning", {"email": "sent", "telegram": "sent"}),
        ("all", "critical", "warning", {"email": "sent", "telegram": "skipped"}),
        ("critical", "all", "warning", {"email": "skipped", "telegram": "sent"}),
        ("none", "critical", "critical", {"email": "skipped", "telegram": "sent"}),
        ("all", "all", "none", {"email": "skipped", "telegram": "skipped"}),
    ],
)
def test_severities_choose_the_channels(
    recipients: None,
    telegram: FakeTelegram,
    case: tuple[str, str, str, dict[str, str]],
) -> None:
    email_policy, telegram_policy, severity, expected = case
    set_setting("monitoring.alert_email_severities", email_policy, actor=None)
    set_setting("monitoring.telegram_severities", telegram_policy, actor=None)
    response = post(payload(alert("ReviewQueueLarge", severity)))
    assert response.json() == expected


def test_switched_off_nothing_is_sent(recipients: None, telegram: FakeTelegram) -> None:
    set_setting("monitoring.alerts_enabled", False, actor=None)
    assert post(payload(alert("WebNotReady", "critical"))).json() == {
        "email": "skipped",
        "telegram": "skipped",
    }
    assert mail.outbox == []
    assert telegram.requests == []


def test_without_recipients_or_a_bot_nothing_is_attempted() -> None:
    response = post(payload(alert("WebNotReady", "critical")))
    assert response.status_code == 200
    assert response.json() == {"email": "skipped", "telegram": "skipped"}


def test_no_smtp_server_means_no_email(recipients: None, settings: Any) -> None:
    settings.EMAIL_BACKEND = SETTINGS_BACKEND
    settings.EMAIL_HOST = ""
    assert post(payload(alert("WebNotReady", "critical"))).json()["email"] == "skipped"


def test_one_failing_channel_is_reported_and_counted(
    recipients: None, telegram: FakeTelegram
) -> None:
    telegram.response = httpx.Response(400, json={"ok": False, "description": "chat not found"})
    failed = deliveries("telegram", "failed")
    response = post(payload(alert("WebNotReady", "critical")))
    assert response.status_code == 200  # email went out; Alertmanager must not resend it
    assert response.json() == {"email": "sent", "telegram": "failed"}
    assert deliveries("telegram", "failed") == failed + 1


def test_every_channel_failing_asks_alertmanager_to_retry(
    recipients: None, telegram: FakeTelegram, caplog: pytest.LogCaptureFixture
) -> None:
    telegram.error = httpx.ConnectError("no route")
    with mock.patch("django.core.mail.EmailMessage.send", side_effect=OSError("SMTP down")):
        response = post(payload(alert("WebNotReady", "critical")))
    assert response.status_code == 503
    assert response.json() == {"email": "failed", "telegram": "failed"}
    assert BOT not in caplog.text


@pytest.mark.parametrize(("token", "status"), [("wrong-token", 401), ("", 401), (TOKEN + "x", 401)])
def test_the_shared_secret_is_required(token: str, status: int) -> None:
    assert post(payload(alert("WebNotReady", "critical")), token=token).status_code == status


def test_without_a_configured_secret_the_webhook_does_not_exist(settings: Any) -> None:
    settings.ALERT_WEBHOOK_TOKEN = ""
    assert post(payload(alert("WebNotReady", "critical"))).status_code == 404


@pytest.mark.parametrize("body", ["not json", "[1, 2]"])
def test_bodies_must_be_json_objects(body: str) -> None:
    response = Client().post(
        "/internal/alerts",
        data=body,
        content_type="application/json",
        headers={**INTERNAL, "authorization": f"Bearer {TOKEN}"},
    )
    assert response.status_code == 400


def test_only_post() -> None:
    response = Client().get("/internal/alerts", headers=INTERNAL)
    assert response.status_code == 405


@pytest.mark.parametrize("host", ["api.localhost", "tv.localhost", "admin.localhost"])
def test_the_webhook_is_internal_only(host: str, settings: Any) -> None:
    response = Client().post(
        "/internal/alerts",
        data="{}",
        content_type="application/json",
        headers={"host": getattr(settings, f"{host.split('.', maxsplit=1)[0].upper()}_HOST", host)},
    )
    assert response.status_code == 404


# --- Messages ------------------------------------------------------------------------------


def test_long_telegram_messages_are_cut() -> None:
    notice = alerts.parse(payload(*[alert("DiskSpaceLow", "warning", n=str(i)) for i in range(60)]))
    assert len(notice.alerts) == alerts.MAX_ALERTS
    text = alerts.telegram_text(notice, notice.alerts * 4)
    assert len(text) <= alerts.TELEGRAM_LIMIT


def test_odd_payloads_are_tolerated() -> None:
    notice = alerts.parse({"alerts": [None, "x", {"labels": None}], "commonLabels": None})
    assert notice.status == "firing"
    assert len(notice.alerts) == 1
    assert notice.alerts[0].name == ""


# --- Alertmanager's fallback channels (CFG, ADR-0019) ----------------------------------------


def fallback(token: str = TOKEN) -> Any:
    return Client().get(
        "/internal/alert-fallback", headers={**INTERNAL, "authorization": f"Bearer {token}"}
    )


def decoded(response: Any) -> dict[str, str]:
    import base64  # noqa: PLC0415

    lines = response.content.decode().splitlines()
    return {
        name: base64.b64decode(value).decode()
        for name, value in (line.split("\t") for line in lines)
    }


@pytest.mark.usefixtures("no_server_config")
def test_the_fallback_follows_the_admin_s_settings(recipients: None) -> None:
    assert decoded(fallback())["email_to"] == ""  # no SMTP server yet
    set_setting("email.smtp_host", "smtp.example.com", actor=None)
    set_setting("email.smtp_port", 465, actor=None)
    set_setting("email.smtp_security", "ssl", actor=None)
    set_setting("email.smtp_username", "alerts", actor=None)
    set_setting("email.smtp_password", "smtp-Fallback-1", actor=None)
    set_setting("notifications.telegram_bot_token", BOT, actor=None)
    set_setting("notifications.telegram_chat_id", "-100123", actor=None)
    response = fallback()
    assert response.status_code == 200
    assert response["Cache-Control"].startswith("max-age=0")
    values = decoded(response)
    assert values["email_to"] == "ops@example.com, oncall@example.com"
    assert values["smarthost"] == "smtp.example.com:465"
    assert values["require_tls"] == "false"
    assert (values["username"], values["smtp_password"]) == ("alerts", "smtp-Fallback-1")
    assert (values["telegram_bot_token"], values["telegram_chat_id"]) == (BOT, "-100123")
    set_setting("monitoring.telegram_severities", "none", actor=None)
    assert decoded(fallback())["telegram_bot_token"] == ""
    set_setting("monitoring.alerts_enabled", False, actor=None)
    assert set(decoded(fallback()).values()) == {"", "true"}


def test_the_fallback_needs_the_shared_secret(settings: Any) -> None:
    assert fallback("wrong").status_code == 401
    settings.ALERT_WEBHOOK_TOKEN = ""
    assert fallback().status_code == 404
