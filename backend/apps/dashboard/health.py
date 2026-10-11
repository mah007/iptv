"""System health for the admin (SPEC §8.3 System Health, §10 `admin/health`).

Everything here is read from inside the backend network, on demand:

- the stores, through the readiness checks of `apps.core.health`;
- the background services, through the heartbeats they keep in redis-state: beat's
  heartbeat task runs on a worker (`hb:beat`), the library watcher beats on its own
  (`hb:watcher`), and every transcoder registers its encoders (`worker:caps:<host>`);
- the media edges, through their `/healthz` (settings.EDGE_HEALTH_URLS);
- Celery queue depths from the broker, Redis memory and evictions (evictions on
  redis-state must stay 0), and Postgres connections.

Probes have short timeouts and never raise: an unreachable part is reported as down.
Error details are exception class names only (messages can hold hosts or secrets).
"""

import logging
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, cast
from urllib.parse import urlsplit

import redis
from django.conf import settings
from django.db import connection
from django.utils import timezone

from apps.core.health import run_checks
from apps.core.stores import cache_redis, state_redis
from apps.core.tasks import HEARTBEAT_KEY
from apps.library.watcher import HEARTBEAT_KEY as WATCHER_HEARTBEAT_KEY
from apps.media import services as media

logger = logging.getLogger(__name__)

PROBE_TIMEOUT_S = 2.0
#: Beat schedules its heartbeat every 30 s and the watcher beats every 30 s.
HEARTBEAT_OK_S = 90
HEARTBEAT_LATE_S = 300
#: Connections in use, as a share of max_connections, from which Postgres is degraded.
DB_CONNECTIONS_DEGRADED = 0.9
#: Kombu's Redis transport keeps one list per priority step: `<queue>\x06\x16<step>`.
_PRIORITY_SEPARATOR = "\x06\x16"
_PRIORITY_STEPS = (3, 6, 9)


