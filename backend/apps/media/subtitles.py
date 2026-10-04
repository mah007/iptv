"""Subtitles (SPEC §7.3): sidecar discovery, character-encoding detection and track sync.

Text subtitles reach players as UTF-8 WebVTT (HLS, the portal) and SRT, and as mov_text
inside the compat MP4. They come from three places:

- **embedded** text streams of the video file, extracted with ffmpeg;
- **sidecars** next to the video in its library: `Film.2010.ar.srt`, `Film.2010.en.forced.ass`,
  or files in a `Subs/` folder beside it;
- **uploads** an admin adds to a title.

Sidecars and uploads come in whatever encoding their author used. Arabic subtitles are
often Windows-1256 (cp1256) without a BOM; `decode()` keeps valid UTF-8 as it is, honours
BOMs, and otherwise asks charset-normalizer, trying the code pages usual for the
track's language first. Nothing here downloads subtitles: that stays off behind
`features.subtitle_download`, which this module never reads.
"""

from __future__ import annotations

import codecs
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import xxhash
from charset_normalizer import from_bytes
from django.db import transaction

from apps.catalog.models import MediaFile
from apps.media.models import (
    SUBTITLE_MAX_BYTES,
    AudioTrack,
    SubtitleFormat,
    SubtitleStatus,
    SubtitleTrack,
)
from apps.media.probe import (
    ISO639_1_TO_2,
    ISO639_2_TO_1,
    UNDETERMINED,
    ProbeResult,
    SubtitleStream,
)

SIDECAR_EXTENSIONS: Final = {
    ".srt": SubtitleFormat.SRT,
    ".vtt": SubtitleFormat.VTT,
    ".ass": SubtitleFormat.ASS,
    ".ssa": SubtitleFormat.ASS,
}
#: Folders beside a video that hold its subtitles (compared case-insensitively).
SIDECAR_FOLDERS: Final = frozenset({"subs", "subtitles", "sub", "subtitle"})
IMAGE_FORMATS: Final = {
    "hdmv_pgs_subtitle": SubtitleFormat.PGS,
    "dvd_subtitle": SubtitleFormat.VOBSUB,
    "dvb_subtitle": SubtitleFormat.VOBSUB,
    "xsub": SubtitleFormat.VOBSUB,
}
TEXT_FORMATS: Final = {
    "subrip": SubtitleFormat.SRT,
    "srt": SubtitleFormat.SRT,
    "webvtt": SubtitleFormat.VTT,
    "ass": SubtitleFormat.ASS,
    "ssa": SubtitleFormat.ASS,
    "mov_text": SubtitleFormat.SRT,
}
#: Code pages tried first for a language (ISO 639-2), before an open detection.
LANGUAGE_ENCODINGS: Final[dict[str, tuple[str, ...]]] = {
    "ara": ("cp1256", "iso8859_6"),
    "per": ("cp1256",),
    "fas": ("cp1256",),
    "urd": ("cp1256",),
    "heb": ("cp1255", "iso8859_8"),
    "rus": ("cp1251", "koi8_r"),
    "ukr": ("cp1251", "koi8_u"),
    "bul": ("cp1251",),
    "gre": ("cp1253", "iso8859_7"),
    "ell": ("cp1253", "iso8859_7"),
    "tur": ("cp1254", "iso8859_9"),
    "pol": ("cp1250", "iso8859_2"),
    "cze": ("cp1250", "iso8859_2"),
    "ces": ("cp1250", "iso8859_2"),
    "hun": ("cp1250", "iso8859_2"),
    "rum": ("cp1250", "iso8859_16"),
    "ron": ("cp1250", "iso8859_16"),
    "tha": ("cp874",),
    "vie": ("cp1258",),
    "jpn": ("shift_jis", "euc_jp"),
    "chi": ("gb18030", "big5"),
    "zho": ("gb18030", "big5"),
    "kor": ("euc_kr",),
    "eng": ("cp1252", "iso8859_15"),
    "fre": ("cp1252", "iso8859_15"),
    "fra": ("cp1252", "iso8859_15"),
    "ger": ("cp1252", "iso8859_15"),
    "deu": ("cp1252", "iso8859_15"),
    "spa": ("cp1252", "iso8859_15"),
    "ita": ("cp1252", "iso8859_15"),
    "por": ("cp1252", "iso8859_15"),
    "dut": ("cp1252", "iso8859_15"),
    "nld": ("cp1252", "iso8859_15"),
}
#: charset-normalizer's "chaos" above which a hinted decode is not trusted.
MAX_CHAOS: Final = 0.2
LANGUAGE_WORDS: Final = {
    "arabic": "ara",
    "english": "eng",
    "french": "fre",
    "german": "ger",
    "spanish": "spa",
    "italian": "ita",
    "portuguese": "por",
    "russian": "rus",
    "turkish": "tur",
    "persian": "per",
    "farsi": "per",
    "urdu": "urd",
    "hindi": "hin",
    "indonesian": "ind",
    "malay": "may",
    "dutch": "dut",
    "hebrew": "heb",
    "japanese": "jpn",
    "korean": "kor",
    "chinese": "chi",
}
KNOWN_LANGUAGES: Final = frozenset(ISO639_1_TO_2.values()) | frozenset(ISO639_2_TO_1)
FORCED_WORDS: Final = frozenset({"forced", "foreign"})
HEARING_IMPAIRED_WORDS: Final = frozenset({"sdh", "hi", "cc"})
DEFAULT_WORDS: Final = frozenset({"default"})
_BOMS: Final = (
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)
_CUE = re.compile(r"^\s*(?:\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{3}\s+-->", re.MULTILINE)


