"""Typed loader for the transcoding profiles (`streaming/ffmpeg/profiles.yaml`, SPEC §7.3).

The YAML is validated strictly: unknown keys, wrong types, out-of-range numbers and
unknown `{placeholders}` in argument templates all raise `ProfileError` at load time,
so a typo stops the worker at start instead of failing an encode halfway through.
"""

from __future__ import annotations

import os
import string
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from pathlib import Path
from typing import Any, Final

import yaml

PROFILES_ENV: Final = "MEDIA_PROFILES_PATH"
# Repository layout: backend/apps/media/profiles.py -> <repo>/streaming/ffmpeg/profiles.yaml.
# Images that don't keep this layout set MEDIA_PROFILES_PATH.
DEFAULT_PROFILES_PATH: Final = (
    Path(__file__).resolve().parents[3] / "streaming" / "ffmpeg" / "profiles.yaml"
)
SUPPORTED_VERSION: Final = 1

QUALITY_PLACEHOLDERS: Final = frozenset({"quality", "maxrate", "bufsize", "target"})
BITRATE_PLACEHOLDERS: Final = frozenset({"bitrate", "maxrate", "bufsize"})
GOP_PLACEHOLDERS: Final = frozenset({"gop"})
DEVICE_PLACEHOLDERS: Final = frozenset({"device"})


class ProfileError(ValueError):
    """The profiles file is missing, unreadable or invalid."""


class Backend(StrEnum):
    """Encoding backends; each one has its own Celery queue (`transcode.<backend>`)."""

    NVENC = "nvenc"
    QSV = "qsv"
    VAAPI = "vaapi"
    CPU = "cpu"


class VideoCodec(StrEnum):
    """Video codecs we encode to."""

    H264 = "h264"
    HEVC = "hevc"


@dataclass(frozen=True, slots=True)
class EncoderPreset:
    """How one backend encodes one codec."""

    encoder: str
    pix_fmt: str
    pix_fmt_10bit: str | None
    args: tuple[str, ...]
    quality: float
    quality_args: tuple[str, ...]
    bitrate_args: tuple[str, ...]
    gop_args: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BackendProfile:
    name: Backend
    queue: str
    input_args: tuple[str, ...]
    upload_filter: str | None
    encoders: Mapping[VideoCodec, EncoderPreset]

    def encoder(self, codec: VideoCodec) -> EncoderPreset | None:
        return self.encoders.get(codec)


@dataclass(frozen=True, slots=True)
class CompatVideo:
    codec: VideoCodec
    profile: str
    level: str
    maxrate_k: int
    bufsize_k: int
    target_k: int


@dataclass(frozen=True, slots=True)
class CopyVideoRule:
    """When the source video may be stream-copied into the compat MP4."""

    codecs: frozenset[str]
    profiles: frozenset[str]
    max_level: int
    pix_fmts: frozenset[str]


@dataclass(frozen=True, slots=True)
class AudioEncoding:
    codec: str
    bitrate_k: int
    channels: int
    copy_codecs: frozenset[str]


@dataclass(frozen=True, slots=True)
class CompatMp4Profile:
    max_width: int
    max_height: int
    video: CompatVideo
    copy_video_when: CopyVideoRule
    stereo: AudioEncoding
    surround: AudioEncoding
    subtitle_codec: str
    movflags: str


@dataclass(frozen=True, slots=True)
class HlsRung:
    name: str
    width: int
    height: int
    bitrate_k: int


@dataclass(frozen=True, slots=True)
class HlsVideo:
    codec: VideoCodec
    profile: str
    level: str


@dataclass(frozen=True, slots=True)
class HlsProfile:
    segment_s: int
    default_rung: str
    rungs: tuple[HlsRung, ...]  # tallest first
    native_rung_ratio: float
    maxrate_ratio: float
    bufsize_ratio: float
    video: HlsVideo
    audio: AudioEncoding
    passthrough_audio_codecs: frozenset[str]

    def rung(self, name: str) -> HlsRung | None:
        return next((rung for rung in self.rungs if rung.name == name), None)


@dataclass(frozen=True, slots=True)
class UhdKeepRule:
    codecs: frozenset[str]
    max_bitrate_k: int
    containers: frozenset[str]


@dataclass(frozen=True, slots=True)
class UhdVideo:
    codec: VideoCodec
    profile: str
    bitrate_k: int
    maxrate_k: int
    bufsize_k: int


