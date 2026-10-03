"""ffmpeg command builders and runner (SPEC §7.3 "Execution").

Builders turn a plan (apps/media/planner.py) into an exact argv list for one output and
one encoding backend: never a shell string, every path passed as a `file:` URL so no
file name can be read as a protocol or an option. Commands report progress on stdout
(`-progress pipe:1 -nostats`); `run()` parses it, keeps the last 50 stderr lines with
storage paths redacted, and raises `FfmpegError` on failure.

HLS packaging is split in two: ffmpeg writes one fMP4 media playlist per video rung and
per audio rendition (plus one WebVTT file per text subtitle), then
`write_hls_master()` writes `master.m3u8` itself, with CODECS read from the init
segments and BANDWIDTH/AVERAGE-BANDWIDTH measured from the segments ffmpeg produced.
"""

from __future__ import annotations

import math
import os
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import IO, Final

from apps.media.planner import (
    AudioPlan,
    CompatMp4Plan,
    HlsAudioPlan,
    HlsPlan,
    HlsRungPlan,
    StreamAction,
    SubtitlePlan,
    ThumbnailPlan,
    UhdMode,
    UhdPlan,
    VideoPlan,
)
from apps.media.probe import ProbeResult, bcp47_language, input_url
from apps.media.profiles import (
    Backend,
    BackendProfile,
    EncoderPreset,
    Profiles,
    VideoCodec,
    render_args,
)
from apps.media.progress import ProgressParser, ProgressUpdate
from apps.media.verify import ceil_target_duration, read_media_playlist

GLOBAL_ARGS: Final = (
    "-hide_banner",
    "-nostdin",
    "-y",
    "-loglevel",
    "error",
    "-progress",
    "pipe:1",
    "-nostats",
)
MUXING_QUEUE_ARGS: Final = ("-max_muxing_queue_size", "4096")
SDR_COLOR_ARGS: Final = ("-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709")
STDERR_TAIL_LINES: Final = 50
# GOP length (in frames per keyframe interval) when the frame rate is unknown (VFR);
# `-force_key_frames` then places the keyframes by time.
UNKNOWN_RATE_GOP_FPS: Final = 60
HLS_MASTER_NAME: Final = "master.m3u8"
HLS_PLAYLIST_NAME: Final = "index.m3u8"
HLS_INIT_NAME: Final = "init.mp4"
HLS_SEGMENT_PATTERN: Final = "seg_%05d.m4s"
HLS_SUBTITLE_FILE: Final = "subtitles.vtt"
SPRITE_PATTERN: Final = "sprite_%03d.jpg"
SUBTITLE_MUXERS: Final = {"vtt": ("webvtt", "webvtt"), "srt": ("srt", "srt")}  # codec, muxer
# HLS CODECS values for the audio we package (AAC is always AAC-LC from ffmpeg's encoder).
AUDIO_CODEC_STRINGS: Final = {"aac": "mp4a.40.2", "ac3": "ac-3", "eac3": "ec-3"}
# Fallback video CODECS value: H.264 High (0x64), no constraint flags, level 4.1 (0x29).
H264_HIGH_41_CODEC: Final = "avc1.640029"


class FfmpegError(RuntimeError):
    """ffmpeg exited with an error. `stderr_tail` is redacted (no storage paths)."""

    def __init__(self, returncode: int, stderr_tail: Sequence[str], *, timed_out: bool = False):
        self.returncode = returncode
        self.stderr_tail = tuple(stderr_tail)
        self.timed_out = timed_out
        last = self.stderr_tail[-1] if self.stderr_tail else "no error output"
        reason = "timed out" if timed_out else f"exited with {returncode}"
        super().__init__(f"ffmpeg {reason}: {last}")


class FfmpegCancelled(RuntimeError):
    """The run was cancelled through its `cancel` event."""


@dataclass(frozen=True, slots=True)
class Command:
    """One ffmpeg invocation and what it writes."""

    argv: tuple[str, ...]
    outputs: tuple[Path, ...]  # files (or HLS directories) the command produces
    directories: tuple[Path, ...] = ()  # created by `run()` before ffmpeg starts
    duration_ms: int | None = None  # expected output duration, for progress percentages
    redactions: tuple[tuple[str, str], ...] = ()  # (path, placeholder) for stderr lines


