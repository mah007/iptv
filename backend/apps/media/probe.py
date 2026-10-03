"""ffprobe wrapper (SPEC §7.2 step 3, §7.3).

`probe()` runs `ffprobe -v error -show_format -show_streams -show_chapters -of json` and
`parse_probe()` maps the JSON to a typed `ProbeResult`: container, duration, bitrate, the
primary video stream (codec/profile/level/size/fps/pix_fmt/HDR kind), audio tracks and
subtitle tracks (text vs image). The raw JSON is kept, minus the input file name so a
stored probe never reveals a storage path.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from fractions import Fraction
from pathlib import Path
from typing import Any, Final

FFPROBE_ARGS: Final = (
    "-v",
    "error",
    "-show_format",
    "-show_streams",
    "-show_chapters",
    "-of",
    "json",
)

TEXT_SUBTITLE_CODECS: Final = frozenset(
    {
        "subrip",
        "srt",
        "ass",
        "ssa",
        "webvtt",
        "mov_text",
        "text",
        "subviewer",
        "subviewer1",
        "microdvd",
        "sami",
        "realtext",
        "stl",
        "jacosub",
        "mpl2",
        "pjs",
        "vplayer",
    }
)
IMAGE_SUBTITLE_CODECS: Final = frozenset(
    {"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub", "hdmv_text_subtitle"}
)
DOLBY_VISION_TAGS: Final = frozenset({"dvh1", "dvhe", "dav1", "dva1", "dvav"})
UNDETERMINED: Final = "und"

# ISO 639-1 -> ISO 639-2/B for the languages a two-letter tag is most likely to carry.
ISO639_1_TO_2: Final[Mapping[str, str]] = {
    "ar": "ara",
    "en": "eng",
    "fr": "fre",
    "de": "ger",
    "es": "spa",
    "it": "ita",
    "pt": "por",
    "ru": "rus",
    "tr": "tur",
    "fa": "per",
    "ur": "urd",
    "hi": "hin",
    "bn": "ben",
    "id": "ind",
    "ms": "may",
    "nl": "dut",
    "sv": "swe",
    "pl": "pol",
    "el": "gre",
    "he": "heb",
    "ja": "jpn",
    "ko": "kor",
    "zh": "chi",
    "th": "tha",
    "vi": "vie",
    "uk": "ukr",
    "ro": "rum",
    "cs": "cze",
    "hu": "hun",
    "da": "dan",
    "fi": "fin",
    "no": "nor",
}
# The reverse, plus the ISO 639-2/T spellings, for BCP 47 tags (HLS LANGUAGE attribute).
ISO639_2_TO_1: Final[Mapping[str, str]] = {
    **{three: two for two, three in ISO639_1_TO_2.items()},
    "fra": "fr",
    "deu": "de",
    "fas": "fa",
    "msa": "ms",
    "nld": "nl",
    "ell": "el",
    "zho": "zh",
    "ron": "ro",
    "ces": "cs",
}

_BIT_DEPTH = re.compile(r"(?:p|le|be)?(\d{2})(?:le|be)?$")


class ProbeError(Exception):
    """ffprobe could not run or could not read the file."""


class HdrKind(StrEnum):
    SDR = "sdr"
    HDR10 = "hdr10"
    HLG = "hlg"
    DV = "dv"


@dataclass(frozen=True, slots=True)
class VideoStream:
    index: int  # absolute stream index in the file (`-map 0:<index>`)
    codec: str
    profile: str | None
    level: int | None  # as ffprobe reports it: 41 = H.264 4.1, 153 = HEVC 5.1
    width: int
    height: int
    sar: Fraction  # sample aspect ratio; 1 when unknown
    frame_rate: Fraction | None
    pix_fmt: str | None
    bit_depth: int
    hdr: HdrKind
    color_primaries: str | None
    color_transfer: str | None
    color_space: str | None
    color_range: str | None
    field_order: str | None
    bitrate: int | None  # bit/s
    codec_tag: str | None
    default: bool
    dv_profile: int | None = None
    dv_bl_compat_id: int | None = None

    @property
    def fps(self) -> float | None:
        return float(self.frame_rate) if self.frame_rate else None

    @property
    def interlaced(self) -> bool:
        return self.field_order in {"tt", "bb", "tb", "bt"}

    @property
    def display_width(self) -> int:
        """Width in square pixels (anamorphic sources are wider than they are coded)."""
        return max(2, round(self.width * self.sar))


@dataclass(frozen=True, slots=True)
class AudioStream:
    index: int
    codec: str
    profile: str | None
    channels: int
    channel_layout: str | None
    sample_rate: int | None
    bitrate: int | None
    language: str  # ISO 639-2, "und" when unknown
    title: str | None
    default: bool
    forced: bool
    commentary: bool


@dataclass(frozen=True, slots=True)
class SubtitleStream:
    index: int
    codec: str
    language: str
    title: str | None
    default: bool
    forced: bool
    hearing_impaired: bool

    @property
    def is_text(self) -> bool:
        return self.codec in TEXT_SUBTITLE_CODECS

    @property
    def is_image(self) -> bool:
        return self.codec in IMAGE_SUBTITLE_CODECS


@dataclass(frozen=True, slots=True)
class Chapter:
    start_ms: int
    end_ms: int
    title: str | None


@dataclass(frozen=True, slots=True)
class ProbeResult:
    container: str  # mp4 | mov | mkv | webm | ts | avi | <ffprobe format name>
    format_name: str
    duration_ms: int | None
    bitrate: int | None  # bit/s, whole file
    size: int | None
    video_streams: tuple[VideoStream, ...]  # cover art (attached pictures) excluded
    audio: tuple[AudioStream, ...]
    subtitles: tuple[SubtitleStream, ...]
    chapters: tuple[Chapter, ...]
    faststart: bool | None = None  # MP4/MOV only: moov before mdat
    raw: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @property
    def video(self) -> VideoStream | None:
        """The main video stream: the one flagged default, else the first."""
        if not self.video_streams:
            return None
        return next((v for v in self.video_streams if v.default), self.video_streams[0])

    @property
    def default_audio(self) -> AudioStream | None:
        if not self.audio:
            return None
        return next((a for a in self.audio if a.default), self.audio[0])

    @property
    def video_bitrate(self) -> int | None:
        """The video stream's bitrate, else an upper bound from the whole file."""
        video = self.video
        if video is not None and video.bitrate:
            return video.bitrate
        if self.bitrate:
            return self.bitrate
        if self.size and self.duration_ms:
            return self.size * 8 * 1000 // self.duration_ms
        return None

    def summary(self) -> dict[str, Any]:
        """The fields `MediaFile` stores in columns, as plain JSON-able values."""
        video = self.video
        return {
            "container": self.container,
            "duration_ms": self.duration_ms,
            "bitrate": self.bitrate,
            "video_codec": video.codec if video else None,
            "video_profile": video.profile if video else None,
            "video_level": video.level if video else None,
            "width": video.width if video else None,
            "height": video.height if video else None,
            "fps": round(video.fps, 3) if video and video.fps else None,
            "pix_fmt": video.pix_fmt if video else None,
            "hdr": video.hdr.value if video else None,
            "audio": [
                {
                    "stream_index": a.index,
                    "codec": a.codec,
                    "channels": a.channels,
                    "language": a.language,
                    "title": a.title,
                    "default": a.default,
                    "forced": a.forced,
                }
                for a in self.audio
            ],
            "subtitles": [
                {
                    "stream_index": s.index,
                    "codec": s.codec,
                    "language": s.language,
                    "title": s.title,
                    "default": s.default,
                    "forced": s.forced,
                    "format": subtitle_format(s.codec),
                    "text": s.is_text,
                }
                for s in self.subtitles
            ],
        }