@dataclass(frozen=True, slots=True)
class UhdProfile:
    min_source_height: int
    max_width: int
    max_height: int
    keep_source_when: UhdKeepRule
    video: UhdVideo


@dataclass(frozen=True, slots=True)
class SpriteProfile:
    interval_s: int
    tile_width: int
    tile_height: int
    columns: int
    rows: int

    @property
    def tiles_per_sheet(self) -> int:
        return self.columns * self.rows


@dataclass(frozen=True, slots=True)
class ThumbnailProfile:
    poster_at_ratio: float
    poster_max_height: int
    sprite: SpriteProfile


@dataclass(frozen=True, slots=True)
class FilterProfile:
    tonemap: str
    deinterlace: str


@dataclass(frozen=True, slots=True)
class LiveTranscode:
    """The capped real-time fallback for live sources that cannot be copied (ADR-0017)."""

    max_height: int = 720
    video_k: int = 2500
    #: libx264 arguments; placeholders {bitrate} {maxrate} {bufsize} {gop}.
    video_args: tuple[str, ...] = (
        "-c:v", "libx264", "-preset", "veryfast", "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-b:v", "{bitrate}", "-maxrate", "{maxrate}", "-bufsize", "{bufsize}",
        "-g", "{gop}", "-keyint_min", "{gop}", "-sc_threshold", "0",
    )  # fmt: skip
    audio_k: int = 128
    audio_channels: int = 2


@dataclass(frozen=True, slots=True)
class LiveProfile:
    """HLS packaging of live channels (ADR-0017): MPEG-TS segments in a rolling window."""

    segment_s: int = 4
    first_segment_s: int = 1
    list_size: int = 6
    transcode: LiveTranscode = LiveTranscode()


LIVE_TRANSCODE_PLACEHOLDERS = frozenset({"bitrate", "maxrate", "bufsize", "gop"})


@dataclass(frozen=True, slots=True)
class Profiles:
    version: int
    keyframe_interval_s: int
    compat_mp4: CompatMp4Profile
    hls: HlsProfile
    uhd: UhdProfile
    thumbnails: ThumbnailProfile
    filters: FilterProfile
    backend_preference: tuple[Backend, ...]
    backends: Mapping[Backend, BackendProfile]
    live: LiveProfile = LiveProfile()

    def backend(self, backend: Backend) -> BackendProfile:
        try:
            return self.backends[backend]
        except KeyError:
            raise ProfileError(f"backend {backend.value!r} is not configured") from None

    def encoder(self, backend: Backend, codec: VideoCodec) -> EncoderPreset:
        preset = self.backend(backend).encoder(codec)
        if preset is None:
            raise ProfileError(f"backend {backend.value!r} has no {codec.value} encoder")
        return preset


def render_args(template: Sequence[str], values: Mapping[str, str]) -> list[str]:
    """Fill `{placeholders}` in an argument template. Every placeholder must have a value."""
    try:
        return [part.format_map(values) for part in template]
    except KeyError as exc:
        raise ProfileError(f"no value for placeholder {{{exc.args[0]}}}") from None


def resolve_profiles_path() -> Path:
    """`$MEDIA_PROFILES_PATH` when set, else the repository's `streaming/ffmpeg/profiles.yaml`."""
    configured = os.environ.get(PROFILES_ENV, "").strip()
    return Path(configured) if configured else DEFAULT_PROFILES_PATH


