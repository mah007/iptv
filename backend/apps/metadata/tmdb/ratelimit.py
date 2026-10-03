"""A token bucket in Redis shared by every worker (SPEC §7.2 step 8: 35 requests/s for TMDB).

The bucket is one Redis hash updated by a Lua script, so concurrent workers
never over-spend it, and the time comes from the Redis server, so worker
clocks do not matter. A request that finds the bucket empty reserves the next
token and is told how long to wait for it, which keeps waiting workers in
first-come order without polling. A reservation that would wait longer than
`max_wait_s` is refused instead, raising `RateLimitTimeoutError`.

If Redis is unreachable the limiter lets the request through and logs a
warning: TMDB's own 429 answers (retried with their Retry-After) are the
backstop, and metadata work should not stop because a cache store blinked.
"""

import logging
import time
from collections.abc import Callable
from typing import Final, Protocol, cast

import redis

from .errors import RateLimitTimeoutError

__all__ = ["DEFAULT_RATE_PER_S", "RateLimiter", "RedisTokenBucket"]

logger = logging.getLogger(__name__)

DEFAULT_RATE_PER_S: Final = 35.0

# KEYS[1]  the bucket: a hash of `tokens` (may go negative: reserved) and `ts` (microseconds)
# ARGV[1]  refill rate in tokens per second
# ARGV[2]  capacity (the largest burst)
# ARGV[3]  tokens wanted
# ARGV[4]  the longest wait the caller accepts, in microseconds
# Returns {granted (1 or 0), wait in microseconds}. Nothing is reserved when not granted.
_BUCKET_LUA: Final = """
local rate = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])
local wanted = tonumber(ARGV[3])
local max_wait = tonumber(ARGV[4])
local clock = redis.call('TIME')
local now = tonumber(clock[1]) * 1000000 + tonumber(clock[2])
local state = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(state[1])
local ts = tonumber(state[2])
if tokens == nil or ts == nil then
  tokens = capacity
  ts = now
end
if now > ts then
  tokens = math.min(capacity, tokens + (now - ts) * rate / 1000000)
  ts = now
end
local left = tokens - wanted
local wait = 0
if left < 0 then
  wait = math.ceil(-left * 1000000 / rate)
end
if wait > max_wait then
  return {0, wait}
end
redis.call('HSET', KEYS[1], 'tokens', left, 'ts', ts)
redis.call('PEXPIRE', KEYS[1], math.ceil((capacity - left) * 1000 / rate) + 1000)
return {1, wait}
"""


class RateLimiter(Protocol):
    def acquire(self) -> None:
        """Block until one more request may be sent."""


class RedisTokenBucket:
    """`rate_per_s` requests per second, in bursts of up to `capacity` (default: one second's)."""

    def __init__(  # noqa: PLR0913
        self,
        client: redis.Redis,
        *,
        key: str = "ratelimit:tmdb",
        rate_per_s: float = DEFAULT_RATE_PER_S,
        capacity: float | None = None,
        max_wait_s: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate_per_s <= 0:
            msg = "rate_per_s must be positive"
            raise ValueError(msg)
        self._client = client
        self._key = key
        self._rate = rate_per_s
        self._capacity = capacity if capacity is not None else rate_per_s
        if self._capacity < 1:
            msg = "capacity must allow at least one request"
            raise ValueError(msg)
        self._max_wait_us = int(max_wait_s * 1_000_000)
        self._sleep = sleep
        self._script = client.register_script(_BUCKET_LUA)

    def reserve(self, tokens: int = 1) -> float:
        """Take `tokens` now or reserve them; return the seconds to wait before using them.

        Raises `RateLimitTimeoutError` (reserving nothing) if the wait would exceed
        `max_wait_s`.
        """
        result = self._script(
            keys=[self._key], args=[self._rate, self._capacity, tokens, self._max_wait_us]
        )
        granted, wait_us = (int(value) for value in cast("list[int]", result))
        if not granted:
            msg = f"TMDB rate limiter: next slot in {wait_us / 1_000_000:.1f}s"
            raise RateLimitTimeoutError(msg)
        return wait_us / 1_000_000

    def acquire(self) -> None:
        try:
            wait = self.reserve()
        except redis.RedisError as exc:
            logger.warning("TMDB rate limiter unavailable (%s); not limiting", type(exc).__name__)
            return
        if wait > 0:
            self._sleep(wait)