@dataclass(frozen=True, slots=True)
class Encoding:
    """Where video is encoded: the profiles, the backend and its device."""

    profiles: Profiles
    backend: Backend
    device: str | None = None  # DRM render node for vaapi/qsv (from hwdetect)
    ffmpeg: str = "ffmpeg"

    @property
    def backend_profile(self) -> BackendProfile:
        return self.profiles.backend(self.backend)

    def preset(self, codec: VideoCodec) -> EncoderPreset:
        return self.profiles.encoder(self.backend, codec)


@dataclass(frozen=True, slots=True)
class RunResult:
    returncode: int
    stderr_tail: tuple[str, ...]
    progress: ProgressUpdate | None  # the last progress block
    elapsed_s: float


@dataclass(frozen=True, slots=True)
class RateControl:
    """Video rate control: capped quality (compat MP4) or average bitrate (HLS rungs, UHD)."""

    maxrate_k: int
    bufsize_k: int
    quality: float | None = None
    target_k: int | None = None
    bitrate_k: int | None = None


# --- shared argument pieces ---------------------------------------------------------------


def h264_level_idc(level: str) -> str:
    """'4.1' -> '41': the level_idc number every H.264 encoder accepts for `-level`."""
    major, _, minor = level.partition(".")
    return str(int(major) * 10 + int(minor or 0))


def gop_size(frame_rate: Fraction | None, interval_s: int) -> int:
    """Frames between keyframes: `-g` = interval x fps (2 s -> 48 at 23.976)."""
    if frame_rate is None:
        return interval_s * UNKNOWN_RATE_GOP_FPS
    return max(1, round(frame_rate * interval_s))


def video_filters(video: VideoPlan, encoding: Encoding, *, pix_fmt: str) -> list[str]:
    """The filter chain for one encoded video output, in order: deinterlace, frame-rate
    reduction, scale (square pixels), HDR->SDR tone mapping, pixel format, upload."""
    filters = []
    if video.deinterlace:
        filters.append(encoding.profiles.filters.deinterlace)
    if video.rate_divisor > 1 and video.frame_rate is not None:
        filters.append(f"fps={_rate(video.frame_rate)}")
    if video.scale:
        filters += [f"scale={video.width}:{video.height}", "setsar=1"]
    if video.tonemap:
        filters.append(encoding.profiles.filters.tonemap)
    filters.append(f"format={pix_fmt}")
    upload = encoding.backend_profile.upload_filter
    if upload:
        filters.append(upload)
    return filters


def video_encoder_args(  # noqa: PLR0913
    preset: EncoderPreset,
    *,
    profile: str,
    level: str | None,
    rate: RateControl,
    frame_rate: Fraction | None,
    keyframe_interval_s: int,
) -> list[str]:
    """`-c:v` and everything the encoder needs: preset, profile/level, rate control, GOP."""
    args = ["-c:v", preset.encoder, *preset.args, "-profile:v", profile]
    if level is not None:
        args += ["-level:v", h264_level_idc(level)]
    if rate.bitrate_k is None:
        args += render_args(
            preset.quality_args,
            {
                "quality": f"{preset.quality:g}",
                "maxrate": f"{rate.maxrate_k}k",
                "bufsize": f"{rate.bufsize_k}k",
                "target": f"{rate.target_k or rate.maxrate_k}k",
            },
        )
    else:
        args += render_args(
            preset.bitrate_args,
            {
                "bitrate": f"{rate.bitrate_k}k",
                "maxrate": f"{rate.maxrate_k}k",
                "bufsize": f"{rate.bufsize_k}k",
            },
        )
    args += render_args(preset.gop_args, {"gop": str(gop_size(frame_rate, keyframe_interval_s))})
    if frame_rate is None:
        args += ["-force_key_frames", f"expr:gte(t,n_forced*{keyframe_interval_s})"]
    return args


def input_args(encoding: Encoding) -> list[str]:
    """Hardware device set-up for the backend (before `-i`)."""
    template = encoding.backend_profile.input_args
    if not template:
        return []
    if encoding.device is None:
        raise ValueError(f"backend {encoding.backend.value} needs a device (DRM render node)")
    return render_args(template, {"device": encoding.device})


def audio_args(plans: Sequence[AudioPlan]) -> list[str]:
    """`-map`, codec, bitrate, channels, language/title and disposition per audio output."""
    args: list[str] = []
    for i, plan in enumerate(plans):
        args += ["-map", f"0:{plan.source_index}"]
        if plan.action is StreamAction.COPY:
            args += [f"-c:a:{i}", "copy"]
        else:
            args += [f"-c:a:{i}", plan.codec, f"-b:a:{i}", f"{plan.bitrate_k}k"]
            args += [f"-ac:a:{i}", str(plan.channels)]
            if plan.sample_rate is not None:
                args += [f"-ar:a:{i}", str(plan.sample_rate)]
        args += [f"-metadata:s:a:{i}", f"language={plan.language}"]
        args += [f"-metadata:s:a:{i}", f"title={plan.title or ''}"]
        args += [f"-disposition:a:{i}", "default" if plan.default else "0"]
    return args


