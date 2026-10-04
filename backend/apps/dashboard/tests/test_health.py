"""GET /api/v1/admin/health: services, queues, transcoders, Redis and Postgres."""

import http.server
import json
import threading
import time
from collections.abc import Iterator
from typing import Any, cast

import pytest
import redis
from django.conf import settings
from rest_framework.test import APIClient

from apps.conftest import AdminFactory
from apps.core.health import CheckResult
from apps.core.stores import state_redis
from apps.core.tasks import HEARTBEAT_KEY
from apps.dashboard import health
from apps.library.watcher import HEARTBEAT_KEY as WATCHER_KEY
from apps.media import hwdetect
from apps.media import services as media

pytestmark = pytest.mark.django_db

ADMIN = {"host": settings.ADMIN_HOST}
URL = "/api/v1/admin/health"
CAPS_KEY = hwdetect.CAPS_KEY_TEMPLATE.format(host="test-transcoder")


class _Healthz(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200 if self.path == "/healthz" else 503)
        self.end_headers()

    def log_message(self, *_args: Any) -> None:
        return


@pytest.fixture
def edge_url(settings: Any) -> Iterator[str]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Healthz)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    settings.EDGE_HEALTH_URLS = [f"{url}/healthz", f"{url}/broken", "ftp://edge/healthz"]
    yield url
    server.shutdown()


@pytest.fixture
def heartbeats() -> Iterator[None]:
    client = state_redis()
    saved = {
        key: cast("bytes | None", client.get(key)) for key in (HEARTBEAT_KEY, WATCHER_KEY, CAPS_KEY)
    }
    client.set(HEARTBEAT_KEY, int(time.time()) - 10)
    client.set(WATCHER_KEY, int(time.time()) - 200)
    client.set(
        CAPS_KEY,
        json.dumps(
            {
                "host": "test-transcoder",
                "ffmpeg": "8.0",
                "checked_at": "2026-10-04T12:00:00+00:00",
                "best": "nvenc",
                "queues": ["transcode.nvenc", "transcode.cpu"],
                "backends": {"nvenc": ["h264", "hevc"], "cpu": ["h264"]},
                "gpus": ["NVIDIA RTX A2000"],
            }
        ),
        ex=60,
    )
    yield
    for key, value in saved.items():
        if value is None:
            client.delete(key)
        else:
            client.set(key, value)


def test_health_reports_every_part(
    owner_client: APIClient, edge_url: str, heartbeats: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        health,
        "run_checks",
        lambda: {
            "postgres": CheckResult(ok=True, latency_ms=1.234),
            "meilisearch": CheckResult(ok=False, latency_ms=2000.0, error="TimeoutError"),
        },
    )
    body = owner_client.get(URL, headers=ADMIN).json()
    services = {item["name"]: item for item in body["services"]}
    assert services["postgres"] == {
        "name": "postgres",
        "status": "ok",
        "latency_ms": 1.2,
        "age_s": None,
        "error": "",
    }
    assert services["meilisearch"]["status"] == "down"
    assert services["meilisearch"]["error"] == "TimeoutError"
    assert services["workers"]["status"] == "ok"
    assert 9 <= services["workers"]["age_s"] <= 12
    assert services["watcher"]["status"] == "degraded"
    assert services["watcher"]["error"] == "late_heartbeat"
    assert services["transcoders"]["status"] == "ok"
    assert services["edge:127.0.0.1"]["status"] in {"ok", "down"}
    edges = [item for item in body["services"] if item["name"].startswith("edge:")]
    assert [item["status"] for item in edges] == ["ok", "down", "down"]
    assert edges[1]["error"] == "HTTPError"
    assert edges[2]["error"] == "bad_url"
    assert body["status"] == "down"

    transcoder = next(item for item in body["transcoders"] if item["host"] == "test-transcoder")
    assert transcoder["best"] == "nvenc"
    assert transcoder["backends"] == {"nvenc": ["h264", "hevc"], "cpu": ["h264"]}
    assert transcoder["gpus"] == ["NVIDIA RTX A2000"]

    queues = {item["name"]: item["depth"] for item in body["queues"]}
    assert set(queues) == set(health.queue_names())
    assert all(isinstance(depth, int) for depth in queues.values())
    stores = {item["name"]: item for item in body["redis"]}
    assert stores["redis_state"]["policy"] == "noeviction"
    assert stores["redis_state"]["evicted_keys"] == 0
    assert stores["redis_cache"]["used_memory"] > 0
    assert body["database"]["connections"] >= 1
    assert body["database"]["max_connections"] >= body["database"]["connections"]


def test_queue_depths_count_every_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    client = redis.Redis.from_url(settings.CELERY_BROKER_URL)
    name = "transcode.qsv"
    keys = [name, f"{name}\x06\x163", f"{name}\x06\x169"]
    saved = {key: cast("list[bytes]", client.lrange(key, 0, -1)) for key in keys}
    try:
        for key in keys:
            client.delete(key)
            client.rpush(key, "message")
        depths = {item.name: item.depth for item in health._queues()}
        assert depths[name] == 3
    finally:
        for key, items in saved.items():
            client.delete(key)
            if items:
                client.rpush(key, *items)


def test_unreachable_parts_are_reported_down(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_args: object, **_kwargs: object) -> Any:
        raise redis.ConnectionError

    class _Broken:
        def get(self, *_args: object) -> Any:
            raise redis.ConnectionError

        def info(self, *_args: object) -> Any:
            raise redis.ConnectionError

    monkeypatch.setattr(health, "state_redis", _Broken)
    monkeypatch.setattr(media, "live_capabilities", broken)
    monkeypatch.setattr(redis.Redis, "pipeline", broken)
    assert health._heartbeat("workers", HEARTBEAT_KEY, time.time()).error == "ConnectionError"
    transcoders, status = health._transcoders()
    assert (transcoders, status.status) == ([], health.Status.DOWN)
    assert {item.depth for item in health._queues()} == {None}
    store = health._redis("redis_state", _Broken, must_not_evict=True)  # type: ignore[arg-type]
    assert (store.status, store.error) == (health.Status.DOWN, "ConnectionError")


def test_evictions_on_redis_state_are_an_outage() -> None:
    class _Evicting:
        def info(self, section: str) -> dict[str, Any]:
            if section == "memory":
                return {"used_memory": 10, "maxmemory": 0, "maxmemory_policy": "noeviction"}
            return {"evicted_keys": 3}

    state = health._redis("redis_state", _Evicting, must_not_evict=True)  # type: ignore[arg-type]
    cache = health._redis("redis_cache", _Evicting, must_not_evict=False)  # type: ignore[arg-type]
    assert (state.status, state.error, state.max_memory) == (health.Status.DOWN, "evictions", None)
    assert cache.status is health.Status.OK


def test_missing_and_stale_heartbeats(monkeypatch: pytest.MonkeyPatch) -> None:
    client = state_redis()
    key = "hb:dashboard-test"
    client.delete(key)
    assert health._heartbeat("x", key, time.time()).error == "no_heartbeat"
    client.set(key, "garbage")
    assert health._heartbeat("x", key, time.time()).error == "bad_heartbeat"
    client.set(key, int(time.time()) - 1000)
    assert health._heartbeat("x", key, time.time()).status is health.Status.DOWN
    client.delete(key)


def test_health_needs_settings_view(make_admin: AdminFactory) -> None:
    client = APIClient()
    client.force_authenticate(make_admin(permissions=["dashboard.view"]))
    assert client.get(URL, headers=ADMIN).status_code == 403
