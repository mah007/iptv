"""concurrency.lua against the real redis-state (this lane's test database)."""

import asyncio
import json
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any, cast

import pytest

from apps.core.stores import state_redis
from apps.playback import concurrency, records
from apps.playback.concurrency import (
    HeartbeatResult,
    KickReason,
    SlotResult,
    SlotStatus,
    conc_key,
    kick_key,
    sess_key,
)
from apps.playback.records import SessionRecord

NOW = 1_800_000_000.0
USER = str(uuid.UUID(int=1))


def session_id(number: int) -> str:
    return f"{number:032x}"


def device_id(number: int) -> str:
    return str(uuid.UUID(int=1000 + number))


def take(
    session: int,
    device: int,
    *,
    now: float = NOW,
    max_streams: int = 1,
    policy: str = "reject",
    user: str = USER,
) -> SlotResult:
    return concurrency.acquire(
        user_id=user,
        session=session_id(session),
        device_id=device_id(device),
        max_streams=max_streams,
        policy=policy,
        now=now,
    )


def get(key: str) -> bytes | None:
    return cast("bytes | None", state_redis().get(key))


def exists(key: str) -> bool:
    return bool(state_redis().exists(key))


def score(key: str, name: str) -> float | None:
    return cast("float | None", state_redis().zscore(key, name))


def ttl(key: str) -> int:
    return cast("int", state_redis().ttl(key))


def held(user: str = USER) -> list[str]:
    return [
        member.decode()
        for member in cast("list[bytes]", state_redis().zrange(conc_key(user), 0, -1))
    ]


def member(session: int, device: int) -> str:
    return concurrency.slot_member(session_id(session), device_id(device))


def record(session: int, device: int, **fields: Any) -> SessionRecord:
    values: dict[str, Any] = {
        "session": session_id(session),
        "row": str(uuid.UUID(int=5000 + session)),
        "user": USER,
        "device": device_id(device),
        "title": f"movie:{uuid.UUID(int=9000 + session)}",
        "rendition": "compat",
        "delivery": "progressive",
        "started": NOW,
        "seen": NOW,
        "exp": int(NOW) + 7200,
        "idle": 120,
        "bytes": 0,
        "ip": "",
        "country": "",
        "edge": "",
        "max_streams": 1,
        "policy": "reject",
        "user_name": "Customer",
        "device_name": "Living room",
        "title_name": "A film",
    }
    values.update(fields)
    result = SessionRecord(**values)
    records.write_record(result, ttl_s=3600)
    return result


def beat(session: int, *, now: float = NOW, **kwargs: Any) -> HeartbeatResult:
    async def run() -> HeartbeatResult:
        client, _script = concurrency.async_state()
        try:
            return await concurrency.heartbeat(session_id(session), now=now, **kwargs)
        finally:
            await client.aclose()

    return asyncio.run(run())


# --- start -----------------------------------------------------------------------------------


def test_adds_refreshes_and_rejects_at_the_limit() -> None:
    assert take(1, 1).status is SlotStatus.ADDED
    assert take(1, 1, now=NOW + 30).status is SlotStatus.REFRESHED
    assert take(2, 2, now=NOW + 31).status is SlotStatus.REJECTED
    assert held() == [member(1, 1)]
    assert score(conc_key(USER), member(1, 1)) == NOW + 30
    assert 0 < ttl(conc_key(USER)) <= 120


def test_idle_slots_are_pruned_after_the_window() -> None:
    take(1, 1)
    assert take(2, 2, now=NOW + 90).status is SlotStatus.REJECTED
    assert take(2, 2, now=NOW + 90.5).status is SlotStatus.ADDED
    assert held() == [member(2, 2)]
    # Pruning frees the slot but stops nothing: no kick flag.
    assert not exists(kick_key(session_id(1)))


def test_room_for_several_streams() -> None:
    for number in range(3):
        assert take(number, number, max_streams=3).status is SlotStatus.ADDED
    assert take(9, 9, max_streams=3).status is SlotStatus.REJECTED
    assert len(held()) == 3


def test_kick_oldest_evicts_the_least_recently_active() -> None:
    record(1, 1, bytes=4096, seen=NOW + 5)
    take(1, 1, now=NOW)
    take(2, 2, now=NOW + 10, max_streams=2)
    take(1, 1, now=NOW + 20, max_streams=2)  # session 1 is active again
    result = take(3, 3, now=NOW + 30, max_streams=2, policy="kick_oldest")
    assert result.status is SlotStatus.ADDED
    assert [eviction.session for eviction in result.evicted] == [session_id(2)]
    assert result.evicted[0].reason is KickReason.STREAM_LIMIT
    assert sorted(held()) == sorted([member(1, 1), member(3, 3)])
    assert get(kick_key(session_id(2))) == b"stream_limit"


