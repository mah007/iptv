import pytest
from django.core.exceptions import ImproperlyConfigured

from config.env import env, env_bool, env_int, env_list


def test_env_returns_value_or_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IPTV_TEST_VAR", "value")
    assert env("IPTV_TEST_VAR") == "value"
    assert env("IPTV_TEST_MISSING", "fallback") == "fallback"


@pytest.mark.parametrize("value", [None, ""])
def test_env_without_default_fails_loudly(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    if value is None:
        monkeypatch.delenv("IPTV_TEST_VAR", raising=False)
    else:
        monkeypatch.setenv("IPTV_TEST_VAR", value)
    with pytest.raises(ImproperlyConfigured, match="IPTV_TEST_VAR"):
        env("IPTV_TEST_VAR")


@pytest.mark.parametrize(
    ("raw", "expected"), [("1", True), ("TRUE", True), ("on", True), ("0", False), ("no", False)]
)
def test_env_bool_parses_common_spellings(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool
) -> None:
    monkeypatch.setenv("IPTV_TEST_BOOL", raw)
    assert env_bool("IPTV_TEST_BOOL", default=not expected) is expected


def test_env_bool_rejects_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IPTV_TEST_BOOL", "maybe")
    with pytest.raises(ImproperlyConfigured):
        env_bool("IPTV_TEST_BOOL", default=False)


def test_env_bool_and_int_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IPTV_TEST_BOOL", raising=False)
    monkeypatch.delenv("IPTV_TEST_INT", raising=False)
    assert env_bool("IPTV_TEST_BOOL", default=True) is True
    assert env_int("IPTV_TEST_INT", default=7) == 7


def test_env_int_parses_and_rejects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IPTV_TEST_INT", "42")
    assert env_int("IPTV_TEST_INT", default=0) == 42
    monkeypatch.setenv("IPTV_TEST_INT", "forty-two")
    with pytest.raises(ImproperlyConfigured):
        env_int("IPTV_TEST_INT", default=0)


def test_env_list_splits_and_trims(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IPTV_TEST_LIST", " web, localhost ,,127.0.0.1 ")
    assert env_list("IPTV_TEST_LIST", []) == ["web", "localhost", "127.0.0.1"]
    monkeypatch.setenv("IPTV_TEST_LIST", "  ")
    assert env_list("IPTV_TEST_LIST", ["default"]) == ["default"]
