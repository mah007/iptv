"""Where live channels live on disk, and the Redis keys about them (ADR-0017).

Django-free on purpose: the live relay (`apps.live.relay`) imports it, and the relay
must run without Django, settings or database access.

    <root>/<key>/live/index.m3u8                  the rolling HLS playlist (ffmpeg)
    <root>/<key>/live/<n>.ts                      its MPEG-TS segments (n: epoch-based)
    <root>/<key>/archive/YYYYMMDD/HH/<ms>-<ms>.ts catch-up: hard links of the live
                                                  segments, named start-duration in ms

`<key>` is the channel's UUID in hex: the media token's `title` field. The media
edge serves the segment files itself; the relay serves the playlist, the continuous
`live.ts` stream and catch-up windows (`archive/<start>-<seconds>.<ts|m3u8>`).
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

LIVE_DIR = "live"
ARCHIVE_DIR = "archive"
PLAYLIST = "index.m3u8"

#: The token rendition names that reach the live root (reserved: no VOD rendition uses them).
LIVE_RENDITION = "live"
ARCHIVE_RENDITION = "archive"
#: Entries the relay serves for a live token.
LIVE_TS_TAIL = "live.ts"
LIVE_PLAYLIST_TAIL = f"{LIVE_DIR}/{PLAYLIST}"

CHANNEL_KEY = re.compile(r"[0-9a-f]{32}")
ARCHIVE_FILE = re.compile(r"(?P<start>[0-9]{13})-(?P<duration>[0-9]{1,9})\.ts")
WINDOW_TAIL = re.compile(
    r"archive/(?P<start>[1-9][0-9]{9})-(?P<seconds>[1-9][0-9]{0,4})\.(?P<ext>ts|m3u8)"
)

# --- Redis keys (redis-state) ------------------------------------------------------------
#: Pub/sub channel: a live session just started on channel <key> (wake the packager).
WAKE_CHANNEL = "live:wake"
#: Hash per channel: what the packager is doing (state, since, error, bitrate, ...).
STATUS_PREFIX = "live:status:"
#: Hash per channel: the catch-up archive's first and last instant (epoch ms).
ARCHIVE_PREFIX = "live:archive:"
#: The packager's heartbeat (a JSON summary, short TTL).
PACKAGER_KEY = "live:packager"


def status_key(key: str) -> str:
    return f"{STATUS_PREFIX}{key}"


def archive_key(key: str) -> str:
    return f"{ARCHIVE_PREFIX}{key}"


def checked_key(key: str) -> str:
    if not CHANNEL_KEY.fullmatch(key):
        msg = "not a channel key"
        raise ValueError(msg)
    return key


def channel_dir(root: Path, key: str) -> Path:
    return root / checked_key(key)


def live_dir(root: Path, key: str) -> Path:
    return channel_dir(root, key) / LIVE_DIR


def playlist_path(root: Path, key: str) -> Path:
    return live_dir(root, key) / PLAYLIST


def archive_dir(root: Path, key: str) -> Path:
    return channel_dir(root, key) / ARCHIVE_DIR


def archive_hour_dir(root: Path, key: str, moment: datetime) -> Path:
    utc = moment.astimezone(UTC)
    return archive_dir(root, key) / f"{utc:%Y%m%d}" / f"{utc:%H}"


def archive_file_name(start_ms: int, duration_ms: int) -> str:
    return f"{start_ms:013d}-{max(1, duration_ms)}.ts"


def window_tail(start_s: int, seconds: int, ext: str) -> str:
    return f"{ARCHIVE_DIR}/{start_s}-{seconds}.{ext}"


@dataclass(frozen=True, slots=True, order=True)
class ArchiveSegment:
    start_ms: int
    duration_ms: int
    path: Path

    @property
    def end_ms(self) -> int:
        return self.start_ms + self.duration_ms

    @property
    def relative(self) -> str:
        """The segment relative to `archive/`: `YYYYMMDD/HH/<file>`."""
        return f"{self.path.parent.parent.name}/{self.path.parent.name}/{self.path.name}"


def _hours(start: datetime, end: datetime) -> list[datetime]:
    first = start.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    hours = []
    moment = first
    while moment <= end:
        hours.append(moment)
        moment += timedelta(hours=1)
    return hours


def archive_segments(root: Path, key: str, start_ms: int, end_ms: int) -> list[ArchiveSegment]:
    """Archived segments overlapping [start_ms, end_ms), in time order."""
    if end_ms <= start_ms:
        return []
    # A segment that starts in the previous hour may still overlap the window.
    start = datetime.fromtimestamp(start_ms / 1000, tz=UTC) - timedelta(minutes=1)
    end = datetime.fromtimestamp(end_ms / 1000, tz=UTC)
    found: list[ArchiveSegment] = []
    for hour in _hours(start, end):
        folder = archive_hour_dir(root, key, hour)
        try:
            names = [entry.name for entry in folder.iterdir()]
        except OSError:
            continue
        for name in names:
            match = ARCHIVE_FILE.fullmatch(name)
            if match is None:
                continue
            segment = ArchiveSegment(int(match["start"]), int(match["duration"]), folder / name)
            if segment.end_ms > start_ms and segment.start_ms < end_ms:
                found.append(segment)
    return sorted(found)
