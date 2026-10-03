"""The Xtream catalog response cache in redis-cache (SPEC §7.5).

Catalog JSON is built once per entitlement scope and locale and stored as orjson
bytes under `xc:{plan_hash}:{locale}:{action}[:{extra}]`. `plan_hash` digests the
scope (allowed categories and content types) together with the catalog version
in `xc:version`, so:

- users with the same access share entries, and an access change moves a user to
  another hash by itself;
- `invalidate()` replaces the version, which retires every entry at once. Old
  entries are never read again and expire with their TTL (or LRU eviction).

Call `invalidate_on_commit()` from every writer that changes what apps see:
titles, episodes, images, categories and their order or names. It runs after the
transaction commits, so no reader can cache pre-commit data under the new version.

redis-cache holds recomputable data only: when it is down, responses are built
from the catalog source directly.
"""

import hashlib
import logging
from collections.abc import Callable
from typing import cast

import orjson
import redis
from django.db import transaction

from apps.core.ids import uuid7
from apps.core.stores import cache_redis
from apps.xtream_api.dto import Locale
from apps.xtream_api.source import CatalogScope

logger = logging.getLogger(__name__)

PREFIX = "xc"
VERSION_KEY = f"{PREFIX}:version"
#: Bounds memory and the life of an entry a writer forgot to invalidate.
TTL_S = 3600


def _version(client: redis.Redis) -> str:
    raw = cast("bytes | None", client.get(VERSION_KEY))
    if raw is None:
        # First use, or the key was evicted (allkeys-lru): start a new version.
        client.set(VERSION_KEY, uuid7().hex, nx=True)
        raw = cast("bytes | None", client.get(VERSION_KEY))
    return raw.decode() if raw is not None else ""


def plan_hash(scope: CatalogScope, version: str) -> str:
    return hashlib.sha256(f"{version}|{scope.fingerprint()}".encode()).hexdigest()[:16]


def key(scope: CatalogScope, locale: Locale, action: str, extra: str = "", *, version: str) -> str:
    base = f"{PREFIX}:{plan_hash(scope, version)}:{locale}:{action}"
    return f"{base}:{extra}" if extra else base


def cached(
    scope: CatalogScope,
    locale: Locale,
    action: str,
    build: Callable[[], object | None],
    extra: str = "",
) -> bytes | None:
    """The cached orjson body, else `build()` serialised and stored.

    `build` returns None for "not found"; that is passed through and not cached.
    """
    client = cache_redis()
    entry = ""
    try:
        entry = key(scope, locale, action, extra, version=_version(client))
        hit = cast("bytes | None", client.get(entry))
    except redis.RedisError:
        logger.warning("xtream catalog cache unavailable; building without it")
        hit = None
    if hit is not None:
        return hit
    payload = build()
    if payload is None:
        return None
    body = orjson.dumps(payload)
    if entry:
        try:
            client.set(entry, body, ex=TTL_S)
        except redis.RedisError:
            logger.warning("xtream catalog cache unavailable; response not stored")
    return body


def invalidate() -> None:
    """Retire every cached catalog response now. Robust: a cache outage is logged."""
    try:
        cache_redis().set(VERSION_KEY, uuid7().hex)
    except redis.RedisError:
        logger.warning("xtream catalog cache unavailable; entries expire with their TTL")


def invalidate_on_commit() -> None:
    """Invalidate once the current transaction commits (at once outside one)."""
    transaction.on_commit(invalidate, robust=True)