def subtitle_args(plans: Sequence[SubtitlePlan], codec: str) -> list[str]:
    args: list[str] = []
    for i, plan in enumerate(plans):
        args += ["-map", f"0:{plan.source_index}", f"-c:s:{i}", codec]
        args += [f"-metadata:s:s:{i}", f"language={plan.language}"]
        args += [f"-metadata:s:s:{i}", f"title={plan.title or ''}"]
        args += [f"-disposition:s:{i}", _subtitle_disposition(plan)]
    return args


# --- progressive MP4 (compat, remux, UHD) ---------------------------------------------------


def compat_mp4_command(
    probe: ProbeResult,
    plan: CompatMp4Plan,
    *,
    source: Path,
    output: Path,
    encoding: Encoding,
) -> Command:
    """The compat MP4 (SPEC §7.3): H.264 High <= L4.1 capped CRF, AAC stereo + surround,
    mov_text subtitles, +faststart. With a copied video stream this is the fast remux
    job of the on-demand fallback (`-c:v copy`, audio fixed)."""
    profiles = encoding.profiles
    compat = profiles.compat_mp4
    video = plan.video
    argv = [encoding.ffmpeg, *GLOBAL_ARGS]
    if video.action is StreamAction.ENCODE:
        argv += input_args(encoding)
    argv += ["-i", input_url(source), "-map", f"0:{video.source_index}"]
    if video.action is StreamAction.COPY:
        argv += ["-c:v", "copy"]
    else:
        preset = encoding.preset(VideoCodec(video.codec))
        argv += ["-vf", ",".join(video_filters(video, encoding, pix_fmt=preset.pix_fmt))]
        argv += video_encoder_args(
            preset,
            profile=compat.video.profile,
            level=compat.video.level,
            rate=RateControl(
                quality=preset.quality,
                maxrate_k=compat.video.maxrate_k,
                bufsize_k=compat.video.bufsize_k,
                target_k=compat.video.target_k,
            ),
            frame_rate=video.frame_rate,
            keyframe_interval_s=profiles.keyframe_interval_s,
        )
        if video.tonemap:
            argv += SDR_COLOR_ARGS
    argv += audio_args(plan.audio)
    argv += subtitle_args(plan.subtitles, compat.subtitle_codec)
    argv += ["-map_metadata", "-1", "-movflags", compat.movflags, *MUXING_QUEUE_ARGS]
    argv += ["-f", "mp4", input_url(output)]
    return Command(
        argv=tuple(argv),
        outputs=(output,),
        directories=(output.parent,),
        duration_ms=probe.duration_ms,
        redactions=_redactions(source, output.parent),
    )


def uhd_command(  # noqa: PLR0913
    probe: ProbeResult,
    plan: UhdPlan,
    audio: Sequence[AudioPlan],
    *,
    source: Path,
    output: Path,
    encoding: Encoding,
) -> Command:
    """The UHD version (SPEC §7.3): HEVC Main10 at the profile's bitrate, `hvc1`-tagged,
    the source colour signalling kept, audio as in the compat MP4."""
    if plan.mode is UhdMode.KEEP_SOURCE:
        raise ValueError("the UHD version keeps the source: there is nothing to encode")
    profiles = encoding.profiles
    preset = encoding.preset(VideoCodec(plan.video.codec))
    pix_fmt = preset.pix_fmt_10bit or preset.pix_fmt
    rate = RateControl(
        bitrate_k=plan.bitrate_k or profiles.uhd.video.bitrate_k,
        maxrate_k=plan.maxrate_k or profiles.uhd.video.maxrate_k,
        bufsize_k=plan.bufsize_k or profiles.uhd.video.bufsize_k,
    )
    argv = [encoding.ffmpeg, *GLOBAL_ARGS, *input_args(encoding), "-i", input_url(source)]
    argv += ["-map", f"0:{plan.video.source_index}"]
    argv += ["-vf", ",".join(video_filters(plan.video, encoding, pix_fmt=pix_fmt))]
    argv += video_encoder_args(
        preset,
        profile=plan.profile or profiles.uhd.video.profile,
        level=None,
        rate=rate,
        frame_rate=plan.video.frame_rate,
        keyframe_interval_s=profiles.keyframe_interval_s,
    )
    source_video = probe.video
    if source_video is not None:
        for option, value in (
            ("-color_primaries", source_video.color_primaries),
            ("-color_trc", source_video.color_transfer),
            ("-colorspace", source_video.color_space),
        ):
            if value:
                argv += [option, value]
    argv += ["-tag:v", "hvc1", *audio_args(audio)]
    argv += ["-map_metadata", "-1", "-movflags", profiles.compat_mp4.movflags]
    argv += [*MUXING_QUEUE_ARGS, "-f", "mp4", input_url(output)]
    return Command(
        argv=tuple(argv),
        outputs=(output,),
        directories=(output.parent,),
        duration_ms=probe.duration_ms,
        redactions=_redactions(source, output.parent),
    )


