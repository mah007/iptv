"""The Integrations page's Test for live ingest (ADR-0023): MediaMTX's API with the read
user. It exercises the whole loop: MediaMTX asks /internal/mediamtx-auth, which checks
the saved settings. No secret appears in the answer."""

import httpx
from django.conf import settings

from apps.core.integration import TestCode, TestResult, http_client
from apps.core.services import get_secret, get_setting


def _not_ready(password: str) -> TestResult | None:
    if not password:
        return TestResult.failed(TestCode.NOT_CONFIGURED, "Set the read user's password first.")
    if not str(getattr(settings, "MEDIAMTX_AUTH_TOKEN", "")):
        return TestResult.failed(
            TestCode.NOT_CONFIGURED,
            "This server has no MEDIAMTX_AUTH_TOKEN: run scripts/install.sh --update.",
        )
    return None


def _answer(response: httpx.Response) -> TestResult:
    if response.status_code in (401, 403):
        return TestResult.failed(
            TestCode.AUTH_FAILED,
            "MediaMTX refused the read user: save the settings again, and check that "
            "MediaMTX signs in through this server (authMethod: http).",
        )
    if response.status_code != 200:
        return TestResult.failed(
            TestCode.UNEXPECTED, f"MediaMTX answered HTTP {response.status_code}."
        )
    try:
        items = response.json().get("items", [])
    except (ValueError, AttributeError):
        items = []
    count = len(items) if isinstance(items, list) else 0
    return TestResult.passed(
        f"MediaMTX accepted the read user; {count} streams are published.", streams=str(count)
    )


def check_ingest() -> TestResult:
    user = str(get_setting("live.ingest_read_user"))
    password = get_secret("live.ingest_read_password")
    if (problem := _not_ready(password)) is not None:
        return problem
    url = f"{str(settings.MEDIAMTX_API_URL).rstrip('/')}/v3/paths/list"
    try:
        with http_client() as client:
            response = client.get(url, params={"itemsPerPage": 100}, auth=(user, password))
    except httpx.TimeoutException:
        return TestResult.failed(TestCode.TIMEOUT, "MediaMTX did not answer in time.")
    except httpx.HTTPError:
        return TestResult.failed(
            TestCode.UNREACHABLE, "MediaMTX isn't reachable: is live ingest on (LIVE_INGEST=1)?"
        )
    return _answer(response)
