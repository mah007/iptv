"""Deployment settings of the media pipeline, with their defaults (ADR-0010).

Each value is read from the Django settings module when it defines the name, else
from the environment, else the default below (the pattern of `apps.playback.conf`).
The attempts, retry delay and job timeout are admin settings (`library.transcode_*`,
read per job, so a change applies without a restart); their old environment variables
are only fallbacks (ADR-0019).
"""

import os
from pathlib import Path
from typing import cast

from django.conf import settings


def _int(name: str, default: int) -> int:
    value = getattr(settings, name, None)
    if value is None:
        raw = os.environ.get(name, "")
        return int(raw) if raw.strip() else default
    return int(value)


def renditions_root() -> Path:
    """Asset directories: `<root>/<storage_key>/compat.mp4`, `.../source.<ext>`."""
    return Path(settings.DATA_ROOT) / "renditions"


def _setting(key: str) -> int:
    from apps.core.services import get_setting  # noqa: PLC0415 (settings need the app registry)

    return int(cast("int", get_setting(key)))


def max_attempts() -> int:
    """Attempts per job before it fails for good (SPEC §7.3: retry up to 3 times)."""
    return max(1, _setting("library.transcode_max_attempts"))


def retry_backoff_s() -> int:
    """Delay before the first retry; doubled for each further attempt."""
    return max(0, _setting("library.transcode_retry_backoff_s"))


def job_timeout_s() -> int:
    """An ffmpeg run longer than this is killed (a hung encoder must not hold a slot)."""
    return max(60, _setting("library.transcode_job_timeout_h") * 3600)


def lease_ttl_s() -> int:
    """A running job's lease in redis-state; refreshed with its progress. A redelivered
    Celery message for a job whose lease is alive is a duplicate and is dropped."""
    return max(30, _int("TRANSCODE_LEASE_TTL_S", 120))


def caps_file() -> Path | None:
    """Where `run_transcoder` leaves this host's detected capabilities for its worker."""
    value = os.environ.get("TRANSCODER_CAPS_FILE", "").strip()
    return Path(value) if value else None
