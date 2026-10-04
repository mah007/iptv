import os
import shutil
import sys
from collections.abc import MutableMapping, Sequence
from pathlib import Path
from typing import Any

from celery import Celery, signals

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

WORKER_METRICS_DIR = "/tmp/prometheus-worker"  # noqa: S108 - private to the container


def prepare_worker_metrics(
    argv: Sequence[str] = sys.argv, environ: MutableMapping[str, str] = os.environ
) -> Path | None:
    """Switch a Celery worker's main process to multiprocess metrics (ADR-0018).

    Only `celery ... worker` with CELERY_METRICS_PORT set: its pool processes write
    their samples to a fresh directory that the main process serves. Other commands in
    the same container (healthchecks, `inspect`, manage.py) keep in-memory metrics, so
    they never leave files behind. Runs before prometheus_client is imported, which
    reads PROMETHEUS_MULTIPROC_DIR once.
    """
    if not environ.get("CELERY_METRICS_PORT"):
        return None
    if not argv or Path(argv[0]).name != "celery" or "worker" not in argv[1:]:
        return None
    path = Path(environ.get("CELERY_METRICS_DIR") or WORKER_METRICS_DIR)
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    environ["PROMETHEUS_MULTIPROC_DIR"] = str(path)
    return path


prepare_worker_metrics()

app = Celery("smart_iptv")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@signals.setup_logging.connect
def use_django_logging(**_kwargs: Any) -> None:
    """Keep Django's LOGGING (structlog, redaction, stderr) in workers and beat.

    With a receiver on this signal Celery leaves logging alone instead of
    replacing the root handlers with its own unredacted format.
    """


# --- Worker metrics (SPEC §14, ADR-0018) ------------------------------------------------
# Task outcomes and the pool processes' counters, served by the worker's main process on
# CELERY_METRICS_PORT (apps.core.worker_metrics). Imported lazily: this module is loaded
# with the settings, before Django is set up.


@signals.worker_ready.connect
def _metrics_worker_ready(**kwargs: Any) -> None:
    from apps.core import worker_metrics  # noqa: PLC0415

    worker_metrics.on_worker_ready(**kwargs)


@signals.worker_process_shutdown.connect
def _metrics_worker_process_shutdown(**kwargs: Any) -> None:
    from apps.core import worker_metrics  # noqa: PLC0415

    worker_metrics.on_worker_process_shutdown(**kwargs)


@signals.task_prerun.connect
def _metrics_task_prerun(**kwargs: Any) -> None:
    from apps.core import worker_metrics  # noqa: PLC0415

    worker_metrics.on_task_prerun(**kwargs)


@signals.task_postrun.connect
def _metrics_task_postrun(**kwargs: Any) -> None:
    from apps.core import worker_metrics  # noqa: PLC0415

    worker_metrics.on_task_postrun(**kwargs)
