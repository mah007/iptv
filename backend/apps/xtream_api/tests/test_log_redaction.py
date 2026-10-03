"""No log line carries Xtream credentials (SPEC §7.5, §11).

Apps send the username and password on every request: in play URL paths and in
the query or form body of player_api.php, get.php and xmltv.php. Every request is
logged once by RequestLogMiddleware (path redacted, query never logged), 4xx/5xx
again by Django's request logger, and tracebacks with the exception; all of it
runs through the redaction processor. The servers' own access logs are off.
"""

import importlib.util
import io
import logging
from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client

from apps.xtream_api.playback import PlayRefused
from apps.xtream_api.tests.conftest import Subscriber
from apps.xtream_api.tests.fakes import FakeStarter, InMemoryCatalogSource

pytestmark = pytest.mark.django_db


def assert_clean(output: io.StringIO, *secrets: str) -> str:
    text = output.getvalue()
    assert text, "the requests were logged"
    for secret in secrets:
        assert secret not in text
    return text


def test_player_api_get_and_post(
    tv: Client, subscriber: Subscriber, catalog: InMemoryCatalogSource, log_output: io.StringIO
) -> None:
    tv.get("/player_api.php", subscriber.params(action="get_vod_streams"))
    tv.post("/player_api.php", subscriber.params(action="get_series"))
    tv.get("/player_api.php", {"username": subscriber.username, "password": "wrong-password-1"})
    tv.get("/player_api.php", subscriber.params(action="get_vod_info", vod_id="424242"))  # 404
    tv.put("/player_api.php", subscriber.params())  # 405, logged by Django too
    text = assert_clean(log_output, subscriber.username, subscriber.password, "wrong-password-1")
    assert "/player_api.php" in text


def test_playlist_and_guide(
    tv: Client, subscriber: Subscriber, catalog: InMemoryCatalogSource, log_output: io.StringIO
) -> None:
    tv.get("/get.php", subscriber.params(type="m3u_plus", output="ts"))
    tv.get("/xmltv.php", subscriber.params())
    tv.get("/get.php", {"username": subscriber.username, "password": "wrong-password-1"})  # 403
    assert_clean(log_output, subscriber.username, subscriber.password, "wrong-password-1")


def test_play_urls(
    tv: Client,
    subscriber: Subscriber,
    catalog: InMemoryCatalogSource,
    starter: FakeStarter,
    log_output: io.StringIO,
) -> None:
    tv.get(subscriber.path("movie", 1001))  # 302
    tv.get(subscriber.path("series", 424242))  # 404, logged by Django too
    tv.get(f"/movie/{subscriber.username}/wrong-password-1/1001.mp4")  # 404
    tv.get(f"/movie/{subscriber.username}/{subscriber.password}/oops")  # no route
    starter.outcome = PlayRefused("CONCURRENCY_LIMIT")
    tv.get(subscriber.path("movie", 1001))  # 403, and the refusal is logged
    text = assert_clean(log_output, subscriber.username, subscriber.password, "wrong-password-1")
    assert "/movie/***/***/1001.mp4" in text
    assert "CONCURRENCY_LIMIT" in text


def test_tracebacks(
    subscriber: Subscriber,
    catalog: InMemoryCatalogSource,
    starter: FakeStarter,
    log_output: io.StringIO,
) -> None:
    path = subscriber.path("movie", 1001)
    starter.error = RuntimeError(f"edge refused {path}?username={subscriber.username}")
    client = Client(headers={"host": settings.TV_HOST}, raise_request_exception=False)
    response = client.get(path)
    assert (response.status_code, response.content) == (500, b"")
    text = assert_clean(log_output, subscriber.username, subscriber.password)
    assert "RuntimeError" in text


def test_access_log_lines_would_be_redacted_too(log_output: io.StringIO) -> None:
    """Uvicorn's access log format, if it were ever switched on and routed here."""
    logging.getLogger("uvicorn.access").warning(
        '%s - "%s %s HTTP/%s" %d',
        "203.0.113.9:51000",
        "GET",
        "/series/mah-7k3p9q/s3cr3tpass99/5001.mp4?username=mah-7k3p9q&password=s3cr3tpass99",
        "1.1",
        302,
    )
    assert_clean(log_output, "mah-7k3p9q", "s3cr3tpass99")


def test_server_access_logs_are_off() -> None:
    """Production gunicorn (config/gunicorn_conf.py) never writes an access log; dev's
    uvicorn runs with --no-access-log (docker/compose.dev.yml, docker/app.Dockerfile)."""
    path = Path(settings.BASE_DIR) / "config" / "gunicorn_conf.py"
    spec = importlib.util.spec_from_file_location("gunicorn_conf_under_test", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.accesslog is None
