"""The Telegram bot of Settings → Integrations (CFG, ADR-0019): sending and the Test."""

import json

import httpx
import pytest

from apps.core.integration import TestCode, use_transport
from apps.core.services import set_setting
from apps.notifications import telegram

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("no_server_config")]

BOT = "123456789:" + "B" * 35
CHAT = "-1001234567890"


def bot_api(answers: dict[str, httpx.Response]) -> tuple[httpx.MockTransport, list[str]]:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        seen.append(method)
        if method not in answers:
            raise AssertionError(method)
        return answers[method]

    return httpx.MockTransport(handler), seen


def configure(chat: str = CHAT) -> None:
    set_setting("notifications.telegram_bot_token", BOT, actor=None)
    if chat:
        set_setting("notifications.telegram_chat_id", chat, actor=None)


def test_the_test_checks_the_bot_then_messages_the_admins(caplog: pytest.LogCaptureFixture) -> None:
    configure()
    sent: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getMe"):
            return httpx.Response(200, json={"ok": True, "result": {"username": "iptv_bot"}})
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {}})

    with use_transport(httpx.MockTransport(handler)):
        result = telegram.send_test_message()
    assert (result.ok, result.details) == (True, {"bot": "iptv_bot"})
    assert sent[0]["chat_id"] == CHAT
    assert BOT not in repr(result) + caplog.text


@pytest.mark.parametrize(
    ("answers", "code"),
    [
        ({"getMe": httpx.Response(401, json={"ok": False, "description": "Unauthorized"})},
         TestCode.AUTH_FAILED),
        (
            {
                "getMe": httpx.Response(200, json={"ok": True, "result": {"username": "b"}}),
                "sendMessage": httpx.Response(
                    400, json={"ok": False, "description": "Bad Request: chat not found"}
                ),
            },
            TestCode.CHAT_NOT_FOUND,
        ),
        (
            {
                "getMe": httpx.Response(200, json={"ok": True, "result": {"username": "b"}}),
                "sendMessage": httpx.Response(
                    403, json={"ok": False, "description": "Forbidden: bot was kicked"}
                ),
            },
            TestCode.FORBIDDEN,
        ),
    ],
)  # fmt: skip
def test_telegram_refusals_become_codes(answers: dict[str, httpx.Response], code: TestCode) -> None:
    configure()
    transport, _ = bot_api(answers)
    with use_transport(transport):
        result = telegram.send_test_message()
    assert (result.ok, result.code) == (False, code)
    assert BOT not in result.message


def test_an_unreachable_telegram_is_reported() -> None:
    configure()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    with use_transport(httpx.MockTransport(handler)):
        result = telegram.send_test_message()
    assert result.code is TestCode.TIMEOUT
    assert BOT not in result.message


def test_the_test_needs_a_bot_and_a_chat() -> None:
    assert telegram.send_test_message().code is TestCode.NOT_CONFIGURED
    configure(chat="")
    transport, seen = bot_api(
        {"getMe": httpx.Response(200, json={"ok": True, "result": {"username": "b"}})}
    )
    with use_transport(transport):
        result = telegram.send_test_message()
    assert (result.code, result.details) == (TestCode.NOT_CONFIGURED, {"bot": "b"})
    assert seen == ["getMe"]
    with pytest.raises(telegram.TelegramError):
        telegram.send_message("hello")


def test_the_settings_fall_back_to_the_alert_variables(settings: object) -> None:
    settings.ALERT_TELEGRAM_BOT_TOKEN = BOT  # type: ignore[attr-defined]
    settings.ALERT_TELEGRAM_CHAT_ID = CHAT  # type: ignore[attr-defined]
    assert telegram.configured() is True
