"""The shared TMDB token bucket (Lua) and the response cache, against a real Redis/Valkey."""

import threading
import time
from typing import cast

import httpx
import pytest
import redis

from apps.metadata.tmdb import (
    RateLimitTimeoutError,
    RedisResponseCache,
    RedisTokenBucket,
    TMDBClient,
)


def dead_redis() -> redis.Redis:
    """A client for a port nothing listens on."""
    return redis.Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.2)


def test_bursts_to_capacity_then_hands_out_one_slot_per_interval(
    redis_client: redis.Redis, redis_key: str
) -> None:
    bucket = RedisTokenBucket(redis_client, key=redis_key, rate_per_s=10, capacity=3)
    assert [bucket.reserve() for _ in range(3)] == [0.0, 0.0, 0.0]
    waits = [bucket.reserve() for _ in range(3)]
    # Reserved slots queue up 0.1 s apart (a few ms of refill may pass between calls).
    assert waits == pytest.approx([0.1, 0.2, 0.3], abs=0.03)


def test_a_wait_beyond_the_limit_is_refused_and_reserves_nothing(
    redis_client: redis.Redis, redis_key: str
) -> None:
    impatient = RedisTokenBucket(
        redis_client, key=redis_key, rate_per_s=10, capacity=1, max_wait_s=0.15
    )
    assert impatient.reserve() == 0.0
    assert impatient.reserve() == pytest.approx(0.1, abs=0.03)
    with pytest.raises(RateLimitTimeoutError):
        impatient.reserve()  # would wait about 0.2 s
    patient = RedisTokenBucket(
        redis_client, key=redis_key, rate_per_s=10, capacity=1, max_wait_s=0.25
    )
    # Had the refused call reserved its slot, this one would wait about 0.3 s and fail.
    assert patient.reserve() == pytest.approx(0.2, abs=0.03)


def test_the_bucket_refills_with_time(redis_client: redis.Redis, redis_key: str) -> None:
    bucket = RedisTokenBucket(redis_client, key=redis_key, rate_per_s=50, capacity=2)
    assert [bucket.reserve(), bucket.reserve()] == [0.0, 0.0]
    assert bucket.reserve() > 0
    time.sleep(0.15)  # 7.5 tokens' worth, capped at the capacity of 2
    assert [bucket.reserve(), bucket.reserve()] == [0.0, 0.0]
    assert 0 < cast("int", redis_client.pttl(redis_key)) <= 2000


def test_concurrent_workers_share_one_budget(redis_client: redis.Redis, redis_key: str) -> None:
    rate, capacity, workers, each = 40.0, 4.0, 8, 5
    stamps: list[float] = []
    lock = threading.Lock()

    def worker() -> None:
        bucket = RedisTokenBucket(redis_client, key=redis_key, rate_per_s=rate, capacity=capacity)
        for _ in range(each):
            bucket.acquire()
            with lock:
                stamps.append(time.monotonic())

    started = time.monotonic()
    threads = [threading.Thread(target=worker) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    elapsed = time.monotonic() - started
    total = workers * each
    assert len(stamps) == total
    # 4 requests may go at once; the other 36 need 0.9 s at 40/s.
    assert elapsed >= (total - capacity) / rate - 0.05
    assert elapsed < 3.0
    # No second-long window ever saw more than rate + capacity requests.
    stamps.sort()
    assert max(sum(1 for t in stamps if s <= t < s + 1.0) for s in stamps) <= rate + capacity


def test_limiter_lets_requests_through_when_redis_is_down() -> None:
    sleeps: list[float] = []
    bucket = RedisTokenBucket(dead_redis(), sleep=sleeps.append)
    bucket.acquire()  # logs a warning, does not raise
    assert sleeps == []


@pytest.mark.parametrize(("rate", "capacity"), [(0.0, None), (-1.0, None), (10.0, 0.5)])
def test_limiter_rejects_impossible_settings(rate: float, capacity: float | None) -> None:
    with pytest.raises(ValueError, match="must"):
        RedisTokenBucket(dead_redis(), rate_per_s=rate, capacity=capacity)


def test_redis_response_cache_round_trip(redis_client: redis.Redis, redis_key: str) -> None:
    cache = RedisResponseCache(redis_client, prefix=f"{redis_key}:")
    assert cache.get("k") is None
    cache.set("k", b'{"id": 603}', ttl_s=60)
    assert cache.get("k") == b'{"id": 603}'
    assert 55 <= cast("int", redis_client.ttl(f"{redis_key}:k")) <= 60


def test_redis_response_cache_failures_are_not_fatal() -> None:
    cache = RedisResponseCache(dead_redis())
    cache.set("k", b"v", ttl_s=60)
    assert cache.get("k") is None


def test_client_with_the_redis_limiter_and_cache(redis_client: redis.Redis, redis_key: str) -> None:
    calls: list[httpx.Request] = []

    def api(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"id": 603, "title": "The Matrix"})

    limiter = RedisTokenBucket(redis_client, key=f"{redis_key}:bucket", rate_per_s=35)
    cache = RedisResponseCache(redis_client, prefix=f"{redis_key}:response:")

    def build(api_key: str) -> TMDBClient:
        return TMDBClient(
            api_key=api_key, rate_limiter=limiter, cache=cache, transport=httpx.MockTransport(api)
        )

    with build("first-key") as client:
        assert client.movie_details(603)["title"] == "The Matrix"
    with build("second-key") as client:
        assert client.movie_details(603)["title"] == "The Matrix"
    assert len(calls) == 1
    for key in redis_client.scan_iter(match=f"{redis_key}:response:*"):
        assert b"first-key" not in key