def load_profiles(path: str | os.PathLike[str] | None = None) -> Profiles:
    """Read and validate a profiles file (default: `resolve_profiles_path()`)."""
    file = Path(path) if path is not None else resolve_profiles_path()
    try:
        text = file.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProfileError(f"cannot read {file}: {exc.strerror or exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ProfileError(f"{file.name}: invalid YAML: {exc}") from exc
    return parse_profiles(data, source=file.name)


@cache
def default_profiles() -> Profiles:
    """The validated default profiles, loaded once per process."""
    return load_profiles()


def parse_profiles(data: object, *, source: str = "profiles.yaml") -> Profiles:
    """Validate already-parsed YAML data and build `Profiles`."""
    root = _Node(data, source)
    version = root.integer("version", minimum=1)
    if version != SUPPORTED_VERSION:
        raise ProfileError(f"{source}: version: unsupported version {version}")
    profiles = Profiles(
        version=version,
        keyframe_interval_s=root.integer("keyframe_interval_s", minimum=1, maximum=10),
        compat_mp4=_compat_mp4(root.section("compat_mp4")),
        hls=_hls(root.section("hls")),
        uhd=_uhd(root.section("uhd")),
        thumbnails=_thumbnails(root.section("thumbnails")),
        filters=_filters(root.section("filters")),
        backend_preference=_preference(root),
        backends=_backends(root.section("backends")),
        live=_live(root.section("live")) if "live" in root.names() else LiveProfile(),
    )
    root.finish()
    for backend in profiles.backend_preference:
        if backend not in profiles.backends:
            raise ProfileError(f"{source}: backend_preference: {backend.value!r} is not configured")
    if Backend.CPU not in profiles.backends:
        raise ProfileError(f"{source}: backends: the cpu fallback backend is required")
    return profiles


# --- section parsers --------------------------------------------------------------------


def _compat_mp4(node: _Node) -> CompatMp4Profile:
    video = node.section("video")
    copy_rule = node.section("copy_video_when")
    audio = node.section("audio")
    profile = CompatMp4Profile(
        max_width=node.integer("max_width", minimum=16),
        max_height=node.integer("max_height", minimum=16),
        video=CompatVideo(
            codec=video.enum("codec", VideoCodec),
            profile=video.text("profile"),
            level=video.text("level"),
            maxrate_k=video.integer("maxrate_k", minimum=1),
            bufsize_k=video.integer("bufsize_k", minimum=1),
            target_k=video.integer("target_k", minimum=1),
        ),
        copy_video_when=CopyVideoRule(
            codecs=copy_rule.text_set("codecs"),
            profiles=frozenset(p.lower() for p in copy_rule.texts("profiles")),
            max_level=copy_rule.integer("max_level", minimum=1),
            pix_fmts=copy_rule.text_set("pix_fmts"),
        ),
        stereo=_audio(audio.section("stereo"), copy=True),
        surround=_audio(audio.section("surround"), copy=True),
        subtitle_codec=node.text("subtitle_codec"),
        movflags=node.text("movflags"),
    )
    video.finish()
    copy_rule.finish()
    audio.finish()
    node.finish()
    return profile


def _audio(node: _Node, *, copy: bool) -> AudioEncoding:
    encoding = AudioEncoding(
        codec=node.text("codec"),
        bitrate_k=node.integer("bitrate_k", minimum=8),
        channels=node.integer("channels", minimum=1, maximum=8),
        copy_codecs=node.text_set("copy_codecs") if copy else frozenset(),
    )
    node.finish()
    return encoding


def _hls(node: _Node) -> HlsProfile:
    rungs = []
    for item in node.items("rungs"):
        rungs.append(
            HlsRung(
                name=item.text("name"),
                width=item.integer("width", minimum=16),
                height=item.integer("height", minimum=16),
                bitrate_k=item.integer("bitrate_k", minimum=1),
            )
        )
        item.finish()
    if not rungs:
        raise ProfileError(f"{node.path}.rungs: at least one rung is required")
    names = [rung.name for rung in rungs]
    if len(set(names)) != len(names):
        raise ProfileError(f"{node.path}.rungs: rung names must be unique")
    rungs.sort(key=lambda rung: rung.height, reverse=True)
    video = node.section("video")
    profile = HlsProfile(
        segment_s=node.integer("segment_s", minimum=1, maximum=30),
        default_rung=node.text("default_rung"),
        rungs=tuple(rungs),
        native_rung_ratio=node.number("native_rung_ratio", minimum=1.0, maximum=4.0),
        maxrate_ratio=node.number("maxrate_ratio", minimum=1.0, maximum=3.0),
        bufsize_ratio=node.number("bufsize_ratio", minimum=0.5, maximum=4.0),
        video=HlsVideo(
            codec=video.enum("codec", VideoCodec),
            profile=video.text("profile"),
            level=video.text("level"),
        ),
        audio=_audio(node.section("audio"), copy=False),
        passthrough_audio_codecs=node.text_set("passthrough_audio_codecs"),
    )
    video.finish()
    node.finish()
    if profile.rung(profile.default_rung) is None:
        raise ProfileError(f"{node.path}.default_rung: {profile.default_rung!r} is not a rung")
    return profile


def _uhd(node: _Node) -> UhdProfile:
    keep = node.section("keep_source_when")
    video = node.section("video")
    profile = UhdProfile(
        min_source_height=node.integer("min_source_height", minimum=16),
        max_width=node.integer("max_width", minimum=16),
        max_height=node.integer("max_height", minimum=16),
        keep_source_when=UhdKeepRule(
            codecs=keep.text_set("codecs"),
            max_bitrate_k=keep.integer("max_bitrate_k", minimum=1),
            containers=keep.text_set("containers"),
        ),
        video=UhdVideo(
            codec=video.enum("codec", VideoCodec),
            profile=video.text("profile"),
            bitrate_k=video.integer("bitrate_k", minimum=1),
            maxrate_k=video.integer("maxrate_k", minimum=1),
            bufsize_k=video.integer("bufsize_k", minimum=1),
        ),
    )
    keep.finish()
    video.finish()
    node.finish()
    return profile


def _thumbnails(node: _Node) -> ThumbnailProfile:
    sprite = node.section("sprite")
    profile = ThumbnailProfile(
        poster_at_ratio=node.number("poster_at_ratio", minimum=0.0, maximum=0.99),
        poster_max_height=node.integer("poster_max_height", minimum=16),
        sprite=SpriteProfile(
            interval_s=sprite.integer("interval_s", minimum=1),
            tile_width=sprite.integer("tile_width", minimum=16),
            tile_height=sprite.integer("tile_height", minimum=16),
            columns=sprite.integer("columns", minimum=1, maximum=32),
            rows=sprite.integer("rows", minimum=1, maximum=32),
        ),
    )
    sprite.finish()
    node.finish()
    return profile


def _live(node: _Node) -> LiveProfile:
    transcode = node.section("transcode")
    profile = LiveProfile(
        segment_s=node.integer("segment_s", minimum=1, maximum=12),
        first_segment_s=node.integer("first_segment_s", minimum=1, maximum=12),
        list_size=node.integer("list_size", minimum=3, maximum=30),
        transcode=LiveTranscode(
            max_height=transcode.integer("max_height", minimum=144, maximum=2160),
            video_k=transcode.integer("video_k", minimum=100),
            video_args=transcode.args("video_args", LIVE_TRANSCODE_PLACEHOLDERS),
            audio_k=transcode.integer("audio_k", minimum=8),
            audio_channels=transcode.integer("audio_channels", minimum=1, maximum=8),
        ),
    )
    transcode.finish()
    node.finish()
    return profile


def _filters(node: _Node) -> FilterProfile:
    profile = FilterProfile(tonemap=node.text("tonemap"), deinterlace=node.text("deinterlace"))
    node.finish()
    return profile


def _preference(root: _Node) -> tuple[Backend, ...]:
    values = root.texts("backend_preference")
    backends = []
    for value in values:
        try:
            backends.append(Backend(value))
        except ValueError:
            raise ProfileError(
                f"{root.path}.backend_preference: unknown backend {value!r}"
            ) from None
    if len(set(backends)) != len(backends):
        raise ProfileError(f"{root.path}.backend_preference: duplicates")
    return tuple(backends)


def _backends(node: _Node) -> dict[Backend, BackendProfile]:
    backends: dict[Backend, BackendProfile] = {}
    for key in node.names():
        try:
            backend = Backend(key)
        except ValueError:
            raise ProfileError(f"{node.path}: unknown backend {key!r}") from None
        section = node.section(key)
        encoders_node = section.section("encoders")
        encoders: dict[VideoCodec, EncoderPreset] = {}
        for codec_key in encoders_node.names():
            try:
                codec = VideoCodec(codec_key)
            except ValueError:
                raise ProfileError(f"{encoders_node.path}: unknown codec {codec_key!r}") from None
            encoders[codec] = _encoder(encoders_node.section(codec_key), codec)
        encoders_node.finish()
        if VideoCodec.H264 not in encoders:
            raise ProfileError(f"{section.path}.encoders: an h264 encoder is required")
        backends[backend] = BackendProfile(
            name=backend,
            queue=section.text("queue"),
            input_args=section.args("input_args", DEVICE_PLACEHOLDERS),
            upload_filter=section.opt_text("upload_filter"),
            encoders=encoders,
        )
        section.finish()
    node.finish()
    return backends


def _encoder(node: _Node, codec: VideoCodec) -> EncoderPreset:
    preset = EncoderPreset(
        encoder=node.text("encoder"),
        pix_fmt=node.text("pix_fmt"),
        pix_fmt_10bit=node.opt_text("pix_fmt_10bit"),
        args=node.args("args", frozenset()),
        quality=node.number("quality", minimum=0.0, maximum=63.0),
        quality_args=node.args("quality_args", QUALITY_PLACEHOLDERS),
        bitrate_args=node.args("bitrate_args", BITRATE_PLACEHOLDERS),
        gop_args=node.args("gop_args", GOP_PLACEHOLDERS),
    )
    node.finish()
    if codec is VideoCodec.HEVC and preset.pix_fmt_10bit is None:
        raise ProfileError(f"{node.path}.pix_fmt_10bit: required for hevc (Main10)")
    return preset


# --- strict YAML reader -------------------------------------------------------------------


class _Node:
    """A mapping in the YAML document that remembers which keys were read."""

    def __init__(self, data: object, path: str) -> None:
        if not isinstance(data, dict):
            raise ProfileError(f"{path}: expected a mapping")
        self._data: dict[Any, Any] = data
        self.path = path
        self._seen: set[str] = set()

    def names(self) -> list[str]:
        return [str(key) for key in self._data]

    def _get(self, key: str) -> object:
        self._seen.add(key)
        if key not in self._data:
            raise ProfileError(f"{self.path}.{key}: missing")
        return self._data[key]

    def finish(self) -> None:
        unknown = sorted(str(key) for key in self._data if str(key) not in self._seen)
        if unknown:
            raise ProfileError(f"{self.path}: unknown keys {', '.join(unknown)}")

    def section(self, key: str) -> _Node:
        return _Node(self._get(key), f"{self.path}.{key}")

    def items(self, key: str) -> list[_Node]:
        value = self._get(key)
        if not isinstance(value, list):
            raise ProfileError(f"{self.path}.{key}: expected a list")
        return [_Node(item, f"{self.path}.{key}[{i}]") for i, item in enumerate(value)]

    def text(self, key: str) -> str:
        value = self._get(key)
        if not isinstance(value, str) or not value.strip():
            raise ProfileError(f"{self.path}.{key}: expected a non-empty string")
        return value

    def opt_text(self, key: str) -> str | None:
        """An optional string: the key may be absent or null."""
        if key not in self._data:
            self._seen.add(key)
            return None
        value = self._get(key)
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ProfileError(f"{self.path}.{key}: expected a non-empty string or null")
        return value

    def integer(self, key: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
        value = self._get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ProfileError(f"{self.path}.{key}: expected an integer")
        self._check_range(key, value, minimum, maximum)
        return value

    def number(self, key: str, *, minimum: float, maximum: float) -> float:
        value = self._get(key)
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ProfileError(f"{self.path}.{key}: expected a number")
        self._check_range(key, value, minimum, maximum)
        return float(value)

    def _check_range(
        self, key: str, value: float, minimum: float | None, maximum: float | None
    ) -> None:
        if minimum is not None and value < minimum:
            raise ProfileError(f"{self.path}.{key}: must be >= {minimum}")
        if maximum is not None and value > maximum:
            raise ProfileError(f"{self.path}.{key}: must be <= {maximum}")

    def texts(self, key: str) -> list[str]:
        value = self._get(key)
        if not isinstance(value, list) or not all(
            isinstance(item, str) and item.strip() for item in value
        ):
            raise ProfileError(f"{self.path}.{key}: expected a list of strings")
        return list(value)

    def text_set(self, key: str) -> frozenset[str]:
        return frozenset(self.texts(key))

    def enum[E: StrEnum](self, key: str, enum: type[E]) -> E:
        value = self.text(key)
        try:
            return enum(value)
        except ValueError:
            allowed = ", ".join(member.value for member in enum)
            raise ProfileError(f"{self.path}.{key}: {value!r} is not one of {allowed}") from None

    def args(self, key: str, placeholders: frozenset[str]) -> tuple[str, ...]:
        """An argument template: strings (YAML numbers are rejected to keep argv exact)."""
        value = self._get(key)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ProfileError(f"{self.path}.{key}: expected a list of strings")
        for item in value:
            try:
                fields = list(string.Formatter().parse(item))
            except ValueError as exc:  # a lone "{" or "}"
                raise ProfileError(f"{self.path}.{key}: invalid template {item!r}: {exc}") from None
            for _, name, spec, conversion in fields:
                if name is None:
                    continue
                if name not in placeholders or spec or conversion:
                    allowed = ", ".join(sorted(placeholders)) or "none"
                    raise ProfileError(
                        f"{self.path}.{key}: unknown placeholder {{{name}}} (allowed: {allowed})"
                    )
        return tuple(value)
