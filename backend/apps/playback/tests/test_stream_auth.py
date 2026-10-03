"""GET /internal/stream-auth: the edge's heartbeat and kick check, from Redis only.

No test here has database access, and `forbid_database` turns any attempt into a
failure: media requests never touch Postgres (CLAUDE.md hard rule).
"""

import uuid
from typing import Any

import pytest
from django.conf import settings
from django.db import connection
from django.db.backends.base.base import BaseDatabaseWrapper
from django.test import Client
from pytest_django import Settings
from redis.exceptions import ConnectionError as RedisConnectionError

from apps.core.stores import state_redis
from apps.playback import concurrency, records, tokens
from apps.playback.records import SessionRecord
from apps.playback.views import split_original_uri

NOW = 1_800_000_000
USER = str(uuid.UUID(int=1))
DEVICE = str(uuid.UUID(int=2))
SESSION = "0192f3a4b5c67d8e9f00112233445566"
ASSET = "0192f3a4-b5c6-7d8e-9f00-1122334455aa"
INTERNAL = {"host": "web"}


@pytest.fixture(autouse=True)
def forbid_database(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        msg = "stream-auth touched the database"
        raise AssertionError(msg)

    monkeypatch.setattr(BaseDatabaseWrapper, "ensure_connection", refuse)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """The view's clock; tests move it by changing `clock[0]`."""
    now = [float(NOW)]
    monkeypatch.setattr("apps.playback.views._clock", lambda: now[0])
    return now


@pytest.fixture
def live_session(clock: list[float]) -> SessionRecord:
    """A session as start_playback leaves it: a slot and a record."""
    record = SessionRecord(
        session=SESSION,
        row=str(uuid.UUID(int=3)),
        user=USER,
        device=DEVICE,
        title=f"movie:{uuid.UUID(int=4)}",
        rendition="compat",
        delivery="progressive",
        started=NOW,
        seen=NOW,
        exp=NOW + 7200,
        idle=7800,
        bytes=0,
        ip="203.0.113.7",
        country="",
        edge="",
        max_streams=1,
        policy="reject",
        user_name="Customer",
        device_name="TV",
        title_name="The Matrix",
    )
    records.write_record(record, ttl_s=9000)
    result = concurrency.acquire(
        user_id=USER, session=SESSION, device_id=DEVICE, max_streams=1, policy="reject", now=NOW
    )
    assert result.status is concurrency.SlotStatus.ADDED
    return record


def token(keys: tokens.KeySet, **overrides: Any) -> str:
    fields: dict[str, Any] = {
        "session": SESSION,
        "title": ASSET,
        "rendition": "compat",
        "exp": NOW + 7200,
    }
    fields.update(overrides)
    return tokens.sign(keys, **fields)


def ask(uri: str, **headers: str) -> Any:
    return Client().get(
        "/internal/stream-auth",
        headers={**INTERNAL, "X-Original-URI": uri, "X-Real-IP": "203.0.113.7", **headers},
    )


def test_allows_a_live_session_and_records_the_heartbeat(
    media_keys: tokens.KeySet, live_session: SessionRecord, clock: list[float]
) -> None:
    clock[0] = NOW + 45
    response = ask(
        f"/v/{token(media_keys)}/compat.mp4?x=1", **{"X-Edge-Id": "edge-a", "X-Bytes": "2048"}
    )
    assert response.status_code == 204
    record = records.read_record(SESSION)
    assert record is not None
    assert (record.seen, record.edge, record.bytes) == (NOW + 45, "edge-a", 2048)
    member = concurrency.slot_member(SESSION, DEVICE)
    assert state_redis().zscore(concurrency.conc_key(USER), member) == NOW + 45


def test_hls_tokens_cover_their_directory(
    media_keys: tokens.KeySet, live_session: SessionRecord
) -> None:
    hls = token(media_keys, rendition="hls")
    assert ask(f"/v/{hls}/hls/v720/seg_00001.m4s").status_code == 204
    response = ask(f"/v/{hls}/compat.mp4")
    assert (response.status_code, response["X-Reason"]) == (403, "invalid")


def test_kicked_session_is_refused(media_keys: tokens.KeySet, live_session: SessionRecord) -> None:
    concurrency.finish(SESSION, concurrency.KickReason.KICKED)
    response = ask(f"/v/{token(media_keys)}/compat.mp4")
    assert (response.status_code, response["X-Reason"]) == (403, "kicked")
    assert response["Cache-Control"] == "no-store"


def test_unknown_session_is_refused(media_keys: tokens.KeySet, clock: list[float]) -> None:
    response = ask(f"/v/{token(media_keys)}/compat.mp4")
    assert (response.status_code, response["X-Reason"]) == (403, "session_ended")


@pytest.mark.parametrize(
    ("uri", "reason"),
    [
        ("", "invalid"),
        ("/images/poster.jpg", "invalid"),
        ("/v/not-a-token/compat.mp4", "invalid"),
        ("/v/{token}", "invalid"),
        ("/v/{token}/../compat.mp4", "invalid"),
        ("/v/{token}/uhd.mp4", "invalid"),
        ("/v/{tampered}/compat.mp4", "invalid"),
        ("/v/{expired}/compat.mp4", "expired"),
        ("/v/{stranger}/compat.mp4", "invalid"),
        ("/v/{bound}/compat.mp4", "ip_mismatch"),
    ],
)
def test_bad_tokens_are_refused(
    media_keys: tokens.KeySet, live_session: SessionRecord, uri: str, reason: str
) -> None:
    good = token(media_keys)
    stranger = tokens.KeySet(tokens.Key("k9", bytes(range(100, 132))))
    values = {
        "token": good,
        "tampered": good[:-2] + ("AA" if not good.endswith("AA") else "BB"),
        "expired": token(media_keys, exp=NOW - tokens.CLOCK_SKEW_S),
        "stranger": token(stranger),
        "bound": token(media_keys, net=tokens.client_net("198.51.100.1")),
    }
    response = ask(uri.format(**values))
    assert (response.status_code, response["X-Reason"]) == (403, reason)


def test_bound_token_from_its_network_is_allowed(
    media_keys: tokens.KeySet, live_session: SessionRecord
) -> None:
    bound = token(media_keys, net=tokens.client_net("203.0.113.99"))
    assert ask(f"/v/{bound}/compat.mp4").status_code == 204


def test_second_device_after_a_lapse_is_refused(
    media_keys: tokens.KeySet, live_session: SessionRecord, clock: list[float]
) -> None:
    # The slot lapsed during one long response and another device took it.
    concurrency.acquire(
        user_id=USER,
        session="f" * 32,
        device_id=str(uuid.UUID(int=99)),
        max_streams=1,
        policy="reject",
        now=NOW + 200,
    )
    clock[0] = NOW + 210
    response = ask(f"/v/{token(media_keys)}/compat.mp4")
    assert (response.status_code, response["X-Reason"]) == (403, "stream_limit")


def test_unusable_keys_fail_closed(
    settings: Settings, live_session: SessionRecord, media_keys: tokens.KeySet
) -> None:
    good = token(media_keys)
    settings.MEDIA_TOKEN_KEYS_FILE = "/nonexistent/keys.json"  # noqa: S105 (a path)
    tokens.reset_keyring()
    assert ask(f"/v/{good}/compat.mp4").status_code == 503


def test_redis_outage_fails_closed(
    monkeypatch: pytest.MonkeyPatch, media_keys: tokens.KeySet, clock: list[float]
) -> None:
    async def down(*args: object, **kwargs: object) -> None:
        raise RedisConnectionError

    monkeypatch.setattr(concurrency, "heartbeat", down)
    assert ask(f"/v/{token(media_keys)}/compat.mp4").status_code == 503


def test_only_safe_methods(media_keys: tokens.KeySet) -> None:
    response = Client().post("/internal/stream-auth", headers=INTERNAL)
    assert response.status_code == 405


@pytest.mark.parametrize("host", [settings.API_HOST, settings.TV_HOST, settings.ADMIN_HOST])
def test_not_reachable_from_public_hosts(media_keys: tokens.KeySet, host: str) -> None:
    response = Client().get(
        "/internal/stream-auth",
        headers={"host": host, "X-Original-URI": f"/v/{token(media_keys)}/compat.mp4"},
    )
    assert response.status_code == 404


def test_original_uri_parsing() -> None:
    assert split_original_uri("/v/abc/compat.mp4?t=1") == ("abc", "compat.mp4")
    assert split_original_uri("/v/abc/hls/master.m3u8") == ("abc", "hls/master.m3u8")
    assert split_original_uri("/v/abc") is None
    assert split_original_uri("/v//compat.mp4") is None
    assert split_original_uri("/x/abc/compat.mp4") is None


def test_the_database_guard_is_armed() -> None:
    with pytest.raises(AssertionError, match="touched the database"):
        connection.ensure_connection()