def test_evictions_report_and_drop_the_record() -> None:
    record(1, 1, bytes=4096, seen=NOW + 5)
    take(1, 1, now=NOW)
    result = take(2, 2, now=NOW + 10, policy="kick_oldest")
    eviction = result.evicted[0]
    assert (eviction.last_seen, eviction.bytes_sent) == (NOW + 5, 4096)
    assert not exists(sess_key(session_id(1)))
    assert score(concurrency.INDEX_KEY, session_id(1)) is None


def test_a_device_plays_one_stream_at_a_time() -> None:
    take(1, 1)
    result = take(2, 1, now=NOW + 5)  # same device, another title, max_streams 1, reject
    assert result.status is SlotStatus.ADDED
    assert [(e.session, e.reason) for e in result.evicted] == [(session_id(1), KickReason.REPLACED)]
    assert held() == [member(2, 1)]
    assert get(kick_key(session_id(1))) == b"replaced"


def test_rejection_changes_nothing() -> None:
    take(1, 1)
    take(2, 2, max_streams=2)
    # Over the (lowered) limit: device 1 starting a new title is refused, and its
    # current session is left alone.
    assert take(3, 1, now=NOW + 1).status is SlotStatus.REJECTED
    assert sorted(held()) == sorted([member(1, 1), member(2, 2)])
    assert not exists(kick_key(session_id(1)))


def test_a_new_start_clears_an_old_kick() -> None:
    state_redis().set(kick_key(session_id(1)), "kicked", ex=60)
    assert take(1, 1).status is SlotStatus.ADDED
    assert not exists(kick_key(session_id(1)))


def test_no_slot_without_a_limit() -> None:
    assert take(1, 1, max_streams=0).status is SlotStatus.REJECTED
    assert held() == []


def test_active_streams_counts_slots_active_within_the_window() -> None:
    assert concurrency.active_streams(USER, now=NOW) == 0
    take(1, 1, max_streams=3)
    take(2, 2, now=NOW + 30, max_streams=3)
    assert concurrency.active_streams(USER, now=NOW + 30) == 2
    # The window is inclusive, as in acquire: at +90 s the first slot still counts.
    assert concurrency.active_streams(USER, now=NOW + 90) == 2
    assert concurrency.active_streams(USER, now=NOW + 90.5) == 1
    assert concurrency.active_streams(USER, now=NOW + 121) == 0
    assert concurrency.active_streams(str(uuid.UUID(int=77)), now=NOW) == 0
    # Read-only: a lapsed member stays until a start or heartbeat prunes it.
    assert len(held()) == 2


def test_release_gives_the_slot_back() -> None:
    take(1, 1)
    concurrency.release(USER, session_id(1), device_id(1))
    assert held() == []


# --- heartbeat ------------------------------------------------------------------------------


def test_heartbeat_refreshes_slot_and_record() -> None:
    record(1, 1)
    take(1, 1)
    result = beat(1, now=NOW + 40, ip="203.0.113.9", edge="edge-1", bytes_sent=1000)
    assert beat(1, now=NOW + 41, bytes_sent=2**52).allowed
    assert result == HeartbeatResult(allowed=True, reason="refreshed")
    stored = records.read_record(session_id(1))
    assert stored is not None
    assert (stored.seen, stored.ip, stored.edge, stored.bytes) == (
        NOW + 41,
        "203.0.113.9",
        "edge-1",
        1000 + 2**52,
    )
    assert score(conc_key(USER), member(1, 1)) == NOW + 41
    assert score(concurrency.INDEX_KEY, session_id(1)) == NOW + 41


def test_heartbeat_refuses_kicked_and_ended_sessions() -> None:
    record(1, 1)
    take(1, 1)
    state_redis().set(kick_key(session_id(1)), "kicked", ex=60)
    assert beat(1) == HeartbeatResult(allowed=False, reason="kicked")
    assert beat(2) == HeartbeatResult(allowed=False, reason="session_ended")


