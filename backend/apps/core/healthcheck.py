"""Container healthchecks: `python -m apps.core.healthcheck web|beat|watcher|live|relay`.

Exit 0 = healthy.
"""

import os
import sys
import time
import urllib.request
from typing import cast

WEB_LIVE_URL = "http://127.0.0.1:8000/internal/health/live"
BEAT_MAX_AGE_S = 90
WATCHER_MAX_AGE_S = 90
LIVE_MAX_AGE_S = 60
RELAY_URL = "http://127.0.0.1:8090/healthz"


def check_web() -> bool:
    request = urllib.request.Request(WEB_LIVE_URL, headers={"Host": "localhost"})
    try:
        with urllib.request.urlopen(request, timeout=3) as resp:  # noqa: S310 (fixed local URL)
            return bool(resp.status == 200)
    except OSError:
        return False


def _fresh(key: str, max_age_s: int) -> bool:
    """Whether the heartbeat timestamp at `key` in redis-state is recent."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
    import redis  # noqa: PLC0415
    from django.conf import settings  # noqa: PLC0415 (settings only, no app loading)

    client = redis.Redis.from_url(settings.REDIS_STATE_URL, socket_timeout=3)
    try:
        # redis-py types sync and async clients together; this one is sync.
        value = cast("bytes | None", client.get(key))
    except redis.RedisError:
        return False
    if value is None:
        return False
    return time.time() - int(value) <= max_age_s


def check_beat() -> bool:
    """Healthy while the heartbeat that beat schedules keeps arriving."""
    from apps.core.tasks import HEARTBEAT_KEY  # noqa: PLC0415

    return _fresh(HEARTBEAT_KEY, BEAT_MAX_AGE_S)


def check_watcher() -> bool:
    """Healthy while the library watcher's loop keeps beating (every 30 s)."""
    return _fresh("hb:watcher", WATCHER_MAX_AGE_S)


def check_live() -> bool:
    """Healthy while the live packager's loop keeps publishing (every second)."""
    return _fresh("hb:live", LIVE_MAX_AGE_S)


def check_relay() -> bool:
    try:
        with urllib.request.urlopen(RELAY_URL, timeout=3) as resp:
            return bool(resp.status == 200)
    except OSError:
        return False


CHECKS = {
    "web": check_web,
    "beat": check_beat,
    "watcher": check_watcher,
    "live": check_live,
    "relay": check_relay,
}


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in CHECKS:
        sys.stderr.write(f"usage: python -m apps.core.healthcheck {{{'|'.join(CHECKS)}}}\n")
        return 2
    return 0 if CHECKS[argv[0]]() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