class SubtitleError(ValueError):
    """A subtitle that cannot be read; the message is a stable code."""


@dataclass(frozen=True, slots=True)
class Decoded:
    text: str  # Unicode, LF line ends, no BOM or NULs
    encoding: str  # what the bytes were: utf-8, cp1256, ...


@dataclass(frozen=True, slots=True)
class Sidecar:
    path: Path
    relative: str  # inside its library, for the admin
    format: SubtitleFormat
    language: str
    forced: bool
    hearing_impaired: bool
    default: bool


# --- Encodings ----------------------------------------------------------------------------


def decode(data: bytes, language: str | None = None) -> Decoded:
    """Text of a subtitle file in any encoding (see the module docstring)."""
    if not data.strip():
        raise SubtitleError("empty")
    for bom, encoding in _BOMS:
        if data.startswith(bom):
            return Decoded(_clean(data.decode(encoding)), encoding.removesuffix("-sig"))
    try:
        return Decoded(_clean(data.decode("utf-8")), "utf-8")
    except UnicodeDecodeError:
        pass
    hinted = LANGUAGE_ENCODINGS.get(language or "", ())
    if hinted:
        best = from_bytes(data, cp_isolation=list(hinted)).best()
        if best is not None and best.chaos <= MAX_CHAOS:
            return Decoded(_clean(str(best)), _codec_name(best.encoding))
    best = from_bytes(data).best()
    if best is None:
        raise SubtitleError("unknown_encoding")
    return Decoded(_clean(str(best)), _codec_name(best.encoding))


def _codec_name(name: str) -> str:
    """Python's canonical spelling, `cp1256` rather than `windows-1256`."""
    try:
        return codecs.lookup(name).name
    except LookupError:
        return name


def _clean(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "").lstrip("﻿")


def count_cues(vtt_text: str) -> int:
    return len(_CUE.findall(vtt_text))


def content_hash(data: bytes) -> str:
    return xxhash.xxh64_hexdigest(data)


# --- Sidecars -----------------------------------------------------------------------------