def test_heartbeat_takes_a_lapsed_slot_back_when_there_is_room() -> None:
    record(1, 1)
    take(1, 1)
    assert beat(1, now=NOW + 600) == HeartbeatResult(allowed=True, reason="readded")
    assert held() == [member(1, 1)]


def test_heartbeat_never_evicts() -> None:
    record(1, 1)
    take(1, 1)
    take(2, 2, now=NOW + 300)  # session 1's slot lapsed, device 2 took it
    assert beat(1, now=NOW + 310) == HeartbeatResult(allowed=False, reason="stream_limit")
    assert held() == [member(2, 2)]


def test_heartbeat_of_a_replaced_session_is_refused() -> None:
    record(1, 1)
    take(1, 1)
    take(2, 1, now=NOW + 300)  # the slot of session 1 lapsed; the device moved on
    assert beat(1, now=NOW + 310) == HeartbeatResult(allowed=False, reason="replaced")


def test_heartbeat_follows_the_entitlement() -> None:
    record(1, 1, max_streams=1)
    take(1, 1)
    entitlement = {"status": "suspended", "max_streams": 1}
    state_redis().set(f"ent:{USER}", json.dumps(entitlement))
    assert beat(1) == HeartbeatResult(allowed=False, reason="access_ended")
    # A raised limit lets a lapsed session back in next to another stream.
    take(2, 2, now=NOW + 300, max_streams=2)
    state_redis().set(f"ent:{USER}", json.dumps({"status": "active", "max_streams": 2}))
    assert beat(1, now=NOW + 310).allowed


# --- finish and reap -------------------------------------------------------------------------


def test_finish_stops_a_session() -> None:
    record(1, 1, seen=NOW + 7, bytes=99)
    take(1, 1)
    finished = concurrency.finish(session_id(1), KickReason.KICKED)
    assert (finished.last_seen, finished.bytes_sent) == (NOW + 7, 99)
    assert held() == []
    assert get(kick_key(session_id(1))) == b"kicked"
    assert 0 < ttl(kick_key(session_id(1))) <= 3600
    assert records.read_record(session_id(1)) is None
    assert concurrency.finish(session_id(1), KickReason.KICKED).last_seen is None


def test_reap_closes_only_idle_sessions() -> None:
    record(1, 1, seen=NOW, idle=120)
    record(2, 2, seen=NOW, idle=7200)
    take(1, 1)
    assert concurrency.reap(session_id(1), NOW + 100) is None
    reaped = concurrency.reap(session_id(1), NOW + 121)
    assert reaped is not None
    assert (reaped.row, reaped.last_seen) == (str(uuid.UUID(int=5001)), NOW)
    assert held() == []
    assert records.read_record(session_id(1)) is None
    assert concurrency.reap(session_id(2), NOW + 3600) is None
    assert concurrency.reap(session_id(3), NOW) is None  # no record: dropped from the index


# --- contention ------------------------------------------------------------------------------


def race(attempts: int, start: Callable[[int], SlotResult]) -> tuple[list[SlotResult], int]:
    """Run `attempts` starts at once; return results and the most slots ever seen held."""
    barrier = threading.Barrier(attempts + 1)
    results: list[SlotResult] = []
    lock = threading.Lock()
    done = threading.Event()
    peak = 0

    def worker(number: int) -> None:
        barrier.wait()
        result = start(number)
        with lock:
            results.append(result)

    def monitor() -> None:
        nonlocal peak
        while not done.is_set():
            peak = max(peak, cast("int", state_redis().zcard(conc_key(USER))))

    threads = [threading.Thread(target=worker, args=(number,)) for number in range(attempts)]
    watcher = threading.Thread(target=monitor)
    watcher.start()
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=30)
    done.set()
    watcher.join(timeout=30)
    return results, peak


@pytest.mark.parametrize("attempts", [64])
def test_racing_starts_never_over_allocate(attempts: int) -> None:
    results, peak = race(attempts, lambda n: take(n, n, now=time.time(), max_streams=3))
    added = [result for result in results if result.status is SlotStatus.ADDED]
    assert len(results) == attempts
    assert len(added) == 3
    assert len(held()) == 3
    assert peak <= 3


def test_racing_starts_under_kick_oldest_keep_the_limit() -> None:
    results, peak = race(
        48, lambda n: take(n, n, now=time.time(), max_streams=2, policy="kick_oldest")
    )
    assert all(result.status is SlotStatus.ADDED for result in results)
    assert sum(len(result.evicted) for result in results) == 48 - 2
    assert len(held()) == 2
    assert peak <= 2
