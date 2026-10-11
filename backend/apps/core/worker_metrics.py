"""Prometheus metrics of the Celery workers (SPEC §14, ADR-0018).

Counters such as scanned files, metadata API calls, notifications and kicks are
observed inside Celery's pool processes. When CELERY_METRICS_PORT is set (the worker
and transcoder services, docker/compose.yml), `config.celery.prepare_worker_metrics`
gives a `celery worker` process a fresh PROMETHEUS_MULTIPROC_DIR before anything
imports prometheus_client; every pool process writes its samples there, and the
worker's main process serves their sum on that port, reachable only on the Docker
backend network.

Task outcomes come from Celery's own signals (no task events on the broker):
`iptv_celery_tasks_total{task,state}` and `iptv_celery_task_duration_seconds{task}`.
Task names are labels only for our own `apps.*` tasks; anything else counts as
"other", so the label set stays bounded.

config/celery.py connects these handlers; they are no-ops outside a worker.
"""

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Final

from prometheus_client import REGISTRY, CollectorRegistry, multiprocess, start_http_server

from apps.core.metrics import CELERY_TASK_DURATION, CELERY_TASKS

logger = logging.getLogger(__name__)

TASK_PREFIX: Final = "apps."
OTHER_TASK: Final = "other"
# Celery's final states (celery.states) as label values.
_STATES: Final = {
    "SUCCESS": "succeeded",
    "FAILURE": "failed",
    "RETRY": "retried",
}

_started: dict[str, float] = {}
_server_lock = threading.Lock()
_server_started = False


def task_label(name: str | None) -> str:
    """Our task names as they are; anything else (or nothing) as "other"."""
    if name and name.startswith(TASK_PREFIX) and len(name) <= 128:
        return name
    return OTHER_TASK


def state_label(state: str | None) -> str:
    return _STATES.get(state or "", "other")


def on_task_prerun(task_id: str | None = None, **_kwargs: Any) -> None:
    if task_id:
        _started[task_id] = time.perf_counter()


def on_task_postrun(
    task_id: str | None = None,
    task: Any = None,
    state: str | None = None,
    **_kwargs: Any,
) -> None:
    name = task_label(getattr(task, "name", None))
    CELERY_TASKS.labels(name, state_label(state)).inc()
    start = _started.pop(task_id, None) if task_id else None
    if start is not None:
        CELERY_TASK_DURATION.labels(name).observe(time.perf_counter() - start)


def multiproc_dir() -> Path | None:
    value = os.environ.get("PROMETHEUS_MULTIPROC_DIR", "")
    return Path(value) if value else None


def registry() -> CollectorRegistry:
    """The pool processes' samples when in multiprocess mode, else this process's."""
    if multiproc_dir() is None:
        return REGISTRY
    collected = CollectorRegistry(auto_describe=False)
    multiprocess.MultiProcessCollector(collected)
    return collected


def on_worker_ready(**_kwargs: Any) -> None:
    """Serve the metrics from the main process once the worker is up (once per process)."""
    global _server_started  # noqa: PLW0603 - one HTTP server per worker process
    configured = os.environ.get("CELERY_METRICS_PORT", "")
    if not configured:
        return
    with _server_lock:
        if _server_started:
            return
        if multiproc_dir() is None:
            logger.warning(
                "worker metrics: PROMETHEUS_MULTIPROC_DIR is not set; "
                "only the main process's samples are served"
            )
        port = int(configured)
        try:
            start_http_server(port, addr="0.0.0.0", registry=registry())  # noqa: S104 - backend network only
        except OSError:
            logger.warning("worker metrics: port %s is not available", port, exc_info=True)
            return
        _server_started = True
        logger.info("worker metrics: serving on port %s", port)


def on_worker_process_shutdown(pid: int | None = None, **_kwargs: Any) -> None:
    """A pool process exits: drop its live gauges (counters are kept)."""
    if multiproc_dir() is None:
        return
    multiprocess.mark_process_dead(pid or os.getpid())
