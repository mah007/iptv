"""Stream slots and live session state (SPEC §7.4): the typed wrapper around
`concurrency.lua`, whose header documents the keys.

- `acquire` takes a slot when a stream starts: it prunes slots idle longer than
  the window, refreshes the session's own slot, ends the device's other session,
  adds the slot when the user is under `max_streams`, and otherwise evicts the
  oldest slots under `kick_oldest` or refuses. Evicted sessions get `kick:` set and
  lose their records.
- `heartbeat` (stream-auth, async) checks the kick flag, the session record and the
  entitlement, refreshes the slot (or takes a lapsed one back when there is room)
  and records the activity. It never evicts.
- `finish` stops a session (kill, expiry, ...) and `reap` closes an idle one.
- `active_streams` counts the slots a user holds now (Xtream `active_cons`).

Every operation is one atomic script call, so concurrent starts can never hand
out more slots than `max_streams`.
"""

import asyncio
import time
import weakref
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from pathlib import Path
from typing import cast
from uuid import UUID

import redis.asyncio
from django.conf import settings
from redis.commands.core import AsyncScript, Script

from apps.core.stores import state_redis
from apps.playback import conf
from apps.playback.entitlements import KEY_PREFIX as ENTITLEMENT_PREFIX

CONC_PREFIX = "conc:"
KICK_PREFIX = "kick:"
SESS_PREFIX = "sess:"
INDEX_KEY = "sess:index"

SCRIPT = Path(__file__).with_name("concurrency.lua").read_text(encoding="utf-8")


class KickReason(StrEnum):
    """Why a session was stopped: stored in `kick:` and sent to players as X-Reason."""

    KICKED = "kicked"  # an admin stopped it
    STREAM_LIMIT = "stream_limit"  # a newer stream took its slot (kick_oldest)
    REPLACED = "replaced"  # its device started another title
    ACCESS_EXPIRED = "access_expired"
    ACCESS_SUSPENDED = "access_suspended"
    DEVICE_DISABLED = "device_disabled"


class SlotStatus(StrEnum):
    ADDED = "added"  # a new slot: the session starts (again)
    REFRESHED = "refreshed"  # the session still held its slot: same playback
    REJECTED = "rejected"  # the stream limit is reached


@dataclass(frozen=True, slots=True)
class Eviction:
    session: str
    reason: KickReason
    last_seen: float | None
    bytes_sent: int


@dataclass(frozen=True, slots=True)
class SlotResult:
    status: SlotStatus
    evicted: tuple[Eviction, ...] = ()


@dataclass(frozen=True, slots=True)
class HeartbeatResult:
    allowed: bool
    # Allowed: "refreshed" or "readded". Denied: the X-Reason for the edge.
    reason: str


@dataclass(frozen=True, slots=True)
class Finished:
    last_seen: float | None
    bytes_sent: int


@dataclass(frozen=True, slots=True)
class Reaped:
    session: str
    row: str
    last_seen: float
    bytes_sent: int


def conc_key(user_id: UUID | str) -> str:
    return f"{CONC_PREFIX}{user_id}"


def kick_key(session: str) -> str:
    return f"{KICK_PREFIX}{session}"


def sess_key(session: str) -> str:
    return f"{SESS_PREFIX}{session}"


def slot_member(session: str, device_id: UUID | str) -> str:
    return f"{session}:{device_id}"


