"""Reading ffmpeg's live HLS playlist and writing catch-up playlists (ADR-0017).

Django-free: the live relay imports it.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from apps.live.layout import ArchiveSegment

#: A playlist larger than this is not one ffmpeg wrote for us.
MAX_PLAYLIST_BYTES = 256 * 1024


@dataclass(frozen=True, slots=True)
class Segment:
    sequence: int
    uri: str
    duration_s: float
    #: EXT-X-PROGRAM-DATE-TIME of the segment, when the playlist carries it.
    program_date_time: datetime | None


@dataclass(frozen=True, slots=True)
class MediaPlaylist:
    target_duration: int
    media_sequence: int
    segments: tuple[Segment, ...]
    ended: bool


def _attribute_value(line: str) -> str:
    return line.split(":", 1)[1].strip() if ":" in line else ""


def _date_time(text: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def parse_media_playlist(text: str) -> MediaPlaylist | None:
    """The segments of a media playlist; None when `text` is not one."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or lines[0] != "#EXTM3U":
        return None
    target, sequence, ended = 0, 0, False
    segments: list[Segment] = []
    duration: float | None = None
    stamp: datetime | None = None
    for line in lines[1:]:
        if line.startswith("#EXT-X-TARGETDURATION:"):
            try:
                target = int(_attribute_value(line))
            except ValueError:
                return None
        elif line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            try:
                sequence = int(_attribute_value(line))
            except ValueError:
                return None
        elif line.startswith("#EXTINF:"):
            try:
                duration = float(_attribute_value(line).split(",", 1)[0])
            except ValueError:
                return None
        elif line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            stamp = _date_time(_attribute_value(line))
        elif line == "#EXT-X-ENDLIST":
            ended = True
        elif line.startswith("#EXT-X-STREAM-INF"):
            return None  # a multivariant playlist
        elif not line.startswith("#"):
            if duration is None:
                return None
            segments.append(Segment(sequence + len(segments), line, duration, stamp))
            duration, stamp = None, None
    return MediaPlaylist(target, sequence, tuple(segments), ended)


def archive_playlist(segments: Sequence[ArchiveSegment], *, complete: bool) -> str:
    """A catch-up playlist over archived segments, URIs relative to `archive/`.

    `complete`: the window has ended, so the playlist is VOD with an end tag; otherwise
    it is an EVENT playlist the player reloads while the window is still recording.
    """
    durations = [max(segment.duration_ms, 1) / 1000 for segment in segments]
    target = max([math.ceil(value) for value in durations] or [1])
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        f"#EXT-X-TARGETDURATION:{target}",
        "#EXT-X-MEDIA-SEQUENCE:0",
        f"#EXT-X-PLAYLIST-TYPE:{'VOD' if complete else 'EVENT'}",
        "#EXT-X-INDEPENDENT-SEGMENTS",
    ]
    previous_end: int | None = None
    for segment, seconds in zip(segments, durations, strict=True):
        if previous_end is not None and segment.start_ms - previous_end > 2000:
            lines.append("#EXT-X-DISCONTINUITY")  # a gap: the source was down
        lines.append(f"#EXTINF:{seconds:.3f},")
        lines.append(segment.relative)
        previous_end = segment.end_ms
    if complete:
        lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"
