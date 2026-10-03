"""Deployment settings of the playback app, with their defaults.

Each value is read from the Django settings module when it defines the name, else
from the environment, else the default below. Runtime-tunable values (token
lifetimes, IP binding) are typed settings in the registry instead
(`playback.token_ttl_vod_s`, `playback.ip_binding`), read with `get_setting`.

None of these functions touches Postgres: stream-auth calls some of them.
"""

import os
from pathlib import Path

from django.conf import settings

from config.origins import origin

# Where Docker mounts the `media_token_keys` secret (docker/compose.yml), as on the edge.
DEFAULT_KEYS_FILE = "/run/secrets/media_token_keys"


def _int(name: str, default: int) -> int:
    value = getattr(settings, name, None)
    if value is None:
        raw = os.environ.get(name, "")
        return int(raw) if raw.strip() else default
    return int(value)


def _str(name: str, default: str) -> str:
    value = getattr(settings, name, None)
    if value is None:
        value = os.environ.get(name, "")
    text = str(value).strip()
    return text or default


def media_token_keys_file() -> Path:
    """The ADR-0007 key file (current and previous kid), shared with every edge."""
    return Path(_str("MEDIA_TOKEN_KEYS_FILE", DEFAULT_KEYS_FILE))


def media_base_url() -> str:
    """Public origin of the media edge, e.g. https://media.example.com (no trailing /).

    Defaults to MEDIA_HOST (else `media.<DOMAIN>`) on the public scheme and port.
    """
    explicit = _str("MEDIA_BASE_URL", "")
    if explicit:
        return explicit.rstrip("/")
    host = _str("MEDIA_HOST", f"media.{getattr(settings, 'DOMAIN', 'localhost')}")
    scheme = getattr(settings, "PUBLIC_SCHEME", "http")
    port = int(getattr(settings, "PUBLIC_PORT", 80))
    return origin(scheme, host, port)


def slot_window_s() -> int:
    """A concurrency slot without activity for this long is free (SPEC §7.4: 90 s)."""
    return _int("PLAYBACK_SLOT_WINDOW_S", 90)


def heartbeat_ttl_s() -> int:
    """TTL of `conc:{user}`, and the idle time after which a segmented session ends
    (SPEC §7.4: 120 s)."""
    return _int("PLAYBACK_HEARTBEAT_TTL_S", 120)


def progressive_slack_s() -> int:
    """A progressive session ends this long after its runtime has passed without a
    request: one long response can carry a whole film (ADR-0007, consequences)."""
    return _int("PLAYBACK_PROGRESSIVE_SLACK_S", 600)


def kick_ttl_s() -> int:
    """How long `kick:{session}` keeps the reason a session was stopped. It must
    outlive the edge's stream-auth cache (60 s)."""
    return _int("PLAYBACK_KICK_TTL_S", 3600)


def feed_interval_s() -> float:
    """Polling interval of the admin live-session feed (SPEC §7.4: 2 s)."""
    return float(_int("PLAYBACK_FEED_INTERVAL_MS", 2000)) / 1000