# --- HLS ladder ---------------------------------------------------------------------------


def hls_rung_dir(position: int) -> str:
    return f"v{position}"


def hls_audio_dir(position: int) -> str:
    return f"a{position}"


def hls_subtitle_dir(position: int) -> str:
    return f"s{position}"


def hls_command(
    probe: ProbeResult,
    plan: HlsPlan,
    *,
    output_dir: Path,
    source: Path,
    encoding: Encoding,
) -> Command:
    """All rungs, audio renditions and WebVTT subtitles in one ffmpeg run (one decode,
    `split` per rung). Run `write_hls_master()` afterwards."""
    if not plan.rungs:
        raise ValueError("the HLS plan has no rungs")
    profiles = encoding.profiles
    hls = profiles.hls
    preset = encoding.preset(VideoCodec(plan.rungs[0].video.codec))
    graph = _ladder_graph(plan.rungs, encoding, preset.pix_fmt)
    argv = [encoding.ffmpeg, *GLOBAL_ARGS, *input_args(encoding), "-i", input_url(source)]
    argv += ["-filter_complex", graph]
    directories = [output_dir]
    for position, rung in enumerate(plan.rungs):
        playlist = output_dir / hls_rung_dir(position) / HLS_PLAYLIST_NAME
        directories.append(playlist.parent)
        argv += ["-map", f"[v{position}]"]
        argv += video_encoder_args(
            preset,
            profile=hls.video.profile,
            level=hls.video.level,
            rate=RateControl(
                bitrate_k=rung.bitrate_k, maxrate_k=rung.maxrate_k, bufsize_k=rung.bufsize_k
            ),
            frame_rate=rung.video.frame_rate,
            keyframe_interval_s=profiles.keyframe_interval_s,
        )
        if rung.video.tonemap:
            argv += SDR_COLOR_ARGS
        argv += _hls_muxer_args(plan.segment_s, playlist)
    rates = {track.index: track.sample_rate for track in probe.audio}
    for position, audio in enumerate(plan.audio):
        playlist = output_dir / hls_audio_dir(position) / HLS_PLAYLIST_NAME
        directories.append(playlist.parent)
        argv += ["-map", f"0:{audio.source_index}"]
        if audio.action is StreamAction.COPY:
            argv += ["-c:a", "copy"]
        else:
            argv += ["-c:a", audio.codec, "-b:a", f"{audio.bitrate_k}k", "-ac", str(audio.channels)]
            source_rate = rates.get(audio.source_index)
            if source_rate is not None and source_rate > 48_000:
                argv += ["-ar", "48000"]
        argv += _hls_muxer_args(plan.segment_s, playlist)
    for position, subtitle in enumerate(plan.subtitles):
        vtt = output_dir / hls_subtitle_dir(position) / HLS_SUBTITLE_FILE
        directories.append(vtt.parent)
        argv += ["-map", f"0:{subtitle.source_index}", "-c:s", "webvtt", "-f", "webvtt"]
        argv.append(input_url(vtt))
    return Command(
        argv=tuple(argv),
        outputs=(output_dir,),
        directories=tuple(directories),
        duration_ms=probe.duration_ms,
        redactions=_redactions(source, output_dir),
    )