def find_sidecars(video: Path, library_dir: Path) -> list[Sidecar]:
    """Subtitle files that belong to `video`: beside it and named after it, or in a
    `Subs/` folder beside it (named after it, or any file there when the video is alone
    in its folder)."""
    folder = video.parent
    stem = video.stem
    try:
        entries = sorted(folder.iterdir())
    except OSError:
        return []
    alone = sum(1 for e in entries if e.is_file() and e.suffix.lower() in _VIDEO_SUFFIXES) <= 1
    candidates = [e for e in entries if _is_subtitle(e) and _named_after(e.name, stem)]
    for sub in entries:
        if sub.is_dir() and sub.name.lower() in SIDECAR_FOLDERS:
            try:
                inside = sorted(sub.iterdir())
            except OSError:
                continue
            candidates += [
                e for e in inside if _is_subtitle(e) and (alone or _named_after(e.name, stem))
            ]
    found = []
    for path in candidates:
        tokens = _tags(path, stem)
        try:
            relative = str(path.relative_to(library_dir))
        except ValueError:
            continue
        found.append(
            Sidecar(
                path=path,
                relative=relative,
                format=SIDECAR_EXTENSIONS[path.suffix.lower()],
                language=_language(tokens),
                forced=bool(tokens & FORCED_WORDS),
                hearing_impaired=bool(tokens & HEARING_IMPAIRED_WORDS),
                default=bool(tokens & DEFAULT_WORDS),
            )
        )
    return found


_VIDEO_SUFFIXES: Final = frozenset(
    {".mkv", ".mp4", ".m4v", ".mov", ".avi", ".ts", ".m2ts", ".webm"}
)


def _is_subtitle(path: Path) -> bool:
    return path.suffix.lower() in SIDECAR_EXTENSIONS and path.is_file()


def _named_after(name: str, stem: str) -> bool:
    lowered, wanted = name.lower(), stem.lower()
    return lowered.startswith(wanted + ".") or lowered.startswith(wanted + "_")


def _tags(path: Path, stem: str) -> set[str]:
    """Lower-case tokens of the name besides the video's stem and the extension."""
    name = path.stem
    if name.lower().startswith(stem.lower()):
        name = name[len(stem) :]
    return {t for t in re.split(r"[.\s_\-\[\]()]+", name.lower()) if t}


def _language(tokens: Iterable[str]) -> str:
    for token in tokens:
        if token in LANGUAGE_WORDS:
            return LANGUAGE_WORDS[token]
        if len(token) == 2 and token in ISO639_1_TO_2:
            return ISO639_1_TO_2[token]
        if len(token) == 3 and token in KNOWN_LANGUAGES:
            return token
    return UNDETERMINED


def read_limited(path: Path) -> bytes:
    """A sidecar's bytes; refuses files over `SUBTITLE_MAX_BYTES`."""
    if path.stat().st_size > SUBTITLE_MAX_BYTES:
        raise SubtitleError("too_large")
    return path.read_bytes()


# --- Track rows ---------------------------------------------------------------------------


def subtitle_format(codec: str) -> SubtitleFormat:
    return IMAGE_FORMATS.get(codec) or TEXT_FORMATS.get(codec) or SubtitleFormat.OTHER


def embedded_key(stream: SubtitleStream) -> str:
    """File stem of an extracted embedded subtitle (`3.eng`)."""
    return f"{stream.index}.{stream.language}"


def sync_tracks(file: MediaFile, result: ProbeResult, sidecars: Sequence[Sidecar]) -> bool:
    """Bring the file's track rows in line with a probe and its sidecars.

    New streams and sidecars get rows; vanished ones lose theirs; an admin's edits
    (language, title, default, forced) stay. A sidecar whose bytes changed goes back to
    `pending`. Returns True when a text subtitle waits for conversion.
    """
    with transaction.atomic():
        _sync_audio(file, result)
        _sync_embedded(file, result)
        _sync_sidecars(file, sidecars)
        return SubtitleTrack.objects.filter(media_file=file, status=SubtitleStatus.PENDING).exists()


