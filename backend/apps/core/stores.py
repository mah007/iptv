"""Shared clients for the two Redis-compatible stores (SPEC §3)."""

from functools import cache

import redis
from django.conf import settings

_TIMEOUTS = {"socket_timeout": 2, "socket_connect_timeout": 2, "health_check_interval": 30}


@cache
def state_redis() -> redis.Redis:
    """redis-state (noeviction, AOF): sessions, slots, entitlements, kicks, broker."""
    return redis.Redis.from_url(settings.REDIS_STATE_URL, **_TIMEOUTS)


@cache
def cache_redis() -> redis.Redis:
    """redis-cache (allkeys-lru): recomputable data only."""
    return redis.Redis.from_url(settings.REDIS_CACHE_URL, **_TIMEOUTS)