def write_hls_master(plan: HlsPlan, output_dir: Path, *, duration_ms: int | None) -> str:
    """Write `master.m3u8` (and one playlist per WebVTT subtitle) for a finished
    `hls_command()` run; returns the master playlist text.

    The default rung is listed first. BANDWIDTH is the peak segment bitrate of the rung
    plus the largest of its audio group, AVERAGE-BANDWIDTH the same with averages.
    """
    video_rates = [
        _playlist_bitrates(output_dir / hls_rung_dir(i) / HLS_PLAYLIST_NAME)
        for i in range(len(plan.rungs))
    ]
    video_codecs = [
        _video_codec_string(output_dir / hls_rung_dir(i) / HLS_INIT_NAME)
        for i in range(len(plan.rungs))
    ]
    groups: dict[str, list[tuple[int, HlsAudioPlan]]] = {}
    for position, audio in enumerate(plan.audio):
        groups.setdefault(audio.group, []).append((position, audio))
    group_rates = {
        group: [
            _playlist_bitrates(output_dir / hls_audio_dir(i) / HLS_PLAYLIST_NAME)
            for i, _ in members
        ]
        for group, members in groups.items()
    }

    lines = ["#EXTM3U", "#EXT-X-VERSION:7", "#EXT-X-INDEPENDENT-SEGMENTS", ""]
    for group, members in groups.items():
        for position, audio in members:
            attributes = [
                "TYPE=AUDIO",
                f'GROUP-ID="{group}"',
                *_language_attribute(audio.language),
                f'NAME="{_quoted(audio.name)}"',
                f"DEFAULT={_yes(audio.default)}",
                "AUTOSELECT=YES",
                f'CHANNELS="{audio.channels}"',
                f'URI="{hls_audio_dir(position)}/{HLS_PLAYLIST_NAME}"',
            ]
            lines.append("#EXT-X-MEDIA:" + ",".join(attributes))
    subtitle_names: set[str] = set()
    for position, subtitle in enumerate(plan.subtitles):
        uri = _write_subtitle_playlist(output_dir / hls_subtitle_dir(position), duration_ms)
        name = _unique(subtitle.title or subtitle.language, subtitle_names)
        attributes = [
            "TYPE=SUBTITLES",
            'GROUP-ID="subs"',
            *_language_attribute(subtitle.language),
            f'NAME="{_quoted(name)}"',
            f"DEFAULT={_yes(subtitle.default)}",
            "AUTOSELECT=YES",
            f"FORCED={_yes(subtitle.forced)}",
            f'URI="{hls_subtitle_dir(position)}/{uri}"',
        ]
        lines.append("#EXT-X-MEDIA:" + ",".join(attributes))
    if len(lines) > 4:
        lines.append("")

    order = sorted(range(len(plan.rungs)), key=lambda i: (not plan.rungs[i].default, i))
    # One set of variants per audio group (AAC, then e.g. EC-3); no AUDIO without audio.
    audio_groups: list[str | None] = [*groups] or [None]
    for audio_group in audio_groups:
        rates = group_rates.get(audio_group, []) if audio_group else []
        audio_peak = max((peak for peak, _ in rates), default=0)
        audio_avg = max((avg for _, avg in rates), default=0)
        audio_codec = _audio_codec_string(groups[audio_group][0][1].codec) if audio_group else None
        for i in order:
            rung = plan.rungs[i]
            peak, average = video_rates[i]
            codecs = ",".join(c for c in (video_codecs[i], audio_codec) if c)
            attributes = [
                f"BANDWIDTH={peak + audio_peak}",
                f"AVERAGE-BANDWIDTH={average + audio_avg}",
                f'CODECS="{codecs}"',
                f"RESOLUTION={rung.width}x{rung.height}",
            ]
            if rung.video.frame_rate is not None:
                attributes.append(f"FRAME-RATE={float(rung.video.frame_rate):.3f}")
            if audio_group is not None:
                attributes.append(f'AUDIO="{audio_group}"')
            if plan.subtitles:
                attributes.append('SUBTITLES="subs"')
            lines.append("#EXT-X-STREAM-INF:" + ",".join(attributes))
            lines.append(f"{hls_rung_dir(i)}/{HLS_PLAYLIST_NAME}")
    text = "\n".join(lines) + "\n"
    _write_atomic(output_dir / HLS_MASTER_NAME, text)
    return text


def _ladder_graph(rungs: Sequence[HlsRungPlan], encoding: Encoding, pix_fmt: str) -> str:
    source = f"[0:{rungs[0].video.source_index}]"
    if len(rungs) == 1:
        return f"{source}{','.join(video_filters(rungs[0].video, encoding, pix_fmt=pix_fmt))}[v0]"
    branches = "".join(f"[s{i}]" for i in range(len(rungs)))
    chains = [f"{source}split={len(rungs)}{branches}"]
    for i, rung in enumerate(rungs):
        chain = ",".join(video_filters(rung.video, encoding, pix_fmt=pix_fmt))
        chains.append(f"[s{i}]{chain}[v{i}]")
    return ";".join(chains)


