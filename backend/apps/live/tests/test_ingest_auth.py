"""MediaMTX's sign-ins through /internal/mediamtx-auth (ADR-0023), and the Integrations
page's Test for live ingest."""

import base64
import json
from typing import Any

import httpx
import pytest
from django.conf import settings as django_settings
from django.test import Client
from pytest_django import Settings

from apps.core.integration import TestCode, use_transport
from apps.core.metrics import LIVE_INGEST_AUTH
from apps.core.services import reset_setting, set_setting
from apps.live import integrations
from apps.live.checks import check_ingest
from apps.live.models import IntegrationKind, LiveIntegration

pytestmark = pytest.mark.django_db
URL = "/internal/mediamtx-auth"
TOKEN = "ab12" * 16  # a test token, built from parts
PUBLISH = "pub-" + "secret-1234"  # test passwords, built from parts
READ = "read-" + "secret-5678"
DOCKER = "172.18.0.7"


@pytest.fixture(autouse=True)
def configured(settings: Settings) -> None:
    settings.MEDIAMTX_AUTH_TOKEN = TOKEN
    set_setting("live.ingest_publish_password", PUBLISH, actor=None)
    set_setting("live.ingest_read_password", READ, actor=None)


def ask(
    action: str,
    user: str = "",
    password: str = "",
    *,
    ip: str = "203.0.113.9",
    token: str = TOKEN,
) -> int:
    body = {
        "user": user,
        "password": password,
        "token": "",
        "ip": ip,
        "action": action,
        "path": "studio",
        "protocol": "rtmp",
        "id": None,
        "query": "",
    }
    basic = base64.b64encode(f"mediamtx:{token}".encode()).decode()
    response = Client(headers={"host": "web"}).post(
        URL, json.dumps(body), content_type="application/json", HTTP_AUTHORIZATION=f"Basic {basic}"
    )
    return response.status_code


def counted(action: str, result: str) -> float:
    return float(LIVE_INGEST_AUTH.labels(action=action, result=result)._value.get())


def test_publishers_and_readers_sign_in_with_their_own_users() -> None:
    allowed = counted("publish", "allowed")
    assert ask("publish", "publisher", PUBLISH) == 200
    assert counted("publish", "allowed") == allowed + 1
    assert ask("read", "reader", READ, ip=DOCKER) == 200
    assert ask("api", "reader", READ, ip=DOCKER) == 200
    assert ask("read", "reader", READ) == 403  # reading from outside the Docker networks
    # Each user only for its own actions.
    assert ask("read", "publisher", PUBLISH, ip=DOCKER) == 403
    assert ask("publish", "reader", READ) == 403
    assert ask("publish", "publisher", "not-it") == 403
    assert ask("metrics", "reader", READ, ip=DOCKER) == 403


def test_no_credentials_ask_for_them_and_loopback_may_publish() -> None:
    assert ask("read", ip=DOCKER) == 401  # RTSP clients send credentials once asked
    assert ask("publish", ip="127.0.0.1") == 200  # MediaMTX's own runOnDemand hooks
    assert ask("read", ip="127.0.0.1") == 401
    assert ask("publish") == 401


def test_a_mediamtx_integrations_user_may_read() -> None:
    LiveIntegration.objects.create(
        kind=IntegrationKind.MEDIAMTX,
        name="Studio",
        base_url="http://mediamtx:9997",
        stream_base_url="rtsp://mediamtx:8554",
        credentials_encrypted=integrations.encrypt_credentials(
            {"username": "sync", "password": "sync-" + "pass-0001"}
        ),
        rights_holder="Smart IPTV",
    )
    assert ask("api", "sync", "sync-pass-0001", ip=DOCKER) == 200
    assert ask("publish", "sync", "sync-pass-0001") == 403


def test_repeated_failures_lock_the_address_out() -> None:
    set_setting("live.ingest_auth_max_failures", 3, actor=None)
    for _ in range(3):
        assert ask("publish", "publisher", "wrong-password") == 403
    throttled = counted("publish", "throttled")
    assert ask("publish", "publisher", PUBLISH) == 403
    assert counted("publish", "throttled") == throttled + 1
    assert ask("publish", "publisher", PUBLISH, ip="198.51.100.20") == 200


def test_the_env_users_of_before_still_work(settings: Settings) -> None:
    reset_setting("live.ingest_publish_password", actor=None)
    settings.MEDIAMTX_PUBLISH_USER = "encoder"
    settings.MEDIAMTX_PUBLISH_PASS = "env-" + "password-42"
    assert ask("publish", "encoder", "env-password-42") == 200


def test_only_mediamtx_may_ask(settings: Settings) -> None:
    assert ask("publish", "publisher", PUBLISH, token="cd34" * 16) == 401
    other = Client(headers={"host": django_settings.APP_HOST})
    assert other.post(URL, "{}", content_type="application/json").status_code == 404
    settings.MEDIAMTX_AUTH_TOKEN = ""
    assert ask("publish", "publisher", PUBLISH) == 404


# --- The Integrations page's Test ------------------------------------------------------------


def _mediamtx(status: int, body: Any = None) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v3/paths/list"
        expected = base64.b64encode(f"reader:{READ}".encode()).decode()
        assert request.headers["authorization"] == f"Basic {expected}"
        return httpx.Response(status, json=body if body is not None else {})

    return httpx.MockTransport(handle)


def test_the_test_reads_mediamtxs_api_with_the_read_user() -> None:
    with use_transport(_mediamtx(200, {"items": [{"name": "studio"}, {"name": "cam"}]})):
        result = check_ingest()
    assert result.ok
    assert result.details == {"streams": "2"}
    with use_transport(_mediamtx(401)):
        assert check_ingest().code is TestCode.AUTH_FAILED

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with use_transport(httpx.MockTransport(down)):
        assert check_ingest().code is TestCode.UNREACHABLE


def test_the_test_needs_the_settings_and_the_token(settings: Settings) -> None:
    settings.MEDIAMTX_AUTH_TOKEN = ""
    assert check_ingest().code is TestCode.NOT_CONFIGURED
    settings.MEDIAMTX_AUTH_TOKEN = TOKEN
    set_setting("live.ingest_read_password", "", actor=None)
    assert check_ingest().code is TestCode.NOT_CONFIGURED
