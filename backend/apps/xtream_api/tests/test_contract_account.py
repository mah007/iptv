"""manage.py xtream_contract_account: idempotent, credentials from the environment."""

import io

import pytest
from django.core.management import CommandError, call_command

from apps.accounts import services as account_services
from apps.accounts.models import CustomerAccess

pytestmark = pytest.mark.django_db


def _run(monkeypatch: pytest.MonkeyPatch, username: str, password: str) -> str:
    monkeypatch.setenv("XC_USER", username)
    monkeypatch.setenv("XC_PASS", password)
    out = io.StringIO()
    call_command("xtream_contract_account", stdout=out)
    return out.getvalue()


def test_creates_then_updates_the_account(monkeypatch: pytest.MonkeyPatch) -> None:
    assert "Created" in _run(monkeypatch, "compat-check", "first-password-1")
    login = account_services.authenticate_xtream("compat-check", "first-password-1")
    assert login is not None
    assert CustomerAccess.objects.get(user=login.user).max_streams == 3

    output = _run(monkeypatch, "compat-check", "second-password-2")
    assert "Updated" in output
    assert "second-password-2" not in output
    assert account_services.authenticate_xtream("compat-check", "first-password-1") is None
    assert account_services.authenticate_xtream("compat-check", "second-password-2") is not None


def test_requires_both_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XC_USER", raising=False)
    monkeypatch.setenv("XC_PASS", "whatever-1234")
    with pytest.raises(CommandError):
        call_command("xtream_contract_account")
