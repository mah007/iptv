"""Deployment settings of live TV, with their defaults (ADR-0017).

Read from the Django settings module when it defines the name, else from the
environment, else the default (the pattern of `apps.playback.conf`). Behaviour an
admin tunes at runtime, the archive's disk budget included, lives in the settings
registry (`live.*`) instead; only paths and binaries are properties of the deployment.
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


def live_root() -> Path:
    """The live volume: `<root>/<channel key>/{live,archive}/` (apps.live.layout)."""
    return Path(_str("LIVE_ROOT", "/live"))


def archive_max_bytes() -> int:
    """The catch-up archive's disk budget across channels: the `live.archive_max_gb`
    setting (admin Settings, read again by the packager every few seconds). Beyond it
    the oldest hours go first, whatever the channels' catch-up days."""
    from apps.core.services import get_setting  # noqa: PLC0415 (settings need the app registry)

    return max(1, int(get_setting("live.archive_max_gb"))) * 1024**3


def ffmpeg_binary() -> str:
    return _str("LIVE_FFMPEG", "ffmpeg")


def ffprobe_binary() -> str:
    return _str("LIVE_FFPROBE", "ffprobe")