def _sync_audio(file: MediaFile, result: ProbeResult) -> None:
    existing = {t.stream_index: t for t in AudioTrack.objects.filter(media_file=file)}
    streams = {a.index: a for a in result.audio}
    AudioTrack.objects.filter(media_file=file).exclude(stream_index__in=list(streams)).delete()
    default = result.default_audio
    for index, stream in streams.items():
        track = existing.get(index)
        if track is None:
            AudioTrack.objects.create(
                media_file=file,
                stream_index=index,
                language=stream.language,
                codec=stream.codec,
                channels=stream.channels,
                title=(stream.title or "")[:200],
                default=stream is default,
                forced=stream.forced,
                commentary=stream.commentary,
            )
        elif (track.codec, track.channels) != (stream.codec, stream.channels):
            track.codec, track.channels = stream.codec, stream.channels
            track.save(update_fields=["codec", "channels", "updated_at"])


def _sync_embedded(file: MediaFile, result: ProbeResult) -> None:
    existing = {
        t.stream_index: t
        for t in SubtitleTrack.objects.filter(media_file=file, stream_index__isnull=False)
    }
    streams = {s.index: s for s in result.subtitles}
    SubtitleTrack.objects.filter(media_file=file, stream_index__isnull=False).exclude(
        stream_index__in=list(streams)
    ).delete()
    for index, stream in streams.items():
        track = existing.get(index)
        fmt = subtitle_format(stream.codec)
        if track is None:
            SubtitleTrack.objects.create(
                media_file=file,
                stream_index=index,
                codec=stream.codec,
                format=fmt,
                language=stream.language,
                title=(stream.title or "")[:200],
                default=stream.default,
                forced=stream.forced,
                hearing_impaired=stream.hearing_impaired,
                status=SubtitleStatus.PENDING if stream.is_text else SubtitleStatus.UNSUPPORTED,
            )
        elif track.codec != stream.codec:
            track.codec, track.format = stream.codec, fmt
            track.status = SubtitleStatus.PENDING if stream.is_text else SubtitleStatus.UNSUPPORTED
            track.save(update_fields=["codec", "format", "status", "updated_at"])


def _sync_sidecars(file: MediaFile, sidecars: Sequence[Sidecar]) -> None:
    rows = SubtitleTrack.objects.filter(media_file=file, external=True).exclude(sidecar_path="")
    existing = {t.sidecar_path: t for t in rows}
    wanted = {s.relative: s for s in sidecars}
    rows.exclude(sidecar_path__in=list(wanted)).delete()
    for relative, sidecar in wanted.items():
        try:
            digest = content_hash(read_limited(sidecar.path))
        except (OSError, SubtitleError):
            digest = ""
        track = existing.get(relative)
        if track is None:
            SubtitleTrack.objects.create(
                media_file=file,
                external=True,
                sidecar_path=relative[:1024],
                codec=sidecar.format.value,
                format=sidecar.format,
                language=sidecar.language,
                default=sidecar.default,
                forced=sidecar.forced,
                hearing_impaired=sidecar.hearing_impaired,
                status=SubtitleStatus.PENDING,
            )
        elif digest != track.source_hash and track.status != SubtitleStatus.PENDING:
            track.status = SubtitleStatus.PENDING
            track.save(update_fields=["status", "updated_at"])


def next_external_key(file: MediaFile, language: str) -> str:
    """A fresh `subs/` stem for a sidecar or upload: `x1.ara`, `x2.eng`, ..."""
    used = set(
        SubtitleTrack.objects.filter(media_file=file).exclude(storage_key="")
        .values_list("storage_key", flat=True)
    )  # fmt: skip
    number = 1
    while any(key.startswith(f"x{number}.") for key in used):
        number += 1
    return f"x{number}.{language or UNDETERMINED}"
