"""The edges host (ADR-0022): the control channel and stream-auth for standalone edges.

stream-auth there stays Redis-only like the internal endpoint: its tests forbid the
database. The control channel (poll, ping) records reports in Postgres.
"""

import json
import uuid
from typing import Any

import pytest
from django.conf import settings
from django.db.backends.base.base import BaseDatabaseWrapper
from django.test import Client

from apps.core.stores import state_redis
from apps.edges import services
from apps.edges.models import Edge, EdgeKind, EdgeStorage
from apps.playback import concurrency, records, tokens
from apps.playback.records import SessionRecord

NOW = 1_800_000_000
SESSION = "0192f3a4b5c67d8e9f00112233445566"
USER = str(uuid.UUID(int=1))
DEVICE = str(uuid.UUID(int=2))
EDGES = {"host": settings.EDGES_HOST}


@pytest.fixture
def edge_secret(db: None) -> str:
    secret = services.new_secret()
    Edge.objects.create(
        edge_id="media2",
        name="Media 2",
        kind=EdgeKind.STANDALONE,
        storage=EdgeStorage.ORIGIN,
        base_url="https://media2.example.test",
        secret_hash=services.hash_secret(secret),
    )
    services.rebuild_auth_mirror()
    return secret


# --- The control channel -------------------------------------------------------------


@pytest.mark.django_db
def test_poll_records_the_report_and_answers_the_keys(
    edge_secret: str, media_keys: tokens.KeySet
) -> None:
    body = {"kids": ["k1"], "connections": {"active": 3}, "tx_bytes": 5}
    response = Client().post(
        "/edge/v1/poll",
        data=json.dumps(body),
        content_type="application/json",
        headers={**EDGES, "Authorization": f"Bearer {edge_secret}", "X-Edge-Id": "media2"},
    )
    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    answer = response.json()
    assert answer["keys"]["current"]["kid"] == media_keys.current.kid
    edge = Edge.objects.get(edge_id="media2")
    assert edge.report["connections"] == {"active": 3}
    assert edge.last_seen_at is not None


@pytest.mark.django_db
@pytest.mark.parametrize("case", ["no_secret", "wrong_secret", "wrong_edge", "disabled"])
def test_poll_and_ping_refuse_strangers(edge_secret: str, case: str) -> None:
    secret, edge = edge_secret, "media2"
    if case == "no_secret":
        secret = ""
    elif case == "wrong_secret":
        secret = services.new_secret()
    elif case == "wrong_edge":
        edge = "media3"
    else:
        Edge.objects.filter(edge_id="media2").update(enabled=False)
    headers = {**EDGES, "X-Edge-Id": edge}
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
    poll = Client().post(
        "/edge/v1/poll", data="{}", content_type="application/json", headers=headers
    )
    ping = Client().get("/edge/v1/ping", headers=headers)
    assert (poll.status_code, ping.status_code) == (401, 401)
    assert "keys" not in poll.content.decode()


@pytest.mark.django_db
def test_ping_tells_the_installer_what_it_needs(edge_secret: str) -> None:
    from apps.core.services import set_setting  # noqa: PLC0415

    set_setting("storage.s3_endpoint", "https://s3.example.test", actor=None)
    set_setting("storage.s3_bucket", "media", actor=None)
    response = Client().get(
        "/edge/v1/ping",
        headers={**EDGES, "Authorization": f"Bearer {edge_secret}", "X-Edge-Id": "media2"},
    )
    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    body = response.json()
    assert body["edge_id"] == "media2"
    assert body["storage"] == "origin"
    assert body["origin_url"] == "https://s3.example.test/media/renditions"
    assert len(body["cors_origins"]) == 2
    assert "secret" not in response.content.decode()


@pytest.mark.django_db
def test_the_edges_host_serves_nothing_else(edge_secret: str) -> None:
    headers = {**EDGES, "Authorization": f"Bearer {edge_secret}", "X-Edge-Id": "media2"}
    for path in ("/internal/stream-auth", "/metrics", "/api/v1/health", "/internal/health/ready"):
        assert Client().get(path, headers=headers).status_code == 404, path


@pytest.mark.django_db
def test_the_control_channel_is_not_on_other_hosts(edge_secret: str) -> None:
    headers = {"Authorization": f"Bearer {edge_secret}", "X-Edge-Id": "media2"}
    for host in (settings.API_HOST, settings.TV_HOST, "web"):
        response = Client().get("/edge/v1/ping", headers={"host": host, **headers})
        assert response.status_code == 404, host


# --- stream-auth (Redis only) ----------------------------------------------------------


