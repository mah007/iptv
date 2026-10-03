"""Parser for ffmpeg's `-progress pipe:1` stream (SPEC §7.3 "Execution").

ffmpeg writes blocks of `key=value` lines, each block ending with `progress=continue`
(or `progress=end` once the encode is finished):

    frame=120
    fps=59.94
    out_time_us=4004000
    out_time_ms=4004000      <- microseconds too, despite the name (kept by ffmpeg for compat)
    out_time=00:00:04.004000
    speed=1.99x
    progress=continue

`ProgressParser.feed()` takes one line at a time and returns a `ProgressUpdate` at the end
of every block. `Throttle` rate-limits publishing (the DB every 5 s, SSE every 1 s).
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Final

DB_PUBLISH_INTERVAL_S: Final = 5.0
SSE_PUBLISH_INTERVAL_S: Final = 1.0


@dataclass(frozen=True, slots=True)
class ProgressUpdate:
    """One progress block. Values ffmpeg reports as `N/A` are None."""

    out_time_ms: int | None  # media time written so far
    frame: int | None
    fps: float | None  # encoding speed in frames per second
    speed: float | None  # media seconds per wall-clock second (1.0 = real time)
    bitrate_kbps: float | None
    total_size: int | None  # bytes written so far
    done: bool  # `progress=end`
    percent: float | None  # 0-100 when the duration is known
    eta_s: int | None  # wall-clock seconds left, from `speed`


class ProgressParser:
    """Turns ffmpeg progress lines into `ProgressUpdate`s.

    `duration_ms` (the expected output duration) enables `percent` and `eta_s`.
    """

    def __init__(self, duration_ms: int | None = None) -> None:
        self.duration_ms = duration_ms if duration_ms and duration_ms > 0 else None
        self._block: dict[str, str] = {}
        self.last: ProgressUpdate | None = None

    def feed(self, line: str) -> ProgressUpdate | None:
        """Consume one line; return an update when it closes a block."""
        key, sep, value = line.strip().partition("=")
        if not sep:
            return None
        key, value = key.strip(), value.strip()
        if key != "progress":
            self._block[key] = value
            return None
        update = self._update(self._block, done=value == "end")
        self._block = {}
        self.last = update
        return update

    def feed_lines(self, lines: Iterable[str]) -> list[ProgressUpdate]:
        """Consume many lines (for example a whole captured stream)."""
        return [update for line in lines if (update := self.feed(line)) is not None]

    def _update(self, block: dict[str, str], *, done: bool) -> ProgressUpdate:
        out_time_ms = _out_time_ms(block)
        speed = _float(block.get("speed", "").removesuffix("x"))
        percent = None
        eta_s = None
        if self.duration_ms is not None:
            if done:
                percent = 100.0
                eta_s = 0
            elif out_time_ms is not None:
                percent = round(min(100.0, 100.0 * out_time_ms / self.duration_ms), 2)
                if speed:
                    remaining_ms = max(0, self.duration_ms - out_time_ms)
                    eta_s = math.ceil(remaining_ms / 1000 / speed)
        return ProgressUpdate(
            out_time_ms=out_time_ms,
            frame=_int(block.get("frame")),
            fps=_float(block.get("fps")),
            speed=speed,
            bitrate_kbps=_float(block.get("bitrate", "").removesuffix("kbits/s")),
            total_size=_int(block.get("total_size")),
            done=done,
            percent=percent,
            eta_s=eta_s,
        )


class Throttle:
    """`ready()` is true at most once per `interval_s` (and always when forced)."""

    def __init__(self, interval_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.interval_s = interval_s
        self._clock = clock
        self._last: float | None = None

    def ready(self, *, force: bool = False) -> bool:
        now = self._clock()
        if force or self._last is None or now - self._last >= self.interval_s:
            self._last = now
            return True
        return False


def _out_time_ms(block: dict[str, str]) -> int | None:
    # out_time_us and out_time_ms are both microseconds; out_time is HH:MM:SS.micro.
    for key in ("out_time_us", "out_time_ms"):
        micros = _int(block.get(key))
        if micros is not None:
            return micros // 1000 if micros >= 0 else None
    return _clock_ms(block.get("out_time"))


def _clock_ms(value: str | None) -> int | None:
    if not value or value.startswith("-"):
        return None
    parts = value.split(":")
    if len(parts) != 3:
        return None
    try:
        hours, minutes, seconds = int(parts[0]), int(parts[1]), float(parts[2])
    except ValueError:
        return None
    return round((hours * 3600 + minutes * 60 + seconds) * 1000)


def _int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None
