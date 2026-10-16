"""POST /internal/stream-bytes: the local edge's byte reports (G-17, ADR-0024), Redis only.

Like stream-auth, `forbid_database` fails any test that reaches Postgres."""

import json
from typing import Any, cast

import pytest
from django.conf import settings
from django.db.backends.base.base import BaseDatabaseWrapper
from django.test import Client
from redis.exceptions import ConnectionError as RedisConnectionError

from apps.core.stores import state_redis
from apps.playback import bytes as served
from apps.playback import concurrency

INTERNAL = {"host": "web", "X-Edge-Auth": settings.EDGE_AUTH_TOKEN}
LIVE = "0192f3a4b5c67d8e9f00112233445566"
GONE = "0192f3a4b5c67d8e9f00112233440000"


@pytest.fixture(autouse=True)
def forbid_database(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        msg = "stream-bytes touched the database"
        raise AssertionError(msg)

    monkeypatch.setattr(BaseDatabaseWrapper, "ensure_connection", refuse)


def report(body: object, **headers: str) -> Any:
    return Client().post(
        "/internal/stream-bytes",
        data=json.dumps(body),
        content_type="application/json",
        headers={**INTERNAL, **headers},
    )


def test_bytes_go_to_live_records_and_closed_sessions_wait_for_the_sweeper() -> None:
    state_redis().hset(concurrency.sess_key(LIVE), mapping={"bytes": 100, "row": "r"})
    response = report({"sessions": {LIVE: 4096, GONE: 50, "not-a-session": 7}})
    assert response.status_code == 204
    assert cast("bytes", state_redis().hget(concurrency.sess_key(LIVE), "bytes")) == b"4196"
    assert not state_redis().exists(concurrency.sess_key(GONE))
    assert served.drain_late() == {GONE: 50}
    assert served.drain_late() == {}


@pytest.mark.parametrize(
    ("headers", "body", "status"),
    [
        ({"X-Edge-Auth": "wrong"}, {"sessions": {}}, 403),
        ({}, ["a list"], 400),
        ({}, {"sessions": "nope"}, 204),
    ],
)
def test_callers_and_bodies_are_checked(headers: dict[str, str], body: object, status: int) -> None:
    assert report(body, **headers).status_code == status


def test_a_body_that_isnt_json_or_is_too_big() -> None:
    client = Client()
    bad = client.post(
        "/internal/stream-bytes", data=b"{", content_type="application/json", headers=INTERNAL
    )
    assert bad.status_code == 400
    huge = client.post(
        "/internal/stream-bytes",
        data=b" " * (600 * 1024),
        content_type="application/json",
        headers=INTERNAL,
    )
    assert huge.status_code == 413
    assert client.get("/internal/stream-bytes", headers=INTERNAL).status_code == 405


def test_without_redis_the_edge_keeps_its_report(monkeypatch: pytest.MonkeyPatch) -> None:
    def down(sessions: dict[str, int]) -> tuple[int, int]:
        raise RedisConnectionError

    monkeypatch.setattr(served, "add", down)
    assert report({"sessions": {LIVE: 1}}).status_code == 503
