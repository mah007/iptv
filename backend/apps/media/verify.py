"""Output verification (SPEC §7.3 "Execution").

Every output is checked before it is moved into place: the duration must be within
0.5 s of the source and the stream counts must be what the plan asked for. HLS outputs
are checked playlist by playlist (every referenced playlist, init segment and media
segment exists, segment durations respect the target duration, totals match).

Problems are reported as stable codes (`duration`, `audio_streams`, ...) so a failed
job's error says what was wrong without exposing storage paths.
"""

from __future__ import annotations

import math
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from apps.media.planner import AudioPlan, CompatMp4Plan, UhdPlan
from apps.media.probe import ProbeResult, input_url, probe

DURATION_TOLERANCE_MS: Final = 500
# EXTINF values rounded to the nearest integer must not exceed EXT-X-TARGETDURATION.
_TARGET_SLACK_S: Final = 0.5


class VerificationError(Exception):
    """An output failed verification; `problems` lists what was wrong."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = tuple(problems)
        super().__init__("output verification failed: " + ", ".join(self.problems))


@dataclass(frozen=True, slots=True)
class Expected:
    """What an output file must contain."""

    duration_ms: int | None  # the source duration; None skips the duration check
    video: int = 1
    audio: int = 0
    subtitle: int = 0
    video_codec: str | None = None
    audio_codecs: tuple[str, ...] | None = None  # in output order
    faststart: bool | None = None  # True: `moov` must precede `mdat`


def expected_compat(plan: CompatMp4Plan, duration_ms: int | None) -> Expected:
    """What a compat MP4 (or remux) produced from `plan` must contain."""
    return Expected(
        duration_ms=duration_ms,
        video=1,
        audio=len(plan.audio),
        subtitle=len(plan.subtitles),
        video_codec=plan.video.codec,
        audio_codecs=tuple(audio.codec for audio in plan.audio),
        faststart=True,
    )


def expected_uhd(plan: UhdPlan, audio: tuple[AudioPlan, ...], duration_ms: int | None) -> Expected:
    """What an encoded UHD version must contain."""
    return Expected(
        duration_ms=duration_ms,
        video=1,
        audio=len(audio),
        video_codec=plan.video.codec,
        audio_codecs=tuple(track.codec for track in audio),
        faststart=True,
    )


def check_probe(
    result: ProbeResult, expected: Expected, *, tolerance_ms: int = DURATION_TOLERANCE_MS
) -> tuple[str, ...]:
    """Compare a probed output with expectations; return the problem codes (empty = ok)."""
    problems: list[str] = []
    if expected.duration_ms is not None:
        if result.duration_ms is None:
            problems.append("duration_unknown")
        elif abs(result.duration_ms - expected.duration_ms) > tolerance_ms:
            problems.append("duration")
    if len(result.video_streams) != expected.video:
        problems.append("video_streams")
    if len(result.audio) != expected.audio:
        problems.append("audio_streams")
    if len(result.subtitles) != expected.subtitle:
        problems.append("subtitle_streams")
    video = result.video
    if expected.video_codec is not None and (video is None or video.codec != expected.video_codec):
        problems.append("video_codec")
    if expected.audio_codecs is not None and (
        tuple(a.codec for a in result.audio) != expected.audio_codecs
    ):
        problems.append("audio_codecs")
    if expected.faststart is not None and result.faststart is not expected.faststart:
        problems.append("faststart")
    return tuple(problems)


def verify_file(
    path: str | Path,
    expected: Expected,
    *,
    ffprobe: str = "ffprobe",
    tolerance_ms: int = DURATION_TOLERANCE_MS,
    timeout_s: float = 120.0,
) -> ProbeResult:
    """Probe an output file and check it; raises `VerificationError` on any problem."""
    file = Path(path)
    if not file.is_file() or file.stat().st_size == 0:
        raise VerificationError(["missing_output"])
    result = probe(file, ffprobe=ffprobe, timeout_s=timeout_s)
    problems = check_probe(result, expected, tolerance_ms=tolerance_ms)
    if problems:
        raise VerificationError(problems)
    return result


def keyframe_times(
    path: str | Path, *, ffprobe: str = "ffprobe", timeout_s: float = 120.0
) -> tuple[float, ...]:
    """Presentation times (seconds) of the key frames of the first video stream.

    Reads packet flags only (no decoding), so it is cheap even for long files.
    """
    argv = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "packet=pts_time,flags",
        "-of",
        "csv=p=0",
        "-i",
        input_url(path),
    ]
    completed = subprocess.run(  # noqa: S603 - argv list, no shell
        argv, capture_output=True, text=True, timeout=timeout_s, check=False
    )
    if completed.returncode != 0:
        raise VerificationError(["keyframes_unreadable"])
    times: list[float] = []
    for line in completed.stdout.splitlines():
        pts, _, flags = line.partition(",")
        if "K" not in flags:
            continue
        try:
            times.append(float(pts))
        except ValueError:
            continue
    return tuple(sorted(times))


# --- HLS -----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Segment:
    uri: str
    duration_s: float


@dataclass(frozen=True, slots=True)
class MediaPlaylist:
    target_duration: int
    segments: tuple[Segment, ...]
    init_uri: str | None
    ended: bool
    playlist_type: str | None
    independent_segments: bool

    @property
    def total_s(self) -> float:
        return sum(segment.duration_s for segment in self.segments)


@dataclass(frozen=True, slots=True)
class Variant:
    uri: str
    attributes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MasterPlaylist:
    variants: tuple[Variant, ...]
    media: tuple[dict[str, str], ...]  # EXT-X-MEDIA attribute lists
    independent_segments: bool


def parse_attributes(text: str) -> dict[str, str]:
    """An HLS attribute list (`A=1,B="x,y"`) as a dict; quotes are removed."""
    attributes: dict[str, str] = {}
    key: list[str] = []
    value: list[str] = []
    in_key, quoted = True, False
    for char in text + ",":
        if in_key:
            if char == "=":
                in_key = False
            elif char != ",":
                key.append(char)
            continue
        if char == '"':
            quoted = not quoted
        elif char == "," and not quoted:
            name = "".join(key).strip()
            if name:
                attributes[name] = "".join(value).strip()
            key, value, in_key = [], [], True
        else:
            value.append(char)
    return attributes


def parse_media_playlist(text: str) -> MediaPlaylist:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or lines[0] != "#EXTM3U":
        raise VerificationError(["playlist_header"])
    target = 0
    init_uri = None
    ended = False
    independent = False
    playlist_type = None
    segments: list[Segment] = []
    pending: float | None = None
    for line in lines[1:]:
        if line.startswith("#EXT-X-TARGETDURATION:"):
            target = int(line.partition(":")[2])
        elif line.startswith("#EXT-X-MAP:"):
            init_uri = parse_attributes(line.partition(":")[2]).get("URI")
        elif line.startswith("#EXTINF:"):
            pending = float(line.partition(":")[2].split(",")[0])
        elif line == "#EXT-X-ENDLIST":
            ended = True
        elif line == "#EXT-X-INDEPENDENT-SEGMENTS":
            independent = True
        elif line.startswith("#EXT-X-PLAYLIST-TYPE:"):
            playlist_type = line.partition(":")[2]
        elif not line.startswith("#"):
            if pending is None:
                raise VerificationError(["segment_without_extinf"])
            segments.append(Segment(uri=line, duration_s=pending))
            pending = None
    return MediaPlaylist(
        target_duration=target,
        segments=tuple(segments),
        init_uri=init_uri,
        ended=ended,
        playlist_type=playlist_type,
        independent_segments=independent,
    )


def read_media_playlist(path: str | Path) -> MediaPlaylist:
    return parse_media_playlist(Path(path).read_text(encoding="utf-8"))


def parse_master_playlist(text: str) -> MasterPlaylist:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or lines[0] != "#EXTM3U":
        raise VerificationError(["playlist_header"])
    variants: list[Variant] = []
    media: list[dict[str, str]] = []
    pending: dict[str, str] | None = None
    independent = False
    for line in lines[1:]:
        if line.startswith("#EXT-X-STREAM-INF:"):
            pending = parse_attributes(line.partition(":")[2])
        elif line.startswith("#EXT-X-MEDIA:"):
            media.append(parse_attributes(line.partition(":")[2]))
        elif line == "#EXT-X-INDEPENDENT-SEGMENTS":
            independent = True
        elif not line.startswith("#") and pending is not None:
            variants.append(Variant(uri=line, attributes=pending))
            pending = None
    return MasterPlaylist(
        variants=tuple(variants), media=tuple(media), independent_segments=independent
    )


def verify_hls(
    directory: str | Path,
    *,
    expected_duration_ms: int | None,
    master_name: str = "master.m3u8",
    tolerance_ms: int = DURATION_TOLERANCE_MS,
) -> MasterPlaylist:
    """Check a packaged HLS directory; raises `VerificationError` listing every problem."""
    root = Path(directory).resolve()
    master_path = root / master_name
    if not master_path.is_file():
        raise VerificationError(["missing_master"])
    master = parse_master_playlist(master_path.read_text(encoding="utf-8"))
    problems: list[str] = []
    if not master.variants:
        problems.append("no_variants")
    for variant in master.variants:
        for attribute in ("BANDWIDTH", "AVERAGE-BANDWIDTH", "CODECS", "RESOLUTION"):
            if attribute not in variant.attributes:
                problems.append(f"variant_{attribute.lower()}")
    uris = {variant.uri for variant in master.variants}
    uris |= {media["URI"] for media in master.media if "URI" in media}
    for uri in sorted(uris):
        problems += _check_media_playlist(root, uri, expected_duration_ms, tolerance_ms)
    if problems:
        raise VerificationError(sorted(set(problems)))
    return master


def _check_media_playlist(
    root: Path, uri: str, expected_duration_ms: int | None, tolerance_ms: int
) -> list[str]:
    path = _inside(root, uri)
    if path is None or not path.is_file():
        return ["missing_playlist"]
    try:
        playlist = read_media_playlist(path)
    except (VerificationError, ValueError):
        return ["invalid_playlist"]
    problems = []
    if not playlist.ended:
        problems.append("playlist_not_ended")
    if not playlist.segments:
        problems.append("no_segments")
    if playlist.init_uri is not None:
        init = _inside(path.parent, playlist.init_uri)
        if init is None or not init.is_file():
            problems.append("missing_init_segment")
    for segment in playlist.segments:
        file = _inside(path.parent, segment.uri)
        if file is None or not file.is_file():
            problems.append("missing_segment")
        if segment.duration_s > playlist.target_duration + _TARGET_SLACK_S:
            problems.append("segment_exceeds_target")
    if (
        expected_duration_ms is not None
        and playlist.segments
        and abs(playlist.total_s * 1000 - expected_duration_ms) > tolerance_ms
    ):
        problems.append("duration")
    return problems


def _inside(root: Path, uri: str) -> Path | None:
    """The file a relative URI names, or None when it points outside `root`."""
    if "://" in uri or uri.startswith("/"):
        return None
    path = (root / uri).resolve()
    return path if path.is_relative_to(root.resolve()) else None


def ceil_target_duration(durations: Sequence[float]) -> int:
    """EXT-X-TARGETDURATION for these segment durations."""
    return max(1, *(math.ceil(d - 1e-3) for d in durations)) if durations else 1
