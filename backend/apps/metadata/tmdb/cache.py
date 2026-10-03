"""Response caches for the TMDB client (SPEC §7.2 step 8: 24 h in redis-cache, keyed by URL).

The key is a SHA-256 of the API path and the sorted query parameters, taken
before the client adds credentials, so the API key never reaches the cache.
Caching is best effort: a cache that fails is logged and skipped, never fatal.
"""

import hashlib
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from typing import Final, Protocol, cast
from urllib.parse import urlencode

import redis

__all__ = [
    "InMemoryResponseCache",
    "RedisResponseCache",
    "ResponseCache",
    "cache_key",
]

logger = logging.getLogger(__name__)

#: Query parameters that are credentials and never part of a cache key.
_SECRET_PARAMS: Final = frozenset({"api_key"})


class ResponseCache(Protocol):
    """Raw response bodies by `cache_key`."""

    def get(self, key: str) -> bytes | None: ...

    def set(self, key: str, value: bytes, ttl_s: int) -> None: ...


def cache_key(path: str, params: Mapping[str, str]) -> str:
    """Stable key of a GET request: the path plus sorted parameters, credentials removed."""
    query = urlencode(sorted((k, v) for k, v in params.items() if k not in _SECRET_PARAMS))
    return hashlib.sha256(f"{path}?{query}".encode()).hexdigest()


class RedisResponseCache:
    """Cache in Redis (redis-cache in production, an LRU store)."""

    def __init__(self, client: redis.Redis, *, prefix: str = "tmdb:response:") -> None:
        self._client = client
        self._prefix = prefix

    def get(self, key: str) -> bytes | None:
        try:
            return cast("bytes | None", self._client.get(self._prefix + key))
        except redis.RedisError as exc:
            logger.warning("TMDB response cache read failed: %s", type(exc).__name__)
            return None

    def set(self, key: str, value: bytes, ttl_s: int) -> None:
        try:
            self._client.set(self._prefix + key, value, ex=ttl_s)
        except redis.RedisError as exc:
            logger.warning("TMDB response cache write failed: %s", type(exc).__name__)


class InMemoryResponseCache:
    """A bounded, thread-safe in-process cache, for tests and one-off scripts."""

    def __init__(
        self, *, max_entries: int = 1024, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._entries: OrderedDict[str, tuple[float, bytes]] = OrderedDict()
        self._max_entries = max_entries
        self._clock = clock
        self._lock = threading.Lock()

    def get(self, key: str) -> bytes | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at <= self._clock():
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return value

    def set(self, key: str, value: bytes, ttl_s: int) -> None:
        with self._lock:
            self._entries[key] = (self._clock() + ttl_s, value)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def __len__(self) -> int:
        return len(self._entries)