def _hls_muxer_args(segment_s: int, playlist: Path) -> list[str]:
    return [
        *MUXING_QUEUE_ARGS,
        "-f",
        "hls",
        "-hls_time",
        str(segment_s),
        "-hls_playlist_type",
        "vod",
        "-hls_segment_type",
        "fmp4",
        "-hls_flags",
        "independent_segments",
        "-hls_fmp4_init_filename",
        HLS_INIT_NAME,
        "-hls_segment_filename",
        input_url(playlist.parent / HLS_SEGMENT_PATTERN),
        input_url(playlist),
    ]


def _playlist_bitrates(playlist_path: Path) -> tuple[int, int]:
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


def _video_codec_string(init_segment: Path) -> str:
    """`avc1.PPCCLL` from the avcC box of the init segment (profile, constraints, level)."""
    try:
        data = init_segment.read_bytes()
    except OSError:
        data = b""
    position = data.find(b"avcC")
    if position >= 0 and len(data) >= position + 8:
        return "avc1." + data[position + 5 : position + 8].hex()
    return H264_HIGH_41_CODEC


def _audio_codec_string(codec: str) -> str:
    return AUDIO_CODEC_STRINGS.get(codec, codec)


def _write_subtitle_playlist(directory: Path, duration_ms: int | None) -> str:
    """A one-segment VOD playlist for `subtitles.vtt`; returns its file name."""
    duration_s = (duration_ms or 0) / 1000
    text = "\n".join(
        [
            "#EXTM3U",
            "#EXT-X-VERSION:3",
            f"#EXT-X-TARGETDURATION:{ceil_target_duration([duration_s])}",
            "#EXT-X-MEDIA-SEQUENCE:0",
            "#EXT-X-PLAYLIST-TYPE:VOD",
            f"#EXTINF:{duration_s:.3f},",
            HLS_SUBTITLE_FILE,
            "#EXT-X-ENDLIST",
            "",
        ]
    )
    _write_atomic(directory / HLS_PLAYLIST_NAME, text)
    return HLS_PLAYLIST_NAME


# --- thumbnails ---------------------------------------------------------------------------


def poster_command(
    plan: ThumbnailPlan, *, source: Path, output: Path, profiles: Profiles, ffmpeg: str = "ffmpeg"
) -> Command:
    """The fallback poster: one JPEG frame at 10% of the duration (fast input seek)."""
    filters = _thumbnail_filters(plan, profiles)
    filters += [f"scale={plan.poster_width}:{plan.poster_height}", "setsar=1"]
    if plan.tonemap:
        filters.append(profiles.filters.tonemap)
    filters.append("format=yuvj420p")
    argv = [ffmpeg, *GLOBAL_ARGS, "-ss", f"{plan.poster_at_ms / 1000:.3f}"]
    argv += ["-i", input_url(source), "-map", f"0:{plan.source_index}", "-frames:v", "1"]
    argv += ["-vf", ",".join(filters), "-c:v", "mjpeg", "-q:v", "2"]
    argv += ["-f", "image2", "-update", "1", input_url(output)]
    return Command(
        argv=tuple(argv),
        outputs=(output,),
        directories=(output.parent,),
        redactions=_redactions(source, output.parent),
    )


def sprite_command(  # noqa: PLR0913
    plan: ThumbnailPlan,
    *,
    source: Path,
    output_dir: Path,
    profiles: Profiles,
    duration_ms: int | None,
    ffmpeg: str = "ffmpeg",
) -> Command:
    """Scrubbing sprites: one tile every `interval_s`, letterboxed to the tile size,
    `columns x rows` tiles per JPEG sheet (`sprite_001.jpg`, ...)."""
    tile_w, tile_h = plan.tile_width, plan.tile_height
    filters = _thumbnail_filters(plan, profiles)
    filters += [
        f"fps=1/{plan.interval_s}",
        f"scale={tile_w}:{tile_h}:force_original_aspect_ratio=decrease",
    ]
    if plan.tonemap:
        filters.append(profiles.filters.tonemap)
    filters += [
        "format=yuvj420p",
        f"pad={tile_w}:{tile_h}:(ow-iw)/2:(oh-ih)/2",
        "setsar=1",
        f"tile={plan.columns}x{plan.rows}",
    ]
    argv = [ffmpeg, *GLOBAL_ARGS, "-i", input_url(source), "-map", f"0:{plan.source_index}"]
    argv += ["-vf", ",".join(filters), "-fps_mode", "passthrough", "-c:v", "mjpeg", "-q:v", "4"]
    argv += ["-f", "image2", "-start_number", "1", input_url(output_dir / SPRITE_PATTERN)]
    return Command(
        argv=tuple(argv),
        outputs=tuple(output_dir / sprite_sheet_name(n) for n in range(1, plan.sheet_count + 1)),
        directories=(output_dir,),
        duration_ms=duration_ms,
        redactions=_redactions(source, output_dir),
    )


