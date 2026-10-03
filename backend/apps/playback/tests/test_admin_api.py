"""Admin API: the session list, kill, and the live SSE feed (SPEC §8.3.4)."""

import asyncio
import io
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import yaml
from django.conf import settings
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.accounts.models import Device, User
from apps.audit.models import AuditLog
from apps.conftest import AdminFactory, CustomerFactory
from apps.core.ids import uuid7
from apps.core.stores import state_redis
from apps.playback import concurrency, feed, records, services
from apps.playback.api import SessionStreamView
from apps.playback.models import EndReason, PlaybackSession, TitleKind
from apps.playback.records import SessionRecord
from apps.playback.services import PlayableRendition, PlayableTitle, RenditionKind

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("admin_routes")]

ADMIN = {"host": settings.ADMIN_HOST}
SESSIONS = "/api/v1/admin/sessions"
T0 = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


def title(name: str = "The Matrix") -> PlayableTitle:
    return PlayableTitle(
        kind=TitleKind.MOVIE,
        id=uuid7(),
        renditions=(PlayableRendition(uuid7().hex, RenditionKind.COMPAT, 1080),),
        runtime_s=3600,
        name=name,
    )


def play(user: User, device: Device | None = None, **kwargs: Any) -> PlaybackSession:
    device = device or user.devices.order_by("created_at").first()
    assert device is not None
    kwargs.setdefault("now", T0)
    grant = services.start_playback(user, device, title(), client_ip="203.0.113.7", **kwargs)
    return grant.session


def client_for(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user)
    return client


# --- List -------------------------------------------------------------------------------------


def test_list_shows_sessions_newest_first(
    owner_client: APIClient, make_customer: CustomerFactory
) -> None:
    user = make_customer(name="Sara", devices=1)
    first = play(user)
    services.stop_sessions([first], concurrency.KickReason.KICKED)
    second = play(user, now=T0 + timedelta(minutes=5))
    response = owner_client.get(SESSIONS, headers=ADMIN)
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 2
    latest = body["results"][0]
    assert latest["id"] == str(second.pk)
    assert latest["user"] == {"id": str(user.pk), "username": user.username, "name": "Sara"}
    assert latest["device"]["name"] == "Device 1"
    assert (latest["title_kind"], latest["title_name"]) == ("movie", "The Matrix")
    assert (latest["is_active"], latest["end_reason"], latest["ended_at"]) == (True, None, None)
    assert latest["log_ref"] == second.session_key[:16]
    assert "session_key" not in latest
    assert body["results"][1]["end_reason"] == "kicked"


def test_list_filters(owner_client: APIClient, make_customer: CustomerFactory) -> None:
    one, two = make_customer(devices=1), make_customer(devices=1)
    ended = play(one)
    services.stop_sessions([ended], concurrency.KickReason.KICKED)
    live = play(two)
    active = owner_client.get(SESSIONS, {"active": "true"}, headers=ADMIN).json()
    assert [row["id"] for row in active["results"]] == [str(live.pk)]
    mine = owner_client.get(SESSIONS, {"user": str(one.pk)}, headers=ADMIN).json()
    assert [row["id"] for row in mine["results"]] == [str(ended.pk)]
    reason = owner_client.get(SESSIONS, {"end_reason": "kicked"}, headers=ADMIN).json()
    assert reason["count"] == 1


def test_list_query_count_does_not_grow(
    owner_client: APIClient, make_customer: CustomerFactory
) -> None:
    play(make_customer(devices=1))
    with CaptureQueriesContext(connection) as one:
        owner_client.get(SESSIONS, headers=ADMIN)
    for _ in range(4):
        play(make_customer(devices=1))
    with CaptureQueriesContext(connection) as five:
        response = owner_client.get(SESSIONS, headers=ADMIN)
    assert response.json()["count"] == 5
    assert len(five) == len(one) <= 5


def test_list_needs_customers_view(make_admin: AdminFactory) -> None:
    content = client_for(make_admin("content_manager"))
    assert content.get(SESSIONS, headers=ADMIN).status_code == 403
    support = client_for(make_admin("support"))
    assert support.get(SESSIONS, headers=ADMIN).status_code == 200
    assert APIClient().get(SESSIONS, headers=ADMIN).status_code in {401, 403}


# --- Kill -------------------------------------------------------------------------------------


def test_kill_stops_the_session(
    owner: User, owner_client: APIClient, make_customer: CustomerFactory
) -> None:
    session = play(make_customer(devices=1))
    response = owner_client.post(f"{SESSIONS}/{session.pk}/kill", headers=ADMIN)
    assert response.status_code == 200
    body = response.json()
    assert (body["is_active"], body["end_reason"]) == (False, "kicked")
    session.refresh_from_db()
    assert session.end_reason == EndReason.KICKED
    kick = state_redis().get(concurrency.kick_key(session.session_key))
    assert kick == b"kicked"
    assert AuditLog.objects.filter(action="session.kill", actor=owner).count() == 1
    again = owner_client.post(f"{SESSIONS}/{session.pk}/kill", headers=ADMIN)
    assert (again.status_code, again.json()["code"]) == (409, "CONFLICT")


def test_kill_needs_sessions_kill(make_admin: AdminFactory, make_customer: CustomerFactory) -> None:
    session = play(make_customer(devices=1))
    viewer = client_for(make_admin("viewer"))
    assert viewer.post(f"{SESSIONS}/{session.pk}/kill", headers=ADMIN).status_code == 403
    support = client_for(make_admin("support"))
    assert support.post(f"{SESSIONS}/{session.pk}/kill", headers=ADMIN).status_code == 200


