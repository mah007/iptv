"""`manage.py loadtest_data` (M15, docs/benchmarks/load.md): the load test's fixtures."""

import json
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.test import override_settings

from apps.accounts.models import ConcurrencyPolicy, User, XtreamCredential
from apps.accounts.services import authenticate_xtream

pytestmark = pytest.mark.django_db
PASSWORD = "load-" + "test-pass-0042"


def run(*args: str) -> str:
    out = StringIO()
    call_command("loadtest_data", *args, stdout=out)
    return out.getvalue()


def test_development_only() -> None:
    with pytest.raises(CommandError, match="development only"):
        run("--viewers", "1")


@override_settings(DEBUG=True)
def test_create_check_and_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOADTEST_PASSWORD", PASSWORD)
    assert "3 created" in run("--viewers", "2", "--groups", "1")
    assert "0 created, 3 in all" in run("--viewers", "2", "--groups", "1")
    assert XtreamCredential.objects.filter(username__startswith="ltc0001d").count() == 4
    group = User.objects.get(email="ltc0001@loadtest.invalid")
    assert group.access.max_streams == 2
    assert group.access.concurrency_policy == ConcurrencyPolicy.REJECT
    assert authenticate_xtream("ltv00002", PASSWORD) is not None
    assert authenticate_xtream("ltv00002", PASSWORD + "x") is None

    slots = json.loads(run("--slots"))
    assert slots == {
        "groups": 1, "limit": 2, "max": 0, "over": 0, "group_streams": 0, "viewer_streams": 0,
    }  # fmt: skip

    assert "deleted" in run("--delete")
    assert not User.objects.filter(email__endswith="@loadtest.invalid").exists()


@override_settings(DEBUG=True)
def test_needs_a_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOADTEST_PASSWORD", raising=False)
    with pytest.raises(CommandError, match="LOADTEST_PASSWORD"):
        run("--viewers", "1")