def _text(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _float(value: object) -> float | None:
    text = _text(value)
    return float(text) if text else None


def _int(value: object) -> int:
    text = _text(value)
    return int(text) if text else 0


@cache
def _script() -> Script:
    return state_redis().register_script(SCRIPT)


def acquire(  # noqa: PLR0913 (keyword-only: each is one input of the slot decision)
    *,
    user_id: UUID | str,
    session: str,
    device_id: UUID | str,
    max_streams: int,
    policy: str,
    now: float,
) -> SlotResult:
    """Take (or refresh) the stream slot of `session` for a playback start."""
    if max_streams < 1:
        return SlotResult(SlotStatus.REJECTED)
    reply = cast(
        "list[bytes]",
        _script()(
            keys=[conc_key(user_id), kick_key(session), INDEX_KEY],
            args=[
                "start",
                slot_member(session, device_id),
                repr(now),
                conf.slot_window_s(),
                max_streams,
                policy,
                conf.heartbeat_ttl_s(),
                conf.kick_ttl_s(),
                KICK_PREFIX,
                SESS_PREFIX,
            ],
        ),
    )
    status = SlotStatus(_text(reply[0]))
    evicted = tuple(
        Eviction(
            session=_text(reply[index]),
            reason=KickReason(_text(reply[index + 1])),
            last_seen=_float(reply[index + 2]),
            bytes_sent=_int(reply[index + 3]),
        )
        for index in range(1, len(reply), 4)
    )
    return SlotResult(status, evicted)


def active_streams(user_id: UUID | str, *, now: float | None = None) -> int:
    """The streams `user_id` plays now: members of `conc:{user}` active within the slot
    window (90 s), the ones `acquire` would count. Read-only: one ZCOUNT."""
    moment = time.time() if now is None else now
    since = moment - conf.slot_window_s()
    return cast("int", state_redis().zcount(conc_key(user_id), repr(since), "+inf"))


def release(user_id: UUID | str, session: str, device_id: UUID | str) -> None:
    """Give a slot back without stopping anything (a start that failed after acquire)."""
    state_redis().zrem(conc_key(user_id), slot_member(session, device_id))


def finish(session: str, reason: KickReason) -> Finished:
    """Stop a session: set `kick:` (the edge's next stream-auth refuses it), free its
    slot and drop its record. Returns what the record knew."""
    reply = cast(
        "list[bytes]",
        _script()(
            keys=[sess_key(session), kick_key(session), INDEX_KEY],
            args=["finish", session, reason.value, conf.kick_ttl_s(), CONC_PREFIX],
        ),
    )
    return Finished(last_seen=_float(reply[0]), bytes_sent=_int(reply[1]))


def reap(session: str, now: float) -> Reaped | None:
    """Close the session if it has been idle past its record's idle time.

    None when it is still live; a reaped session without a record (already gone)
    returns None too, after dropping it from the index.
    """
    reply = cast(
        "list[bytes]",
        _script()(
            keys=[sess_key(session), INDEX_KEY],
            args=["reap", session, repr(now), CONC_PREFIX, conf.heartbeat_ttl_s()],
        ),
    )
    if _text(reply[0]) != "reaped":
        return None
    last_seen = _float(reply[2])
    return Reaped(
        session=session,
        row=_text(reply[1]),
        last_seen=last_seen if last_seen is not None else now,
        bytes_sent=_int(reply[3]),
    )


# --- Async access for stream-auth and the live feed -----------------------------------

type AsyncState = tuple[redis.asyncio.Redis, AsyncScript]

_async_clients: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, AsyncState] = (
    weakref.WeakKeyDictionary()
)


def async_state() -> AsyncState:
    """An asyncio client of redis-state (and the script) for the running event loop."""
    loop = asyncio.get_running_loop()
    entry = _async_clients.get(loop)
    if entry is None:
        client = redis.asyncio.Redis.from_url(
            settings.REDIS_STATE_URL,
            socket_timeout=2,
            socket_connect_timeout=2,
            health_check_interval=30,
        )
        entry = (client, client.register_script(SCRIPT))
        _async_clients[loop] = entry
    return entry


async def heartbeat(
    session: str, *, now: float, ip: str = "", edge: str = "", bytes_sent: int = 0
) -> HeartbeatResult:
    """stream-auth's check and refresh for one session (Redis only, one round trip)."""
    _client, script = async_state()
    reply = cast(
        "list[bytes]",
        await script(
            keys=[sess_key(session), kick_key(session), INDEX_KEY],
            args=[
                "heartbeat",
                session,
                repr(now),
                conf.slot_window_s(),
                conf.heartbeat_ttl_s(),
                CONC_PREFIX,
                ENTITLEMENT_PREFIX,
                ip,
                edge,
                max(0, bytes_sent),
            ],
        ),
    )
    return HeartbeatResult(allowed=_text(reply[0]) == "allow", reason=_text(reply[1]))
