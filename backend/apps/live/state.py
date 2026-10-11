"""What the packager publishes in redis-state, read by the control plane (ADR-0017).

- `wake(key)`: a live session just started on a channel; the packager starts it now
  instead of at its next scan.
- `status(key)`: the packager's view of a channel (state, since, error, bitrate,
  viewers, archive bytes), for the admin.
- `archive_window(key)`: the first and last instants the catch-up archive holds,
  for Xtream (`has_archive`, timeshift requests).

Every reader treats Redis errors as "unknown"; none touches the media volume.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

import redis
import structlog

from apps.core.stores import state_redis
from apps.live import layout

logger = structlog.get_logger(__name__)


def wake(key: str) -> None:
    try:
        state_redis().publish(layout.WAKE_CHANNEL, layout.checked_key(key))
    except redis.RedisError:
        logger.warning("live.wake_unavailable")


@dataclass(frozen=True, slots=True)
class ArchiveWindow:
    first: datetime
    last: datetime


def _decode(raw: Mapping[bytes, bytes]) -> dict[str, str]:
    return {key.decode(): value.decode() for key, value in raw.items()}


def _window(raw: Mapping[bytes, bytes]) -> ArchiveWindow | None:
    fields = _decode(raw)
    try:
        first, last = int(fields["first"]), int(fields["last"])
    except (KeyError, ValueError):
        return None
    return ArchiveWindow(
        datetime.fromtimestamp(first / 1000, tz=UTC), datetime.fromtimestamp(last / 1000, tz=UTC)
    )


def archive_window(key: str) -> ArchiveWindow | None:
    try:
        raw = cast("Mapping[bytes, bytes]", state_redis().hgetall(layout.archive_key(key)))
    except redis.RedisError:
        return None
    return _window(raw)


def archive_windows(keys: Iterable[str]) -> dict[str, ArchiveWindow]:
    wanted = list(dict.fromkeys(keys))
    if not wanted:
        return {}
    try:
        pipe = state_redis().pipeline(transaction=False)
        for key in wanted:
            pipe.hgetall(layout.archive_key(key))
        raws = cast("list[Mapping[bytes, bytes]]", pipe.execute())
    except redis.RedisError:
        return {}
    windows = {}
    for key, raw in zip(wanted, raws, strict=True):
        window = _window(raw)
        if window is not None:
            windows[key] = window
    return windows


def statuses(keys: Iterable[str]) -> dict[str, dict[str, str]]:
    """Each channel's packager status hash (missing channels: not running)."""
    wanted = list(dict.fromkeys(keys))
    if not wanted:
        return {}
    try:
        pipe = state_redis().pipeline(transaction=False)
        for key in wanted:
            pipe.hgetall(layout.status_key(key))
        raws = cast("list[Mapping[bytes, bytes]]", pipe.execute())
    except redis.RedisError:
        return {}
    return {key: _decode(raw) for key, raw in zip(wanted, raws, strict=True) if raw}


def packager() -> str | None:
    """The packager's heartbeat JSON, None when it is not running."""
    try:
        raw = cast("bytes | None", state_redis().get(layout.PACKAGER_KEY))
    except redis.RedisError:
        return None
    return raw.decode() if raw else None