def test_kill_unknown_session(owner_client: APIClient) -> None:
    response = owner_client.post(f"{SESSIONS}/{uuid7()}/kill", headers=ADMIN)
    assert response.status_code == 404


# --- Live feed --------------------------------------------------------------------------------


def collect(events: Any) -> list[bytes]:
    async def run() -> list[bytes]:
        client, _script = concurrency.async_state()
        try:
            return [chunk async for chunk in events]
        finally:
            await client.aclose()

    return asyncio.run(run())


def live(number: int, **fields: Any) -> SessionRecord:
    values: dict[str, Any] = {
        "session": f"{number:032x}",
        "row": str(uuid7()),
        "user": str(uuid7()),
        "device": str(uuid7()),
        "title": f"movie:{uuid7()}",
        "rendition": "compat",
        "delivery": "progressive",
        "started": T0.timestamp(),
        "seen": T0.timestamp(),
        "exp": int(T0.timestamp()) + 7200,
        "idle": 7800,
        "bytes": 0,
        "ip": "203.0.113.7",
        "country": "SA",
        "edge": "edge-a",
        "max_streams": 1,
        "policy": "reject",
        "user_name": "Omar",
        "device_name": "TV",
        "title_name": "The Matrix",
    }
    values.update(fields)
    record = SessionRecord(**values)
    records.write_record(record, ttl_s=600)
    return record


def test_feed_sends_a_snapshot_then_diffs() -> None:
    first = live(1)
    steps: list[Callable[[], object]] = [
        lambda: live(2, user_name="Sara"),
        lambda: concurrency.finish(first.session, concurrency.KickReason.KICKED),
    ]

    async def sleep(_seconds: float) -> None:
        steps.pop(0)()

    events = collect(feed.session_events(interval=0, polls=2, sleep=sleep))
    assert events[0].startswith(b"retry: 5000\nevent: snapshot\ndata: ")
    snapshot = json.loads(events[0].split(b"data: ", 1)[1])
    assert snapshot == {
        "sessions": [
            {
                "id": first.row,
                "user": {"id": first.user, "name": "Omar"},
                "device": {"id": first.device, "name": "TV"},
                "title": {"kind": "movie", "id": first.title[6:], "name": "The Matrix"},
                "rendition": "compat",
                "ip": "203.0.113.7",
                "country": "SA",
                "edge": "edge-a",
                "started_at": T0.isoformat(),
                "last_seen_at": T0.isoformat(),
                "bytes_sent": 0,
            }
        ]
    }
    added = json.loads(events[1].split(b"data: ", 1)[1])
    assert events[1].startswith(b"event: diff\n")
    assert [entry["user"]["name"] for entry in added["added"]] == ["Sara"]
    removed = json.loads(events[2].split(b"data: ", 1)[1])
    assert removed == {"added": [], "updated": [], "removed": [first.row]}


def test_feed_keeps_quiet_streams_alive() -> None:
    ticks = iter([0.0, 20.0, 21.0, 22.0])

    async def sleep(_seconds: float) -> None:
        return None

    events = collect(
        feed.session_events(interval=0, polls=2, sleep=sleep, clock=lambda: next(ticks))
    )
    assert events == [
        b'retry: 5000\nevent: snapshot\ndata: {"sessions":[]}\n\n',
        b": keep-alive\n\n",
    ]


def test_diff_reports_changes_only() -> None:
    before = {"a": {"id": "a", "bytes_sent": 1}, "b": {"id": "b", "bytes_sent": 1}}
    after = {"a": {"id": "a", "bytes_sent": 2}, "c": {"id": "c", "bytes_sent": 0}}
    assert feed.diff(before, after) == {
        "added": [{"id": "c", "bytes_sent": 0}],
        "updated": [{"id": "a", "bytes_sent": 2}],
        "removed": ["b"],
    }
    assert feed.diff(after, after) is None


def test_stream_endpoint(
    owner_client: APIClient,
    make_admin: AdminFactory,
    monkeypatch: pytest.MonkeyPatch,
    settings: Any,
) -> None:
    settings.PLAYBACK_FEED_INTERVAL_MS = 1
    monkeypatch.setattr(SessionStreamView, "polls", 1)
    response = owner_client.get(
        f"{SESSIONS}/stream", headers={**ADMIN, "Accept": "text/event-stream"}
    )
    assert response.status_code == 200
    assert response["Content-Type"] == "text/event-stream"
    assert response["Cache-Control"] == "no-store"
    with pytest.warns(Warning, match="asynchronous iterators"):
        body = b"".join(response)
    assert body.startswith(b"retry: 5000\nevent: snapshot\n")
    denied = client_for(make_admin("content_manager")).get(
        f"{SESSIONS}/stream", headers={**ADMIN, "Accept": "text/event-stream"}
    )
    assert denied.status_code == 403


# --- OpenAPI ----------------------------------------------------------------------------------


def test_schema_has_no_warnings_and_stable_ids() -> None:
    out = io.StringIO()
    call_command(
        "spectacular",
        "--urlconf",
        "apps.playback.tests.urls",
        "--validate",
        "--fail-on-warn",
        stdout=out,
    )
    schema = yaml.safe_load(out.getvalue())
    paths = schema["paths"]
    assert paths["/api/v1/admin/sessions"]["get"]["operationId"] == "sessions_list"
    assert paths["/api/v1/admin/sessions/{id}/kill"]["post"]["operationId"] == "sessions_kill"
    stream = paths["/api/v1/admin/sessions/stream"]["get"]
    assert stream["operationId"] == "sessions_stream"
    assert "text/event-stream" in stream["responses"]["200"]["content"]
