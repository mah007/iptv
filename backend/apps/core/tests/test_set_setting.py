"""`manage.py set_setting`: the installer's way into the settings registry (ADR-0018)."""

import io
from typing import Any
from unittest import mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.audit.models import AuditLog
from apps.core.services import get_setting, set_setting, setting_state

pytestmark = pytest.mark.django_db

BOT = "123456789:" + "B" * 35


def run(*args: str, stdin: str | None = None) -> str:
    out = io.StringIO()
    if stdin is None:
        call_command("set_setting", *args, stdout=out)
    else:
        with mock.patch("sys.stdin", io.StringIO(stdin)):
            call_command("set_setting", *args, stdout=out)
    return out.getvalue()


def test_a_value_from_stdin_is_validated_stored_and_audited() -> None:
    output = run("monitoring.alert_emails", "--stdin", stdin="ops@example.com\n")
    assert get_setting("monitoring.alert_emails") == "ops@example.com"
    assert "set to 'ops@example.com'" in output
    entry = AuditLog.objects.get(action="setting.update")
    assert entry.actor is None
    assert entry.after == {"value": "ops@example.com"}


def test_a_sensitive_value_from_the_environment_is_never_printed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BOT_TOKEN", BOT)
    output = run("monitoring.telegram_bot_token", "--env", "BOT_TOKEN")
    assert get_setting("monitoring.telegram_bot_token") == BOT
    assert BOT not in output
    assert "(hidden)" in output
    assert BOT not in str(AuditLog.objects.get(action="setting.update").after)


@pytest.mark.parametrize(
    ("key", "text", "expected"),
    [
        ("monitoring.alerts_enabled", "no", False),
        ("monitoring.alerts_enabled", "TRUE", True),
        ("monitoring.edge_capacity_mbps", " 2500 ", 2500),
        ("monitoring.alert_email_severities", "critical", "critical"),
    ],
)
def test_values_take_the_setting_s_type(key: str, text: str, expected: Any) -> None:
    run(key, "--stdin", stdin=text)
    assert get_setting(key) == expected


@pytest.mark.parametrize(
    ("key", "text"),
    [
        ("monitoring.alert_emails", "not-an-address"),
        ("monitoring.edge_capacity_mbps", "fast"),
        ("monitoring.alerts_enabled", "maybe"),
        ("monitoring.alert_email_severities", "some"),
        ("monitoring.telegram_chat_id", "@channel"),
    ],
)
def test_invalid_values_are_refused(key: str, text: str) -> None:
    with pytest.raises(CommandError, match=key):
        run(key, "--stdin", stdin=text)


def test_unknown_keys_are_refused() -> None:
    with pytest.raises(CommandError, match="Unknown setting"):
        run("nope.nothing", "--stdin", stdin="1")


def test_if_unset_leaves_an_admin_s_change_alone() -> None:
    run("monitoring.alert_emails", "--stdin", "--if-unset", stdin="first@example.com")
    assert get_setting("monitoring.alert_emails") == "first@example.com"
    set_setting("monitoring.alert_emails", "admin-choice@example.com", actor=None)
    output = run("monitoring.alert_emails", "--stdin", "--if-unset", stdin="other@example.com")
    assert "left as it is" in output
    assert setting_state("monitoring.alert_emails").value == "admin-choice@example.com"


def test_the_value_is_never_an_argument() -> None:
    with pytest.raises(CommandError):
        call_command("set_setting", "monitoring.alert_emails", "ops@example.com")
