"""HLS master playlists (SPEC §7.3, RFC 8216): codec strings, measured bandwidths and
the master text for any set of variants, audio and subtitle renditions.

`apps.media.presentations` uses these to write every presentation of a file (the full
ladder, capped ladders and the UHD one) from the facts stored on its renditions, so a
master can be rewritten (a new subtitle, another default audio track) without
touching a segment.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from apps.media.probe import bcp47_language
from apps.media.verify import ceil_target_duration, read_media_playlist

#: HLS CODECS values for the audio we package (ffmpeg's AAC encoder writes AAC-LC).
AUDIO_CODEC_STRINGS: Final = {"aac": "mp4a.40.2", "ac3": "ac-3", "eac3": "ec-3"}
#: Fallback video CODECS values when an init segment cannot be read.
FALLBACK_CODECS: Final = {"h264": "avc1.640029", "hevc": "hvc1.2.4.L150.B0", "av1": "av01.0.12M.10"}
#: Names players show for common subtitle and audio languages (ISO 639-2 -> native name).
LANGUAGE_NAMES: Final = {
    "ara": "العربية",
    "eng": "English",
    "fre": "Français",
    "fra": "Français",
    "ger": "Deutsch",
    "deu": "Deutsch",
    "spa": "Español",
    "ita": "Italiano",
    "por": "Português",
    "rus": "Русский",
    "tur": "Türkçe",
    "per": "فارسی",
    "fas": "فارسی",
    "urd": "اردو",
    "hin": "हिन्दी",
    "ind": "Bahasa Indonesia",
    "may": "Bahasa Melayu",
    "jpn": "日本語",
    "kor": "한국어",
    "chi": "中文",
    "zho": "中文",
    "heb": "עברית",
}
_HEVC_PROFILE_SPACES: Final = ("", "A", "B", "C")


@dataclass(frozen=True, slots=True)
class VariantEntry:
    uri: str  # relative to the master: `v0/index.m3u8`
    width: int
    height: int
    frame_rate: float | None
    codecs: str  # the video codec string
    peak_bps: int
    average_bps: int
    video_range: str = "SDR"  # SDR | PQ | HLG
    default: bool = False


@dataclass(frozen=True, slots=True)
class AudioEntry:
    uri: str
    group: str  # GROUP-ID: `aac`, or the passthrough codec (`eac3`)
    language: str  # ISO 639-2
    name: str
    default: bool
    channels: int
    codec: str
    peak_bps: int
    average_bps: int


@dataclass(frozen=True, slots=True)
class SubtitleEntry:
    uri: str
    language: str  # ISO 639-2
    name: str
    default: bool
    forced: bool


def language_name(code: str, title: str = "") -> str:
    """What a player's track menu shows: the track's title, else the language's name."""
    return title.strip() or LANGUAGE_NAMES.get(code, code or "und")


# --- Facts read from packaged output ---------------------------------------------------------


def playlist_bitrates(playlist_path: Path) -> tuple[int, int]:
    """(peak, average) segment bitrate in bit/s of one media playlist."""
    playlist = read_media_playlist(playlist_path)
    total_bits = 0
    total_s = 0.0
    peak = 0
    for segment in playlist.segments:
        bits = (playlist_path.parent / segment.uri).stat().st_size * 8
        total_bits += bits
        total_s += segment.duration_s
        if segment.duration_s > 0:
            peak = max(peak, math.ceil(bits / segment.duration_s))
    average = math.ceil(total_bits / total_s) if total_s > 0 else 0
    return peak, average


def video_codec_string(init_segment: Path, codec: str = "h264") -> str:
    """The RFC 6381 codec string of the video in an fMP4 init segment (ISO/IEC 14496-15
    annex E): `avc1.PPCCLL` from avcC, `hvc1.<space><profile>.<compat>.<tier><level>.<cn>`
    from hvcC, the fallback for `codec` when neither box can be read."""
    try:
        data = init_segment.read_bytes()
    except OSError:
        data = b""
    position = data.find(b"avcC")
    if position >= 0 and len(data) >= position + 8:
        return "avc1." + data[position + 5 : position + 8].hex()
    position = data.find(b"hvcC")
    if position >= 0 and len(data) >= position + 17:
        sample_entry = "hev1" if b"hev1" in data[:position] else "hvc1"
        return hevc_codec_string(data[position + 4 : position + 17], sample_entry)
    return FALLBACK_CODECS.get(codec, FALLBACK_CODECS["h264"])


def hevc_codec_string(record: bytes, sample_entry: str = "hvc1") -> str:
    """The codec string of an HEVCDecoderConfigurationRecord's first 13 bytes."""
    profile_byte = record[1]
    space = _HEVC_PROFILE_SPACES[profile_byte >> 6]
    tier = "H" if profile_byte & 0x20 else "L"
    profile_idc = profile_byte & 0x1F
    compatibility = int.from_bytes(record[2:6], "big")
    reversed_flags = int(f"{compatibility:032b}"[::-1], 2)
    constraints = list(record[6:12])
    while constraints and constraints[-1] == 0:
        constraints.pop()
    level = record[12]
    parts = [sample_entry, f"{space}{profile_idc}", f"{reversed_flags:X}", f"{tier}{level}"]
    parts += [f"{byte:02X}" for byte in constraints]
    return ".".join(parts)


