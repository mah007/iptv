"""Deployment settings of live TV, with their defaults (ADR-0017).

Read from the Django settings module when it defines the name, else from the
environment, else the default (the pattern of `apps.playback.conf`). Behaviour an
admin tunes at runtime lives in the settings registry (`live.*`) instead; disk and
paths are properties of the deployment.
"""

import os
from pathlib import Path

from django.conf import settings


def _str(name: str, default: str) -> str:
    value = getattr(settings, name, None)
    if value is None:
        value = os.environ.get(name, "")
    text = str(value).strip()
    return text or default


def _float(name: str, default: float) -> float:
    raw = _str(name, "")
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


def live_root() -> Path:
    """The live volume: `<root>/<channel key>/{live,archive}/` (apps.live.layout)."""
    return Path(_str("LIVE_ROOT", "/live"))


def archive_max_bytes() -> int:
    """The catch-up archive's disk budget across channels (LIVE_ARCHIVE_MAX_GB, default
    50): beyond it the oldest hours go first, whatever the channels' catch-up days."""
    return int(max(0.1, _float("LIVE_ARCHIVE_MAX_GB", 50.0)) * 1024**3)


def ffmpeg_binary() -> str:
    return _str("LIVE_FFMPEG", "ffmpeg")


def ffprobe_binary() -> str:
    return _str("LIVE_FFPROBE", "ffprobe")