def input_url(path: str | os.PathLike[str]) -> str:
    """An ffmpeg input URL for a local file that no protocol prefix can hijack.

    Library file names are arbitrary ("Movie: Part 2.mkv"); the explicit `file:` protocol
    stops ffmpeg from reading a name like `concat:...` or `http:...` as a protocol.
    """
    return f"file:{Path(path).absolute()}"


def probe(
    path: str | os.PathLike[str], *, ffprobe: str = "ffprobe", timeout_s: float = 120.0
) -> ProbeResult:
    """Run ffprobe on a local file and parse the result."""
    file = Path(path)
    argv = [ffprobe, *FFPROBE_ARGS, "-i", input_url(file)]
    try:
        completed = subprocess.run(  # noqa: S603 - argv list, no shell
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ProbeError(f"ffprobe not found: {ffprobe}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ProbeError(f"ffprobe timed out after {timeout_s:g} s") from exc
    if completed.returncode != 0:
        detail = _redact(completed.stderr.strip().splitlines()[-1:] or ["no output"], file)
        raise ProbeError(f"ffprobe failed ({completed.returncode}): {detail[0]}")
    try:
        data = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ProbeError("ffprobe returned invalid JSON") from exc
    result = parse_probe(data, filename=file.name)
    if result.container in {"mp4", "mov"}:
        result = replace(result, faststart=mp4_faststart(file))
    return result


def parse_probe(data: Mapping[str, Any], *, filename: str | None = None) -> ProbeResult:
    """Map ffprobe's JSON to a `ProbeResult`. `filename` (no directories) only helps tell
    containers that share a demuxer apart (.webm/.mkv, .mov/.mp4)."""
    if not isinstance(data, Mapping):
        raise ProbeError("ffprobe JSON is not an object")
    fmt: Mapping[str, Any] = data.get("format") or {}
    streams: Sequence[Mapping[str, Any]] = data.get("streams") or []
    format_name = str(fmt.get("format_name") or "unknown")

    video: list[VideoStream] = []
    audio: list[AudioStream] = []
    subtitles: list[SubtitleStream] = []
    for stream in streams:
        kind = stream.get("codec_type")
        disposition: Mapping[str, Any] = stream.get("disposition") or {}
        if kind == "video" and not disposition.get("attached_pic"):
            parsed = _video(stream)
            if parsed is not None:
                video.append(parsed)
        elif kind == "audio":
            audio.append(_audio(stream))
        elif kind == "subtitle":
            subtitles.append(_subtitle(stream))

    duration_ms = _seconds_ms(fmt.get("duration"))
    if duration_ms is None:
        duration_ms = max(
            (d for d in (_stream_duration_ms(s) for s in streams) if d is not None), default=None
        )
    return ProbeResult(
        container=_container(format_name, filename),
        format_name=format_name,
        duration_ms=duration_ms,
        bitrate=_int(fmt.get("bit_rate")),
        size=_int(fmt.get("size")),
        video_streams=tuple(video),
        audio=tuple(audio),
        subtitles=tuple(subtitles),
        chapters=tuple(_chapters(data.get("chapters") or [])),
        raw=_without_filename(data),
    )


def mp4_faststart(path: str | os.PathLike[str]) -> bool | None:
    """True when the top-level `moov` box precedes `mdat` (progressive playback can start
    at once), False when it follows it, None when the file isn't a readable MP4."""
    try:
        with open(path, "rb") as handle:
            size = os.fstat(handle.fileno()).st_size
            offset = 0
            while offset + 8 <= size:
                handle.seek(offset)
                header = handle.read(16)
                box_size = int.from_bytes(header[0:4], "big")
                box_type = header[4:8]
                if box_type == b"moov":
                    return True
                if box_type == b"mdat":
                    return False
                if box_size == 1:
                    box_size = int.from_bytes(header[8:16], "big")
                elif box_size == 0:
                    return None
                if box_size < 8:
                    return None
                offset += box_size
    except OSError:
        return None
    return None


def subtitle_format(codec: str) -> str | None:
    """The `SubtitleTrack.format` value for a codec (srt|vtt|ass|pgs), when it has one."""
    return {
        "subrip": "srt",
        "srt": "srt",
        "webvtt": "vtt",
        "ass": "ass",
        "ssa": "ass",
        "mov_text": "srt",
        "hdmv_pgs_subtitle": "pgs",
    }.get(codec)


def normalize_language(value: object) -> str:
    """ISO 639-2 code for a stream language tag ('en', 'eng', 'en-US' -> 'eng')."""
    if not isinstance(value, str):
        return UNDETERMINED
    tag = value.strip().lower().replace("_", "-").split("-")[0]
    if len(tag) == 2:
        return ISO639_1_TO_2.get(tag, UNDETERMINED)
    if len(tag) == 3 and tag.isalpha():
        return tag
    return UNDETERMINED


def bcp47_language(code: str) -> str | None:
    """The BCP 47 tag for an ISO 639-2 code (HLS `LANGUAGE`), None when undetermined."""
    if not code or code == UNDETERMINED:
        return None
    return ISO639_2_TO_1.get(code, code)


# --- stream parsers ----------------------------------------------------------------------


def _video(stream: Mapping[str, Any]) -> VideoStream | None:
    width = _int(stream.get("width"))
    height = _int(stream.get("height"))
    if not width or not height:
        return None
    pix_fmt = _str(stream.get("pix_fmt"))
    dovi = _dovi(stream)
    return VideoStream(
        index=int(stream.get("index", 0)),
        codec=str(stream.get("codec_name") or "unknown"),
        profile=_str(stream.get("profile")),
        level=_positive(stream.get("level")),
        width=width,
        height=height,
        sar=_ratio(stream.get("sample_aspect_ratio")) or Fraction(1),
        frame_rate=_ratio(stream.get("avg_frame_rate")) or _ratio(stream.get("r_frame_rate")),
        pix_fmt=pix_fmt,
        bit_depth=_bit_depth(pix_fmt, stream.get("bits_per_raw_sample")),
        hdr=_hdr(stream, dovi is not None),
        color_primaries=_str(stream.get("color_primaries")),
        color_transfer=_str(stream.get("color_transfer")),
        color_space=_str(stream.get("color_space")),
        color_range=_str(stream.get("color_range")),
        field_order=_str(stream.get("field_order")),
        bitrate=_int(stream.get("bit_rate")) or _tag_bitrate(stream),
        codec_tag=_str(stream.get("codec_tag_string")),
        default=bool((stream.get("disposition") or {}).get("default")),
        dv_profile=_int(dovi.get("dv_profile")) if dovi else None,
        dv_bl_compat_id=_int(dovi.get("dv_bl_signal_compatibility_id")) if dovi else None,
    )


def _audio(stream: Mapping[str, Any]) -> AudioStream:
    disposition: Mapping[str, Any] = stream.get("disposition") or {}
    tags: Mapping[str, Any] = stream.get("tags") or {}
    return AudioStream(
        index=int(stream.get("index", 0)),
        codec=str(stream.get("codec_name") or "unknown"),
        profile=_str(stream.get("profile")),
        channels=_int(stream.get("channels")) or 0,
        channel_layout=_str(stream.get("channel_layout")),
        sample_rate=_int(stream.get("sample_rate")),
        bitrate=_int(stream.get("bit_rate")) or _tag_bitrate(stream),
        language=normalize_language(tags.get("language")),
        title=_str(tags.get("title")),
        default=bool(disposition.get("default")),
        forced=bool(disposition.get("forced")),
        commentary=bool(disposition.get("comment")),
    )


def _subtitle(stream: Mapping[str, Any]) -> SubtitleStream:
    disposition: Mapping[str, Any] = stream.get("disposition") or {}
    tags: Mapping[str, Any] = stream.get("tags") or {}
    return SubtitleStream(
        index=int(stream.get("index", 0)),
        codec=str(stream.get("codec_name") or "unknown"),
        language=normalize_language(tags.get("language")),
        title=_str(tags.get("title")),
        default=bool(disposition.get("default")),
        forced=bool(disposition.get("forced")),
        hearing_impaired=bool(disposition.get("hearing_impaired")),
    )


def _chapters(chapters: Sequence[Mapping[str, Any]]) -> list[Chapter]:
    parsed = []
    for chapter in chapters:
        start = _seconds_ms(chapter.get("start_time"))
        end = _seconds_ms(chapter.get("end_time"))
        if start is None or end is None:
            continue
        title = _str((chapter.get("tags") or {}).get("title"))
        parsed.append(Chapter(start_ms=start, end_ms=end, title=title))
    return parsed


def _dovi(stream: Mapping[str, Any]) -> Mapping[str, Any] | None:
    for side_data in stream.get("side_data_list") or []:
        if side_data.get("side_data_type") == "DOVI configuration record":
            return side_data  # type: ignore[no-any-return]
    return None


def _hdr(stream: Mapping[str, Any], has_dovi: bool) -> HdrKind:
    tag = (_str(stream.get("codec_tag_string")) or "").lower()
    if has_dovi or tag in DOLBY_VISION_TAGS:
        return HdrKind.DV
    transfer = stream.get("color_transfer")
    if transfer == "smpte2084":
        return HdrKind.HDR10
    if transfer == "arib-std-b67":
        return HdrKind.HLG
    return HdrKind.SDR


def _container(format_name: str, filename: str | None) -> str:
    names = format_name.split(",")
    suffix = Path(filename).suffix.lower() if filename else ""
    if "matroska" in names or "webm" in names:
        return "webm" if suffix == ".webm" else "mkv"
    if "mov" in names or "mp4" in names:
        return "mov" if suffix == ".mov" else "mp4"
    if "mpegts" in names:
        return "ts"
    if "avi" in names:
        return "avi"
    return names[0]


def _without_filename(data: Mapping[str, Any]) -> dict[str, Any]:
    raw = dict(data)
    fmt = raw.get("format")
    if isinstance(fmt, Mapping):
        raw["format"] = {key: value for key, value in fmt.items() if key != "filename"}
    return raw


def _bit_depth(pix_fmt: str | None, bits_per_raw_sample: object) -> int:
    if pix_fmt:
        if pix_fmt.startswith(("p010", "p210", "p410")):
            return 10
        if pix_fmt.startswith(("p012", "p212", "p412")):
            return 12
        if pix_fmt.startswith(("p016", "p216", "p416")):
            return 16
        match = _BIT_DEPTH.search(pix_fmt)
        if match and match.group(1) in {"09", "10", "12", "14", "16"}:
            return int(match.group(1))
    depth = _int(bits_per_raw_sample)
    return depth if depth and depth > 8 else 8


def _stream_duration_ms(stream: Mapping[str, Any]) -> int | None:
    duration = _seconds_ms(stream.get("duration"))
    if duration is not None:
        return duration
    tags: Mapping[str, Any] = stream.get("tags") or {}
    for key, value in tags.items():
        if str(key).upper().startswith("DURATION"):
            return _clock_ms(value)
    return None


def _tag_bitrate(stream: Mapping[str, Any]) -> int | None:
    """mkvmerge's statistics tags (`BPS`, `BPS-eng`) when the stream has no bit_rate."""
    tags: Mapping[str, Any] = stream.get("tags") or {}
    for key, value in tags.items():
        if str(key).upper() in {"BPS", "BPS-ENG"}:
            return _int(value)
    return None


def _clock_ms(value: object) -> int | None:
    """'01:23:45.678000000' -> milliseconds."""
    if not isinstance(value, str):
        return None
    parts = value.strip().split(":")
    if len(parts) != 3:
        return None
    try:
        hours, minutes = int(parts[0]), int(parts[1])
        seconds = float(parts[2])
    except ValueError:
        return None
    return round((hours * 3600 + minutes * 60 + seconds) * 1000)


def _seconds_ms(value: object) -> int | None:
    try:
        seconds = float(str(value))
    except ValueError:
        return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return round(seconds * 1000)


def _ratio(value: object) -> Fraction | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        if ":" in value:
            num, den = value.split(":", 1)
            ratio = Fraction(int(num), int(den))
        else:
            ratio = Fraction(value)
    except (ValueError, ZeroDivisionError):
        return None
    return ratio if ratio > 0 else None


def _int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(str(value))
    except ValueError:
        try:
            return int(float(str(value)))
        except (ValueError, OverflowError):  # "N/A", "nan", "inf"
            return None


def _positive(value: object) -> int | None:
    number = _int(value)
    return number if number is not None and number > 0 else None


def _str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text and text != "unknown" else None


def _redact(lines: Sequence[str], path: Path) -> list[str]:
    """Strip the input path from ffprobe's messages (storage paths are never exposed)."""
    full = str(path.absolute())
    return [line.replace(f"file:{full}", "<input>").replace(full, "<input>") for line in lines]
