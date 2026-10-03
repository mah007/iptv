"""The admin live-session feed (SPEC §7.4): `GET /api/v1/admin/sessions/stream`, SSE.

Read from redis-state only (`sess:index` and the session records), polled every
`conf.feed_interval_s()` (2 s). Events:

- `snapshot`, first: `{"sessions": [Session, ...]}`, every live session;
- `diff`, whenever something changed: `{"added": [...], "updated": [...], "removed": [id]}`;
- a `: keep-alive` comment after 15 s without changes, so proxies keep the stream open.

A Session is `{id, user{id,name}, device{id,name}, title{kind,id,name}, rendition, ip,
country, edge, started_at, last_seen_at, bytes_sent}`; `id` is the PlaybackSession id
that `sessions/{id}/kill` takes. The stream ends if Redis fails; browsers reconnect
on their own (after the `retry` hint) and get a fresh snapshot.
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import structlog
from redis.exceptions import RedisError

from apps.playback.concurrency import async_state
from apps.playback.records import SessionRecord, live_records

logger = structlog.get_logger(__name__)

KEEPALIVE_S = 15.0
RETRY_MS = 5000

type Entry = dict[str, Any]


def _iso(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, tz=UTC).isoformat()


def entry(record: SessionRecord) -> Entry:
    kind, _sep, title_id = record.title.partition(":")
    return {
        "id": record.row,
        "user": {"id": record.user, "name": record.user_name},
        "device": {"id": record.device, "name": record.device_name},
        "title": {"kind": kind, "id": title_id, "name": record.title_name},
        "rendition": record.rendition,
        "ip": record.ip or None,
        "country": record.country,
        "edge": record.edge,
        "started_at": _iso(record.started),
        "last_seen_at": _iso(record.seen),
        "bytes_sent": record.bytes,
    }


def diff(before: dict[str, Entry], after: dict[str, Entry]) -> dict[str, Any] | None:
    """What changed between two snapshots; None when nothing did."""
    added = [value for key, value in after.items() if key not in before]
    updated = [value for key, value in after.items() if key in before and before[key] != value]
    removed = [key for key in before if key not in after]
    if not (added or updated or removed):
        return None
    return {"added": added, "updated": updated, "removed": removed}


def event(name: str, data: object) -> bytes:
    payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    return f"event: {name}\ndata: {payload}\n\n".encode()


async def snapshot() -> dict[str, Entry]:
    client, _script = async_state()
    return {record.row: entry(record) for record in await live_records(client)}


async def session_events(
    *,
    interval: float,
    polls: int | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> AsyncIterator[bytes]:
    """The SSE byte stream; `polls` bounds it (tests), None runs until disconnect."""
    try:
        current = await snapshot()
        yield f"retry: {RETRY_MS}\n".encode() + event("snapshot", {"sessions": [*current.values()]})
        quiet_since = clock()
        count = 0
        while polls is None or count < polls:
            count += 1
            await sleep(interval)
            latest = await snapshot()
            change = diff(current, latest)
            current = latest
            if change is not None:
                yield event("diff", change)
                quiet_since = clock()
            elif clock() - quiet_since >= KEEPALIVE_S:
                yield b": keep-alive\n\n"
                quiet_since = clock()
    except RedisError:
        logger.warning("playback.feed_unavailable")
