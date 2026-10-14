"""Per-account rate limits on expensive endpoints (SPEC §11 "Django-level per-user limits";
G-20, ADR-0021).

Traefik limits requests per client address at the edge of the stack (the Xtream host,
sign-in on every host). These limits sit behind it and count per *account* (the signed-in
user; the client address for anonymous requests), so a customer or admin hammering a
costly endpoint is slowed whatever address they come from, and many customers behind one
carrier-grade NAT don't share a budget.

Each limit is a registry setting (requests per minute or per hour), counted in a fixed
window in redis-cache: the counters are recomputable, and if redis-cache is down nothing is
limited. A refusal is 429 `RATE_LIMITED` with Retry-After (DRF's Throttled).
"""

import time
from typing import ClassVar, cast

import redis
from rest_framework.request import Request
from rest_framework.throttling import BaseThrottle
from rest_framework.views import APIView

from apps.core.http import client_ip
from apps.core.services import get_setting
from apps.core.stores import cache_redis


class SettingRateThrottle(BaseThrottle):
    """A fixed-window limit whose size is the registry setting `setting`.

    Subclasses set `scope`, `setting`, `period_s` and optionally `methods` (only those
    HTTP methods count; empty: all)."""

    scope: ClassVar[str] = ""
    setting: ClassVar[str] = ""
    period_s: ClassVar[int] = 60
    methods: ClassVar[frozenset[str]] = frozenset()

    def __init__(self) -> None:
        self._wait: float | None = None

    def ident(self, request: Request) -> str:
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False):
            return f"u{user.pk}"
        return f"ip{client_ip(request) or 'unknown'}"

    def allow_request(self, request: Request, view: APIView) -> bool:
        if self.methods and (request.method or "").upper() not in self.methods:
            return True
        limit = int(cast("int", get_setting(self.setting)))
        window = int(time.time() // self.period_s)
        key = f"rl:{self.scope}:{self.ident(request)}:{window}"
        try:
            pipe = cache_redis().pipeline()
            pipe.incr(key)
            pipe.expire(key, self.period_s + 5)
            count, _ = pipe.execute()
        except redis.RedisError:
            return True
        if int(count) <= limit:
            return True
        self._wait = self.period_s - (time.time() % self.period_s)
        return False

    def wait(self) -> float | None:
        return self._wait


class SearchThrottle(SettingRateThrottle):
    scope = "search"
    setting = "security.rate_search_per_minute"


class PlaybackStartThrottle(SettingRateThrottle):
    scope = "playback_start"
    setting = "security.rate_playback_start_per_minute"
    methods = frozenset({"POST"})


class DeviceCredentialThrottle(SettingRateThrottle):
    scope = "device_credentials"
    setting = "security.rate_device_credentials_per_hour"
    period_s = 3600
    methods = frozenset({"POST"})


class CheckoutThrottle(SettingRateThrottle):
    scope = "checkout"
    setting = "security.rate_checkout_per_hour"
    period_s = 3600
    methods = frozenset({"POST"})


class AdminTmdbThrottle(SettingRateThrottle):
    scope = "admin_tmdb"
    setting = "security.rate_admin_tmdb_search_per_minute"


class BackupRunThrottle(SettingRateThrottle):
    scope = "backup_runs"
    setting = "security.rate_backup_runs_per_hour"
    period_s = 3600
    methods = frozenset({"POST"})