@pytest.fixture
def live_session(edge_secret: str, monkeypatch: pytest.MonkeyPatch) -> SessionRecord:
    monkeypatch.setattr("apps.playback.views._clock", lambda: float(NOW))
    record = SessionRecord(
        session=SESSION, row=str(uuid.UUID(int=3)), user=USER, device=DEVICE,
        title=f"movie:{uuid.UUID(int=4)}", rendition="compat", delivery="progressive",
        started=NOW, seen=NOW, exp=NOW + 7200, idle=7800, bytes=0, ip="203.0.113.7",
        country="", edge="", max_streams=1, policy="reject", user_name="Customer",
        device_name="TV", title_name="The Matrix",
    )  # fmt: skip
    records.write_record(record, ttl_s=9000)
    concurrency.acquire(
        user_id=USER, session=SESSION, device_id=DEVICE, max_streams=1, policy="reject", now=NOW
    )
    return record


@pytest.fixture
def no_database(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        msg = "stream-auth on the edges host touched the database"
        raise AssertionError(msg)

    monkeypatch.setattr(BaseDatabaseWrapper, "ensure_connection", refuse)


def ask(uri: str, secret: str, *, edge: str = "media2", **headers: str) -> Any:
    sent = {
        **EDGES,
        "X-Original-URI": uri,
        "X-Edge-Client-IP": "203.0.113.7",
        "X-Edge-Id": edge,
        "Authorization": f"Bearer {secret}",
        **headers,
    }
    return Client().get("/edge/v1/stream-auth", headers=sent)


def signed(keys: tokens.KeySet, **fields: Any) -> str:
    values: dict[str, Any] = {
        "session": SESSION, "title": "asset-g1", "rendition": "compat", "exp": NOW + 7200,
    }  # fmt: skip
    values.update(fields)
    return tokens.sign(keys, **values)


@pytest.mark.django_db
def test_stream_auth_allows_and_records_the_edge(
    live_session: SessionRecord, media_keys: tokens.KeySet, edge_secret: str, no_database: None
) -> None:
    response = ask(f"/v/{signed(media_keys)}/compat.mp4", edge_secret)
    assert response.status_code == 204
    record = records.read_record(SESSION)
    assert record is not None
    assert record.edge == "media2"


@pytest.mark.django_db
def test_stream_auth_uses_the_edges_client_address(
    live_session: SessionRecord, media_keys: tokens.KeySet, edge_secret: str, no_database: None
) -> None:
    bound = signed(media_keys, net=tokens.client_net("198.51.100.9"))
    response = ask(f"/v/{bound}/compat.mp4", edge_secret, **{"X-Real-IP": "198.51.100.9"})
    assert (response.status_code, response["X-Reason"]) == (403, "ip_mismatch")
    response = ask(f"/v/{bound}/compat.mp4", edge_secret, **{"X-Edge-Client-IP": "198.51.100.200"})
    assert response.status_code == 204


@pytest.mark.django_db
def test_kicks_bite_through_the_edges_host(
    live_session: SessionRecord, media_keys: tokens.KeySet, edge_secret: str, no_database: None
) -> None:
    concurrency.finish(SESSION, concurrency.KickReason.KICKED)
    response = ask(f"/v/{signed(media_keys)}/compat.mp4", edge_secret)
    assert (response.status_code, response["X-Reason"]) == (403, "kicked")


@pytest.mark.django_db
def test_stream_auth_refuses_unknown_edges(
    live_session: SessionRecord, media_keys: tokens.KeySet, edge_secret: str, no_database: None
) -> None:
    uri = f"/v/{signed(media_keys)}/compat.mp4"
    for secret, edge in ((services.new_secret(), "media2"), (edge_secret, "media3")):
        response = ask(uri, secret, edge=edge)
        assert (response.status_code, response["X-Reason"]) == (403, "edge_unauthorized")
    state_redis().delete(services.AUTH_KEY)
    response = ask(uri, edge_secret)
    assert (response.status_code, response["X-Reason"]) == (403, "edge_unauthorized")


@pytest.mark.django_db
def test_the_edge_probe_session_is_allowed_for_its_minute(
    edge_secret: str, media_keys: tokens.KeySet, monkeypatch: pytest.MonkeyPatch,
    no_database: None,
) -> None:  # fmt: skip
    monkeypatch.setattr("apps.playback.views._clock", lambda: float(NOW))
    probe = signed(media_keys, session="f" * 32, title="edgeprobe", rendition="probe")
    response = ask(f"/v/{probe}/probe.txt", edge_secret)
    assert (response.status_code, response["X-Reason"]) == (403, "session_ended")
    state_redis().set("edges:probe:" + "f" * 32, "1", ex=60)
    assert ask(f"/v/{probe}/probe.txt", edge_secret).status_code == 204
