"""Xtream request metrics and the `route` log field (SPEC §14, ADR-0018)."""

import io
import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest
from django.conf import settings
from django.test import Client, RequestFactory
from prometheus_client import REGISTRY

from apps.core.middleware import XTREAM_ACTIONS, xtream_action

USERNAME = "alice"
PASSWORD = "s3cr3t-pass"  # noqa: S105 (a fake credential the test hunts for)


def sample(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


@pytest.fixture
def log_output() -> Iterator[io.StringIO]:
    root = logging.getLogger()
    console = next(handler for handler in root.handlers if handler.get_name() == "console")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(console.formatter)
    root.addHandler(handler)
    yield stream
    root.removeHandler(handler)


def request_lines(stream: io.StringIO) -> list[dict[str, Any]]:
    lines = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
    return [line for line in lines if line.get("logger") == "apps.request"]


@pytest.mark.parametrize(
    ("method", "path", "data", "action"),
    [
        ("get", "/player_api.php", {"username": "u", "password": "p"}, "login"),
        ("get", "/player_api.php", {"action": "get_vod_streams"}, "get_vod_streams"),
        ("get", "/player_api.php", {"action": "get_live_streams"}, "get_live_streams"),
        ("get", "/player_api.php", {"action": "drop table"}, "other"),
        ("post", "/player_api.php", {"action": "get_series_info"}, "get_series_info"),
        ("get", "/get.php", {"type": "m3u_plus"}, "m3u"),
        ("get", "/xmltv.php", {}, "xmltv"),
        ("get", "/movie/u/p/10.mp4", {}, "play_movie"),
        ("get", "/series/u/p/11.mkv", {}, "play_series"),
        ("get", "/live/u/p/12.ts", {}, "play_live"),
        ("get", "/timeshift/u/p/60/2026-10-03:12-00/5.ts", {}, "play_timeshift"),
        ("get", "/u/p/12", {}, "play_live"),
        ("get", "/u/p/12.m3u8", {}, "play_live"),
        ("get", "/health", {}, "other"),
        ("get", "/a/b/c/d", {}, "other"),
    ],
)
def test_actions_come_from_a_fixed_set(
    method: str, path: str, data: dict[str, str], action: str
) -> None:
    request = getattr(RequestFactory(), method)(path, data)
    assert xtream_action(request) == action
    assert action in XTREAM_ACTIONS | {
        "login",
        "m3u",
        "xmltv",
        "play_movie",
        "play_series",
        "play_live",
        "play_timeshift",
        "other",
    }


@pytest.mark.django_db
def test_xtream_requests_are_counted_and_timed() -> None:
    labels = {"action": "login", "status": "200"}
    before = sample("iptv_xtream_requests_total", **labels)
    timed = sample("iptv_xtream_latency_seconds_count", action="login")
    response = Client().get(
        "/player_api.php",
        {"username": USERNAME, "password": PASSWORD},
        headers={"host": settings.TV_HOST},
    )
    assert response.status_code == 200
    assert sample("iptv_xtream_requests_total", **labels) == before + 1
    assert sample("iptv_xtream_latency_seconds_count", action="login") == timed + 1


@pytest.mark.django_db
def test_other_hosts_are_not_counted_as_xtream() -> None:
    before = sum(
        sample("iptv_xtream_requests_total", action="other", status=status)
        for status in ("200", "404")
    )
    Client().get("/api/v1/health", headers={"host": settings.API_HOST})
    Client().get("/nope", headers={"host": settings.API_HOST})
    after = sum(
        sample("iptv_xtream_requests_total", action="other", status=status)
        for status in ("200", "404")
    )
    assert after == before


@pytest.mark.django_db
def test_request_lines_name_the_route_never_the_credentials(log_output: io.StringIO) -> None:
    Client().get("/api/v1/health", headers={"host": settings.API_HOST})
    Client().get(f"/movie/{USERNAME}/{PASSWORD}/1.mp4", headers={"host": settings.TV_HOST})
    health, play = request_lines(log_output)
    assert health["route"] == "api/v1/health"
    # A regex pattern logs the view name instead of the pattern.
    assert play["route"] == "xtream-play-movie"
    assert PASSWORD not in log_output.getvalue()
    assert USERNAME not in log_output.getvalue()


@pytest.mark.django_db
def test_unresolved_requests_have_no_route(log_output: io.StringIO) -> None:
    Client().get("/nope", headers={"host": settings.API_HOST})
    (line,) = request_lines(log_output)
    assert "route" not in line
