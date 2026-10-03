"""Gunicorn settings for the production web container (SPEC §13).

Loaded by path (`gunicorn --config config/gunicorn_conf.py`), so importing it
does not import the `config` package or Celery into the master process.

Workers: WEB_CONCURRENCY (2*CPU+1 in prod). Access logs stay off: they print
raw paths, and Xtream paths carry credentials; RequestLogMiddleware logs every
request redacted instead (ADR-0005).
"""

import os
import shutil
from pathlib import Path
from typing import Any

bind = "0.0.0.0:8000"
worker_class = "uvicorn_worker.UvicornWorker"
# No control socket: the container is managed by restarts, and /app is read-only.
control_socket_disable = True
accesslog = None

# prometheus_client multiprocess mode: every worker writes its samples here and
# /metrics aggregates them. Private to this container; wiped at each start.
PROMETHEUS_MULTIPROC_DIR = "/tmp/prometheus-multiproc"  # noqa: S108


def on_starting(server: Any) -> None:
    """Before workers fork: give them a fresh, empty metrics directory."""
    path = Path(os.environ.setdefault("PROMETHEUS_MULTIPROC_DIR", PROMETHEUS_MULTIPROC_DIR))
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(mode=0o700, parents=True)


def child_exit(server: Any, worker: Any) -> None:
    """Drop a dead worker's live gauges so they stop being reported."""
    from prometheus_client import multiprocess  # noqa: PLC0415

    multiprocess.mark_process_dead(worker.pid)
