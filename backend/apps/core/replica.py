"""The PostgreSQL read replica (M15, ADR-0022): lag, and where reporting reads go.

A replica is optional (`docker/compose.replica.yml`); `DATABASES["replica"]` exists only
when `POSTGRES_REPLICA_HOST` is set. Reads are routed to it explicitly, never by a global
router: a global one would break read-your-writes (create a customer, then list them).
Code that only reads aggregates written seconds ago (dashboard charts, billing figures,
the storage report, the minute gauges) asks `reporting_db()` for its alias:

- "replica" when one is configured, `database.replica_reads` is on and its lag, measured
  every 15 s by beat into redis-cache, is within `database.replica_max_lag_s`;
- "default" otherwise, and for 30 s after a replica query failed (`replica_failed`).

The replica signs in as the app role (`iptv_app`): a hot standby refuses writes anyway,
and the role sees the same tables as on the primary.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Any, Final, cast

import redis
from django.conf import settings
from django.db import DatabaseError, connections

from apps.core.stores import cache_redis

logger = logging.getLogger(__name__)

REPLICA: Final = "replica"
DEFAULT: Final = "default"
LAG_KEY: Final = "db:replica:lag"
LAG_TTL_S: Final = 60
_MEMO_S: Final = 5.0
_BACKOFF_S: Final = 30.0
_LAG_SQL: Final = (
    "SELECT pg_is_in_recovery(), CASE "
    "WHEN NOT pg_is_in_recovery() THEN NULL "
    "WHEN pg_last_wal_receive_lsn() = pg_last_wal_replay_lsn() THEN 0 "
    "ELSE GREATEST(0, EXTRACT(EPOCH FROM now() - pg_last_xact_replay_timestamp())) END"
)

_memo: tuple[float, dict[str, Any] | None] | None = None
_failed_until = 0.0


def configured() -> bool:
    return REPLICA in settings.DATABASES


def measure() -> dict[str, Any] | None:
    """The replica's state, stored in redis-cache for `reporting_db()` (None: no replica)."""
    if not configured():
        return None
    state: dict[str, Any] = {"at": time.time(), "ok": False, "lag_s": None, "standby": False}
    try:
        with connections[REPLICA].cursor() as cursor:
            cursor.execute(_LAG_SQL)
            row = cursor.fetchone()
        standby, lag = bool(row[0]), row[1]
        state.update(ok=standby, standby=standby, lag_s=None if lag is None else float(lag))
    except DatabaseError as exc:
        state["error"] = type(exc).__name__
    finally:
        connections[REPLICA].close()
    try:
        cache_redis().set(LAG_KEY, json.dumps(state), ex=LAG_TTL_S)
    except redis.RedisError:
        logger.warning("replica: redis-cache unavailable; the lag is not stored")
    return state


def state() -> dict[str, Any] | None:
    """The last measurement (memoised for a few seconds), None when unknown."""
    global _memo  # noqa: PLW0603 (process-local memo)
    clock = time.monotonic()
    if _memo is not None and clock - _memo[0] < _MEMO_S:
        return _memo[1]
    found: dict[str, Any] | None = None
    try:
        raw = cast("bytes | None", cache_redis().get(LAG_KEY))
        found = json.loads(raw) if raw else None
    except (redis.RedisError, ValueError):
        found = None
    _memo = (clock, found)
    return found


def reporting_db() -> str:
    """The alias reporting reads use: the replica while it is usable, else the primary."""
    if not configured() or time.monotonic() < _failed_until:
        return DEFAULT
    from apps.core.services import get_setting  # noqa: PLC0415 (settings need the registry)

    if not get_setting("database.replica_reads"):
        return DEFAULT
    current = state()
    if not current or not current.get("ok") or current.get("lag_s") is None:
        return DEFAULT
    limit = float(cast("int", get_setting("database.replica_max_lag_s")))
    return REPLICA if float(current["lag_s"]) <= limit else DEFAULT


def replica_failed() -> None:
    """Keep reporting reads on the primary for a while after a replica error."""
    global _failed_until  # noqa: PLW0603
    _failed_until = time.monotonic() + _BACKOFF_S


def read[T](query: Callable[[str], T]) -> T:
    """Run `query(alias)` on the reporting alias, on the primary again if the replica fails."""
    alias = reporting_db()
    if alias == DEFAULT:
        return query(DEFAULT)
    try:
        return query(alias)
    except DatabaseError:
        logger.warning("replica: a reporting read failed; using the primary for 30 s")
        replica_failed()
        return query(DEFAULT)


def reset() -> None:
    """Forget the memo and the back-off (tests)."""
    global _memo, _failed_until  # noqa: PLW0603
    _memo = None
    _failed_until = 0.0


class ReplicaRouter:
    """Writes and migrations go to the primary only; reads go where code sends them."""

    def db_for_read(self, model: Any, **hints: Any) -> str | None:
        return None

    def db_for_write(self, model: Any, **hints: Any) -> str:
        return DEFAULT

    def allow_relation(self, obj1: Any, obj2: Any, **hints: Any) -> bool:
        return True

    def allow_migrate(self, db: str, app_label: str, **hints: Any) -> bool:
        return db == DEFAULT