class Status(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"


@dataclass(frozen=True, slots=True)
class ServiceHealth:
    name: str
    status: Status
    latency_ms: float | None = None
    #: Seconds since the last heartbeat, for heartbeat-based services.
    age_s: int | None = None
    #: An exception class name or a short machine-readable reason.
    error: str = ""


@dataclass(frozen=True, slots=True)
class QueueDepth:
    name: str
    depth: int | None


@dataclass(frozen=True, slots=True)
class Transcoder:
    host: str
    best: str
    queues: list[str]
    backends: dict[str, list[str]]
    gpus: list[str]
    ffmpeg: str
    checked_at: datetime | None


@dataclass(frozen=True, slots=True)
class RedisHealth:
    name: str
    status: Status
    used_memory: int | None = None
    max_memory: int | None = None
    policy: str = ""
    evicted_keys: int | None = None
    error: str = ""


@dataclass(frozen=True, slots=True)
class DatabaseHealth:
    status: Status
    connections: int | None = None
    max_connections: int | None = None
    error: str = ""


@dataclass(frozen=True, slots=True)
class Health:
    as_of: datetime
    status: Status
    services: list[ServiceHealth] = field(default_factory=list)
    queues: list[QueueDepth] = field(default_factory=list)
    transcoders: list[Transcoder] = field(default_factory=list)
    redis: list[RedisHealth] = field(default_factory=list)
    database: DatabaseHealth | None = None
    grafana_url: str = ""


def _overall(statuses: list[Status]) -> Status:
    if Status.DOWN in statuses:
        return Status.DOWN
    if Status.DEGRADED in statuses:
        return Status.DEGRADED
    return Status.OK


def _heartbeat(name: str, key: str, now: float) -> ServiceHealth:
    try:
        raw = cast("bytes | None", state_redis().get(key))
    except redis.RedisError as exc:
        return ServiceHealth(name, Status.DOWN, error=type(exc).__name__)
    if raw is None:
        return ServiceHealth(name, Status.DOWN, error="no_heartbeat")
    try:
        age = max(0, int(now - int(raw)))
    except ValueError:
        return ServiceHealth(name, Status.DOWN, error="bad_heartbeat")
    if age <= HEARTBEAT_OK_S:
        return ServiceHealth(name, Status.OK, age_s=age)
    status = Status.DEGRADED if age <= HEARTBEAT_LATE_S else Status.DOWN
    return ServiceHealth(name, status, age_s=age, error="late_heartbeat")


def _probe_url(url: str) -> tuple[bool, float, str]:
    start = time.perf_counter()
    scheme = urlsplit(url).scheme
    if scheme not in {"http", "https"}:
        return False, 0.0, "bad_url"
    try:
        with urllib.request.urlopen(url, timeout=PROBE_TIMEOUT_S) as response:  # noqa: S310
            ok = 200 <= response.status < 300
    except Exception as exc:  # any failure means the edge is not serving
        return False, (time.perf_counter() - start) * 1000, type(exc).__name__
    return ok, (time.perf_counter() - start) * 1000, "" if ok else "bad_status"


def _edges() -> list[ServiceHealth]:
    edges = []
    urls: list[str] = list(getattr(settings, "EDGE_HEALTH_URLS", []))
    for index, url in enumerate(urls):
        host = urlsplit(url).hostname or f"edge-{index + 1}"
        ok, latency, error = _probe_url(url)
        edges.append(
            ServiceHealth(
                f"edge:{host}",
                Status.OK if ok else Status.DOWN,
                latency_ms=round(latency, 1),
                error=error,
            )
        )
    return edges


def _transcoders() -> tuple[list[Transcoder], ServiceHealth]:
    try:
        documents = media.live_capabilities()
    except redis.RedisError as exc:
        return [], ServiceHealth("transcoders", Status.DOWN, error=type(exc).__name__)
    transcoders = []
    for document in sorted(documents, key=lambda item: str(item.get("host", ""))):
        checked = document.get("checked_at")
        try:
            checked_at = datetime.fromisoformat(checked) if isinstance(checked, str) else None
        except ValueError:
            checked_at = None
        backends = document.get("backends")
        transcoders.append(
            Transcoder(
                host=str(document.get("host", "")),
                best=str(document.get("best", "")),
                queues=[str(name) for name in document.get("queues") or []],
                backends={
                    str(backend): [str(codec) for codec in codecs]
                    for backend, codecs in (backends.items() if isinstance(backends, dict) else [])
                    if isinstance(codecs, list)
                },
                gpus=[str(gpu) for gpu in document.get("gpus") or []],
                ffmpeg=str(document.get("ffmpeg") or ""),
                checked_at=checked_at,
            )
        )
    status = Status.OK if transcoders else Status.DOWN
    error = "" if transcoders else "no_transcoder"
    return transcoders, ServiceHealth("transcoders", status, error=error)


def queue_names() -> list[str]:
    return [queue.name for queue in settings.CELERY_TASK_QUEUES]


def _queues() -> list[QueueDepth]:
    names = queue_names()
    try:
        client = redis.Redis.from_url(
            settings.CELERY_BROKER_URL,
            socket_timeout=PROBE_TIMEOUT_S,
            socket_connect_timeout=PROBE_TIMEOUT_S,
        )
        with client.pipeline(transaction=False) as pipe:
            for name in names:
                pipe.llen(name)
                for step in _PRIORITY_STEPS:
                    pipe.llen(f"{name}{_PRIORITY_SEPARATOR}{step}")
            lengths = cast("list[int]", pipe.execute())
        client.close()
    except redis.RedisError:
        logger.warning("health: broker unavailable", exc_info=True)
        return [QueueDepth(name, None) for name in names]
    per_queue = len(_PRIORITY_STEPS) + 1
    return [
        QueueDepth(name, sum(lengths[index * per_queue : (index + 1) * per_queue]))
        for index, name in enumerate(names)
    ]


def queue_depths() -> list[QueueDepth]:
    """Messages waiting per Celery queue (None when the broker is unreachable)."""
    return _queues()


def _redis(name: str, client_of: Callable[[], redis.Redis], *, must_not_evict: bool) -> RedisHealth:
    try:
        client = client_of()
        memory = cast("dict[str, Any]", client.info("memory"))
        stats = cast("dict[str, Any]", client.info("stats"))
    except redis.RedisError as exc:
        return RedisHealth(name, Status.DOWN, error=type(exc).__name__)
    evicted = int(stats.get("evicted_keys", 0))
    status = Status.DOWN if must_not_evict and evicted > 0 else Status.OK
    return RedisHealth(
        name,
        status,
        used_memory=int(memory.get("used_memory", 0)),
        max_memory=int(memory.get("maxmemory", 0)) or None,
        policy=str(memory.get("maxmemory_policy", "")),
        evicted_keys=evicted,
        error="evictions" if status is Status.DOWN else "",
    )


def _database() -> DatabaseHealth:
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*), current_setting('max_connections')::int "
                "FROM pg_stat_activity WHERE datname = current_database()"
            )
            row = cursor.fetchone()
    except Exception as exc:  # the readiness check reports the outage itself
        return DatabaseHealth(Status.DOWN, error=type(exc).__name__)
    connections, max_connections = row if row is not None else (None, None)
    ratio = connections / max_connections if connections and max_connections else 0
    status = Status.DEGRADED if ratio >= DB_CONNECTIONS_DEGRADED else Status.OK
    return DatabaseHealth(status, connections=connections, max_connections=max_connections)


def collect() -> Health:
    now = time.time()
    services = [
        ServiceHealth(
            name,
            Status.OK if result.ok else Status.DOWN,
            latency_ms=round(result.latency_ms, 1),
            error=result.error,
        )
        for name, result in run_checks().items()
    ]
    services.append(_heartbeat("workers", HEARTBEAT_KEY, now))
    services.append(_heartbeat("watcher", WATCHER_HEARTBEAT_KEY, now))
    transcoders, transcoder_health = _transcoders()
    services.append(transcoder_health)
    services.extend(_edges())
    stores = [
        _redis("redis_state", state_redis, must_not_evict=True),
        _redis("redis_cache", cache_redis, must_not_evict=False),
    ]
    database = _database()
    statuses = [
        *(item.status for item in services),
        *(item.status for item in stores),
        database.status,
    ]
    return Health(
        as_of=timezone.now(),
        status=_overall(statuses),
        services=services,
        queues=_queues(),
        transcoders=transcoders,
        redis=stores,
        database=database,
        grafana_url=str(getattr(settings, "GRAFANA_URL", "")),
    )
