"""Telegram Bot API: the admins' alerts and messages (SPEC §7.7; CFG, ADR-0019).

The bot token and the admins' chat are settings (`notifications.telegram_bot_token`,
a secret, and `notifications.telegram_chat_id`), read at every send. The token is
part of every Bot API URL, so URLs are never logged (`redact_text` masks
`/bot<token>` anyway) and errors carry only Telegram's own description.
"""

from typing import Any, Final

import httpx

from apps.core.integration import TestCode, TestResult, from_http_error, http_client
from apps.core.services import get_secret, get_setting

TELEGRAM_API: Final = "https://api.telegram.org"
TEXT_LIMIT: Final = 4000  # characters; Telegram's own limit is 4096


class TelegramError(RuntimeError):
    """Telegram refused a call or could not be reached; `code` says why."""

    def __init__(self, code: TestCode, message: str) -> None:
        super().__init__(message)
        self.code = code


def bot_token() -> str:
    return get_secret("notifications.telegram_bot_token")


def admin_chat_id() -> str:
    return str(get_setting("notifications.telegram_chat_id"))


def configured() -> bool:
    return bool(bot_token() and admin_chat_id())


def _call(token: str, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        with http_client() as client:
            response = client.post(f"{TELEGRAM_API}/bot{token}/{method}", json=payload or {})
    except httpx.HTTPError as exc:
        result = from_http_error("Telegram", exc)
        raise TelegramError(result.code, result.message) from None
    try:
        body: dict[str, Any] = response.json()
    except ValueError:
        body = {}
    if response.status_code != httpx.codes.OK or not body.get("ok"):
        # Telegram's description names the problem ("chat not found"); never the token.
        reason = str(body.get("description", ""))[:200]
        if response.status_code == httpx.codes.UNAUTHORIZED:
            code = TestCode.AUTH_FAILED
        elif "chat not found" in reason.lower():
            code = TestCode.CHAT_NOT_FOUND
        elif response.status_code == httpx.codes.FORBIDDEN:
            code = TestCode.FORBIDDEN
        else:
            code = TestCode.REJECTED
        msg = f"Telegram answered {response.status_code}: {reason}"
        raise TelegramError(code, msg)
    return body


def send_message(text: str, *, chat_id: str | None = None, token: str | None = None) -> None:
    """Send an HTML message to `chat_id` (default: the admins' chat). Raises TelegramError."""
    token = token if token is not None else bot_token()
    chat = chat_id if chat_id is not None else admin_chat_id()
    if not token or not chat:
        raise TelegramError(TestCode.NOT_CONFIGURED, "No Telegram bot or chat is set.")
    _call(
        token,
        "sendMessage",
        {
            "chat_id": chat,
            "text": text[:TEXT_LIMIT],
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
    )


def send_test_message() -> TestResult:
    """Check the bot (getMe), then send a test message to the admins' chat."""
    token, chat = bot_token(), admin_chat_id()
    if not token:
        return TestResult.failed(TestCode.NOT_CONFIGURED, "No Telegram bot token is set.")
    try:
        me = _call(token, "getMe").get("result") or {}
        username = str(me.get("username", ""))[:64]
        if not chat:
            return TestResult.failed(
                TestCode.NOT_CONFIGURED,
                "The bot works, but no chat is set to send to.",
                bot=username,
            )
        service = str(get_setting("branding.service_name_en"))
        send_message(
            f"✅ Test message from the {service} admin (Settings, Integrations, Telegram).",
            chat_id=chat,
            token=token,
        )
    except TelegramError as exc:
        return TestResult.failed(exc.code, str(exc))
    return TestResult.passed(f"Sent by @{username}.", bot=username)