def sprite_sheet_name(number: int) -> str:
    """File name of sheet `number` (1-based), matching `SPRITE_PATTERN`."""
    return SPRITE_PATTERN.replace("%03d", f"{number:03d}")


def thumbnails_vtt(plan: ThumbnailPlan, duration_ms: int) -> str:
    """`thumbs.vtt`: one cue per tile pointing into its sheet with a `#xywh=` fragment."""
    interval_ms = plan.interval_s * 1000
    per_sheet = plan.columns * plan.rows
    lines = ["WEBVTT", ""]
    for tile in range(plan.tile_count):
        start = tile * interval_ms
        end = min(start + interval_ms, duration_ms)
        if end <= start:
            break
        sheet, position = divmod(tile, per_sheet)
        x = (position % plan.columns) * plan.tile_width
        y = (position // plan.columns) * plan.tile_height
        lines.append(f"{_vtt_time(start)} --> {_vtt_time(end)}")
        lines.append(
            f"{sprite_sheet_name(sheet + 1)}#xywh={x},{y},{plan.tile_width},{plan.tile_height}"
        )
        lines.append("")
    return "\n".join(lines)


def _thumbnail_filters(plan: ThumbnailPlan, profiles: Profiles) -> list[str]:
    return [profiles.filters.deinterlace] if plan.deinterlace else []


# --- subtitles ----------------------------------------------------------------------------


def subtitle_path(output_dir: Path, subtitle: SubtitlePlan, fmt: str) -> Path:
    """Where an extracted subtitle goes: `<stream index>.<language>.<vtt|srt>`."""
    return output_dir / f"{subtitle.source_index}.{subtitle.language}.{fmt}"


def subtitles_command(
    subtitles: Iterable[SubtitlePlan],
    *,
    source: Path,
    output_dir: Path,
    ffmpeg: str = "ffmpeg",
) -> Command:
    """Extract every embedded text subtitle to WebVTT and SRT in one pass."""
    argv = [ffmpeg, *GLOBAL_ARGS, "-i", input_url(source)]
    outputs = []
    for subtitle in subtitles:
        for fmt in subtitle.extract_formats:
            codec, muxer = SUBTITLE_MUXERS[fmt]
            path = subtitle_path(output_dir, subtitle, fmt)
            argv += ["-map", f"0:{subtitle.source_index}", "-c:s", codec, "-f", muxer]
            argv.append(input_url(path))
            outputs.append(path)
    if not outputs:
        raise ValueError("no text subtitles to extract")
    return Command(
        argv=tuple(argv),
        outputs=tuple(outputs),
        directories=(output_dir,),
        redactions=_redactions(source, output_dir),
    )


# --- hardware test encode -----------------------------------------------------------------


def trial_encode_command(  # noqa: PLR0913
    profiles: Profiles,
    backend: Backend,
    codec: VideoCodec,
    *,
    device: str | None = None,
    ffmpeg: str = "ffmpeg",
    seconds: int = 2,
) -> Command:
    """Encode a short `testsrc2` clip with the backend's real preset (H.264 as for the
    compat MP4, HEVC as Main10 for UHD) and discard it. Used by hwdetect."""
    encoding = Encoding(profiles=profiles, backend=backend, device=device, ffmpeg=ffmpeg)
    preset = encoding.preset(codec)
    ten_bit = codec is VideoCodec.HEVC
    pix_fmt = (preset.pix_fmt_10bit or preset.pix_fmt) if ten_bit else preset.pix_fmt
    filters = [f"format={pix_fmt}"]
    upload = encoding.backend_profile.upload_filter
    if upload:
        filters.append(upload)
    rate = Fraction(30)
    if ten_bit:
        uhd = profiles.uhd.video
        encoder = video_encoder_args(
            preset,
            profile=uhd.profile,
            level=None,
            rate=RateControl(
                bitrate_k=uhd.bitrate_k, maxrate_k=uhd.maxrate_k, bufsize_k=uhd.bufsize_k
            ),
            frame_rate=rate,
            keyframe_interval_s=profiles.keyframe_interval_s,
        )
    else:
        compat = profiles.compat_mp4.video
        encoder = video_encoder_args(
            preset,
            profile=compat.profile,
            level=compat.level,
            rate=RateControl(
                quality=preset.quality,
                maxrate_k=compat.maxrate_k,
                bufsize_k=compat.bufsize_k,
                target_k=compat.target_k,
            ),
            frame_rate=rate,
            keyframe_interval_s=profiles.keyframe_interval_s,
        )
    argv = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error"]
    argv += input_args(encoding)
    argv += ["-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate={rate}:duration={seconds}"]
    argv += ["-vf", ",".join(filters), *encoder, "-f", "null", "-"]
    return Command(argv=tuple(argv), outputs=())


# --- runner -------------------------------------------------------------------------------


def run(
    command: Command,
    *,
    on_progress: Callable[[ProgressUpdate], None] | None = None,
    timeout_s: float | None = None,
    cancel: threading.Event | None = None,
) -> RunResult:
    """Run a command to completion, feeding progress blocks to `on_progress`.

    Raises `FfmpegError` (non-zero exit or timeout) or `FfmpegCancelled`; both kill
    ffmpeg first. Exceptions raised by `on_progress` also kill ffmpeg and propagate.
    """
    for directory in command.directories:
        directory.mkdir(parents=True, exist_ok=True)
    parser = ProgressParser(command.duration_ms)
    tail: deque[str] = deque(maxlen=STDERR_TAIL_LINES)
    started = time.monotonic()
    process = subprocess.Popen(  # noqa: S603 - argv list, no shell
        command.argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    timed_out = threading.Event()
    reader = threading.Thread(target=_drain, args=(process.stderr, tail), daemon=True)
    reader.start()
    timer = None
    if timeout_s is not None:
        timer = threading.Timer(timeout_s, _expire, args=(process, timed_out))
        timer.daemon = True
        timer.start()
    cancelled = False
    try:
        assert process.stdout is not None  # noqa: S101 - set by stdout=PIPE
        for line in process.stdout:
            update = parser.feed(line)
            if update is not None and on_progress is not None:
                on_progress(update)
            if cancel is not None and cancel.is_set():
                cancelled = True
                process.kill()
                break
        returncode = process.wait()
    except BaseException:
        process.kill()
        process.wait()
        raise
    finally:
        if timer is not None:
            timer.cancel()
        reader.join(timeout=5)
        if process.stdout is not None:
            process.stdout.close()
    lines = tuple(_redact(line, command.redactions) for line in tail)
    if cancelled:
        raise FfmpegCancelled("ffmpeg run cancelled")
    if timed_out.is_set():
        raise FfmpegError(returncode, lines, timed_out=True)
    if returncode != 0:
        raise FfmpegError(returncode, lines)
    return RunResult(
        returncode=returncode,
        stderr_tail=lines,
        progress=parser.last,
        elapsed_s=time.monotonic() - started,
    )


def _drain(stream: IO[str] | None, tail: deque[str]) -> None:
    if stream is None:
        return
    with stream:
        for line in stream:
            stripped = line.rstrip()
            if stripped:
                tail.append(stripped)


def _expire(process: subprocess.Popen[str], flag: threading.Event) -> None:
    flag.set()
    process.kill()


# --- helpers ------------------------------------------------------------------------------


def _redactions(source: Path, output_dir: Path) -> tuple[tuple[str, str], ...]:
    pairs = [(str(source.absolute()), "<source>"), (str(output_dir.absolute()), "<output>")]
    return tuple(sorted(pairs, key=lambda pair: len(pair[0]), reverse=True))


def _redact(line: str, redactions: Sequence[tuple[str, str]]) -> str:
    for path, placeholder in redactions:
        line = line.replace(path, placeholder)
    return line


def _rate(rate: Fraction) -> str:
    return f"{rate.numerator}/{rate.denominator}"


def _subtitle_disposition(plan: SubtitlePlan) -> str:
    flags = [name for name, on in (("default", plan.default), ("forced", plan.forced)) if on]
    return "+".join(flags) or "0"


def _language_attribute(code: str) -> list[str]:
    language = bcp47_language(code)
    return [f'LANGUAGE="{language}"'] if language else []


def _quoted(value: str) -> str:
    """HLS quoted strings cannot contain double quotes or line breaks."""
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


def _vtt_time(ms: int) -> str:
    hours, rest = divmod(ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)
