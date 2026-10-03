"""Readiness checks for every store the control plane depends on."""

import json
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

from django.conf import settings
from django.db import connection

from apps.core.stores import cache_redis, state_redis

TIMEOUT_S = 2.0


@dataclass(frozen=True, slots=True)
class CheckResult:
    ok: bool
    latency_ms: float
    # Exception class name only: messages can carry hosts or credentials.
    error: str = ""


def _timed(probe: Callable[[], None]) -> CheckResult:
    start = time.perf_counter()
    try:
        probe()
    except Exception as exc:
        return CheckResult(ok=False, latency_ms=_ms_since(start), error=type(exc).__name__)
    return CheckResult(ok=True, latency_ms=_ms_since(start))


def _ms_since(start: float) -> float:
    return (time.perf_counter() - start) * 1000


def _postgres() -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")


def _redis_state() -> None:
    state_redis().ping()


def _redis_cache() -> None:
    cache_redis().ping()


class MeilisearchUnavailableError(RuntimeError):
    pass


def _meilisearch() -> None:
    url = f"{settings.MEILI_URL.rstrip('/')}/health"
    if not url.startswith(("http://", "https://")):
        raise MeilisearchUnavailableError
    with urllib.request.urlopen(url, timeout=TIMEOUT_S) as resp:  # noqa: S310 (scheme checked)
        body = json.load(resp)
    if body.get("status") != "available":
        raise MeilisearchUnavailableError


CHECKS: dict[str, Callable[[], None]] = {
    "postgres": _postgres,
    "redis_state": _redis_state,
    "redis_cache": _redis_cache,
    "meilisearch": _meilisearch,
}


def run_checks() -> dict[str, CheckResult]:
    return {name: _timed(probe) for name, probe in CHECKS.items()}
