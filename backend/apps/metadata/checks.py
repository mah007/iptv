"""The admin's "Test" for the metadata providers (CFG, ADR-0019): is the saved
credential accepted? One small request each, without the rate limiter or the cache,
so the answer is the provider's own, now."""

from typing import Final

import httpx

from apps.core.integration import (
    TestCode,
    TestResult,
    from_http_error,
    from_status,
    http_client,
)
from apps.core.services import get_secret

TMDB_AUTH_URL: Final = "https://api.themoviedb.org/3/authentication"
TVDB_LOGIN_URL: Final = "https://api4.thetvdb.com/v4/login"


def check_tmdb() -> TestResult:
    token = get_secret("metadata.tmdb_read_access_token")
    api_key = get_secret("metadata.tmdb_api_key")
    if not token and not api_key:
        return TestResult.failed(
            TestCode.NOT_CONFIGURED,
            "No TMDB credential is set: metadata comes from the offline sample fixtures.",
        )
    headers = {"Accept": "application/json"}
    params: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    else:
        params["api_key"] = api_key
    try:
        with http_client() as client:
            response = client.get(TMDB_AUTH_URL, headers=headers, params=params)
    except httpx.HTTPError as exc:
        return from_http_error("TMDB", exc)
    if response.status_code != httpx.codes.OK:
        return from_status("TMDB", response)
    credential = "read access token" if token else "API key"
    return TestResult.passed(f"TMDB accepted the {credential}.", credential=credential)


def check_tvdb() -> TestResult:
    api_key = get_secret("metadata.tvdb_api_key")
    if not api_key:
        return TestResult.failed(TestCode.NOT_CONFIGURED, "No TheTVDB API key is set.")
    body = {"apikey": api_key}
    pin = get_secret("metadata.tvdb_pin")
    if pin:
        body["pin"] = pin
    try:
        with http_client() as client:
            response = client.post(TVDB_LOGIN_URL, json=body)
    except httpx.HTTPError as exc:
        return from_http_error("TheTVDB", exc)
    if response.status_code != httpx.codes.OK:
        return from_status("TheTVDB", response)
    return TestResult.passed("TheTVDB accepted the key.")