def audio_codec_string(codec: str) -> str:
    return AUDIO_CODEC_STRINGS.get(codec, codec)


# --- Text -------------------------------------------------------------------------------------


def master_playlist(
    variants: Sequence[VariantEntry],
    audio: Sequence[AudioEntry] = (),
    subtitles: Sequence[SubtitleEntry] = (),
) -> str:
    """A VOD master playlist: EXT-X-MEDIA for audio and subtitles, then one
    EXT-X-STREAM-INF per variant per audio group, the default variant first.

    BANDWIDTH is the variant's peak segment bitrate plus its audio group's largest peak,
    AVERAGE-BANDWIDTH the same with averages (RFC 8216 4.3.4.2).
    """
    if not variants:
        raise ValueError("a master playlist needs at least one variant")
    lines = ["#EXTM3U", "#EXT-X-VERSION:7", "#EXT-X-INDEPENDENT-SEGMENTS", ""]
    groups: dict[str, list[AudioEntry]] = {}
    for entry in audio:
        groups.setdefault(entry.group, []).append(entry)
    for group, members in groups.items():
        names: set[str] = set()
        default_seen = False
        for entry in members:
            is_default = entry.default and not default_seen
            default_seen = default_seen or is_default
            attributes = [
                "TYPE=AUDIO",
                f'GROUP-ID="{group}"',
                *_language(entry.language),
                f'NAME="{_unique(_quoted(entry.name), names)}"',
                f"DEFAULT={_yes(is_default)}",
                "AUTOSELECT=YES",
                f'CHANNELS="{entry.channels}"',
                f'URI="{entry.uri}"',
            ]
            lines.append("#EXT-X-MEDIA:" + ",".join(attributes))
    names = set()
    default_seen = False
    for subtitle in subtitles:
        is_default = subtitle.default and not default_seen
        default_seen = default_seen or is_default
        attributes = [
            "TYPE=SUBTITLES",
            'GROUP-ID="subs"',
            *_language(subtitle.language),
            f'NAME="{_unique(_quoted(subtitle.name), names)}"',
            f"DEFAULT={_yes(is_default)}",
            f"AUTOSELECT={_yes(is_default or not subtitle.forced)}",
            f"FORCED={_yes(subtitle.forced)}",
            f'URI="{subtitle.uri}"',
        ]
        lines.append("#EXT-X-MEDIA:" + ",".join(attributes))
    if len(lines) > 4:
        lines.append("")
    ordered = sorted(variants, key=lambda v: (not v.default, -v.height, v.peak_bps))
    names_of_groups: list[str | None] = [*groups] or [None]
    for variant_group in names_of_groups:
        members = groups.get(variant_group, []) if variant_group else []
        audio_peak = max((entry.peak_bps for entry in members), default=0)
        audio_average = max((entry.average_bps for entry in members), default=0)
        audio_codec = audio_codec_string(members[0].codec) if members else None
        for variant in ordered:
            codecs = ",".join(c for c in (variant.codecs, audio_codec) if c)
            attributes = [
                f"BANDWIDTH={variant.peak_bps + audio_peak}",
                f"AVERAGE-BANDWIDTH={variant.average_bps + audio_average}",
                f'CODECS="{codecs}"',
                f"RESOLUTION={variant.width}x{variant.height}",
            ]
            if variant.frame_rate:
                attributes.append(f"FRAME-RATE={variant.frame_rate:.3f}")
            attributes.append(f"VIDEO-RANGE={variant.video_range}")
            if variant_group is not None:
                attributes.append(f'AUDIO="{variant_group}"')
            if subtitles:
                attributes.append('SUBTITLES="subs"')
            lines.append("#EXT-X-STREAM-INF:" + ",".join(attributes))
            lines.append(variant.uri)
    return "\n".join(lines) + "\n"


def subtitle_playlist(vtt_name: str, duration_s: float) -> str:
    """A one-segment VOD media playlist for a whole-film WebVTT file."""
    return "\n".join(
        [
            "#EXTM3U",
            "#EXT-X-VERSION:3",
            f"#EXT-X-TARGETDURATION:{ceil_target_duration([duration_s])}",
            "#EXT-X-MEDIA-SEQUENCE:0",
            "#EXT-X-PLAYLIST-TYPE:VOD",
            f"#EXTINF:{duration_s:.3f},",
            vtt_name,
            "#EXT-X-ENDLIST",
            "",
        ]
    )


def _language(code: str) -> list[str]:
    tag = bcp47_language(code)
    return [f'LANGUAGE="{tag}"'] if tag else []


def _quoted(value: str) -> str:
    """HLS quoted strings cannot hold double quotes or line breaks."""
    return value.replace('"', "'").replace("\n", " ").replace("\r", " ")


def _yes(flag: bool) -> str:
    return "YES" if flag else "NO"


def _unique(name: str, used: set[str]) -> str:
    candidate, suffix = name, 2
    while candidate in used:
        candidate = f"{name} {suffix}"
        suffix += 1
    used.add(candidate)
    return candidate
