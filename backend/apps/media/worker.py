"""Transcoder workers keep their capabilities fresh in redis-state (SPEC §7.3).

`manage.py run_transcoder` detects the encoders, writes them to TRANSCODER_CAPS_FILE
and starts a Celery worker on the matching `transcode.*` queues. In that worker, this
module re-registers `worker:caps:<host>` (TTL 60 s) every 20 s, so the planner routes
jobs only to backends a live transcoder has, and removes it on shutdown. Other Celery
processes (the general worker, beat) have no caps file and do nothing here.
"""

import json
import threading
from typing import Any

import structlog
from celery import signals

from apps.core.stores import state_redis
from apps.media import conf, hwdetect

logger = structlog.get_logger(__name__)

REFRESH_S = 20.0
_stop = threading.Event()


def _document() -> dict[str, Any] | None:
    path = conf.caps_file()
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("host"), str) else None


def register_once() -> str | None:
    """Store this host's capabilities with a fresh TTL; returns the key."""
    document = _document()
    if document is None:
        return None
    key = hwdetect.CAPS_KEY_TEMPLATE.format(host=document["host"])
    state_redis().set(key, json.dumps(document, sort_keys=True), ex=hwdetect.CAPS_TTL_S)
    return key


def _refresh() -> None:
    while not _stop.wait(REFRESH_S):
        try:
            register_once()
        except Exception:
            logger.warning("media.caps_refresh_failed")


@signals.worker_ready.connect
def start_caps_refresh(**_kwargs: Any) -> None:
    if register_once() is None:
        return
    _stop.clear()
    threading.Thread(target=_refresh, name="caps-refresh", daemon=True).start()


@signals.worker_shutdown.connect
def remove_caps(**_kwargs: Any) -> None:
    document = _document()
    _stop.set()
    if document is not None:
        try:
            state_redis().delete(hwdetect.CAPS_KEY_TEMPLATE.format(host=document["host"]))
        except Exception:
            logger.warning("media.caps_remove_failed")
