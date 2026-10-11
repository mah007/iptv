"""TMDB and TheTVDB from the admin's settings (CFG, ADR-0019): the Integrations Test,
and the TMDB client following a credential change without a restart."""

import json

import httpx
import pytest

from apps.core.integration import TestCode, use_transport
from apps.core.services import set_setting
from apps.metadata import checks
from apps.metadata import services as metadata

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("no_server_config")]

TOKEN = "eyJhbGciOiJIUzI1NiJ9." + "a" * 40 + "." + "b" * 30
API_KEY = "0123456789abcdef0123456789abcdef"
TVDB_KEY = "f5744a13-9203-4d02-b951-fbd7352c1657"


def recording(response: httpx.Response) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return response

    return httpx.MockTransport(handler), seen


def test_tmdb_accepts_the_read_access_token() -> None:
    set_setting("metadata.tmdb_read_access_token", TOKEN, actor=None)
    transport, seen = recording(httpx.Response(200, json={"success": True}))
    with use_transport(transport):
        result = checks.check_tmdb()
    assert (result.ok, result.details) == (True, {"credential": "read access token"})
    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in repr(result)


def test_tmdb_accepts_an_api_key() -> None:
    set_setting("metadata.tmdb_api_key", API_KEY, actor=None)
    transport, seen = recording(httpx.Response(200, json={"success": True}))
    with use_transport(transport):
        assert checks.check_tmdb().ok is True
    assert seen[0].url.params["api_key"] == API_KEY


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(401, json={"status_code": 7}), TestCode.AUTH_FAILED),
        (httpx.Response(503), TestCode.REJECTED),
    ],
)
def test_tmdb_refusals_become_codes(response: httpx.Response, code: TestCode) -> None:
    set_setting("metadata.tmdb_read_access_token", TOKEN, actor=None)
    transport, _ = recording(response)
    with use_transport(transport):
        result = checks.check_tmdb()
    assert (result.ok, result.code) == (False, code)


def test_tmdb_unreachable_and_unset() -> None:
    assert checks.check_tmdb().code is TestCode.NOT_CONFIGURED
    set_setting("metadata.tmdb_api_key", API_KEY, actor=None)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Name or service not known", request=request)

    with use_transport(httpx.MockTransport(handler)):
        result = checks.check_tmdb()
    assert result.code is TestCode.UNREACHABLE
    assert API_KEY not in result.message


def test_tvdb_logs_in_with_the_key_and_pin() -> None:
    assert checks.check_tvdb().code is TestCode.NOT_CONFIGURED
    set_setting("metadata.tvdb_api_key", TVDB_KEY, actor=None)
    set_setting("metadata.tvdb_pin", "1234ABCD", actor=None)
    transport, seen = recording(httpx.Response(200, json={"status": "success"}))
    with use_transport(transport):
        assert checks.check_tvdb().ok is True
    assert json.loads(seen[0].content) == {"apikey": TVDB_KEY, "pin": "1234ABCD"}
    transport, _ = recording(httpx.Response(401, json={"status": "failure"}))
    with use_transport(transport):
        assert checks.check_tvdb().code is TestCode.AUTH_FAILED


def test_the_tmdb_client_follows_the_settings_without_a_restart() -> None:
    metadata.reset_caches()
    try:
        offline = metadata.tmdb()
        assert offline.fixture_mode is True
        assert metadata.tmdb() is offline  # unchanged settings keep the client
        set_setting("metadata.tmdb_read_access_token", TOKEN, actor=None)
        live = metadata.tmdb()
        assert live.fixture_mode is False
        set_setting("metadata.tmdb_language", "ar-SA", actor=None)
        assert metadata.tmdb() is not live
    finally:
        metadata.reset_caches()
