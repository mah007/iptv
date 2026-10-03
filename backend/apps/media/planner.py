"""Transcode planner (SPEC §7.3).

`plan_processing()` decides, from a probe and the library's processing policy, which
outputs a media file gets (compat MP4 with per-stream copy/encode decisions, HLS ladder,
UHD version, thumbnails, subtitle extraction) and which of them run at ingest.
`decide_on_demand()` decides what to do when a title is requested before its compat MP4
exists. Both are pure functions of their inputs; nothing here runs ffmpeg.

Reason and warning values are stable machine codes (the admin UI translates them).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import StrEnum
from fractions import Fraction
from typing import Final

from apps.media.probe import AudioStream, HdrKind, ProbeResult, VideoStream
from apps.media.profiles import (
    CompatMp4Profile,
    CopyVideoRule,
    HlsProfile,
    HlsRung,
    Profiles,
    VideoCodec,
)

QUALITY_CEILINGS: Final = (480, 720, 1080, 2160)
# Frame rates above this are container time bases, not real rates (VFR MKV/TS).
MAX_PLAUSIBLE_FPS: Final = 240
# H.264 level limits (ITU-T H.264 Table A-1): max macroblocks per second, per frame.
H264_LEVEL_LIMITS: Final[dict[str, tuple[int, int]]] = {
    "3": (40_500, 1_620),
    "3.0": (40_500, 1_620),
    "3.1": (108_000, 3_600),
    "3.2": (216_000, 5_120),
    "4": (245_760, 8_192),
    "4.0": (245_760, 8_192),
    "4.1": (245_760, 8_192),
    "4.2": (522_240, 8_704),
    "5": (589_824, 22_080),
    "5.0": (589_824, 22_080),
    "5.1": (983_040, 36_864),
    "5.2": (2_073_600, 36_864),
}


class PlanError(ValueError):
    """The file cannot be planned (for example, it has no video stream)."""


class ProcessingPolicy(StrEnum):
    """`Library.processing_policy`."""

    INGEST = "ingest"  # produce every output when the file is added
    ON_DEMAND = "on_demand"  # produce the playable outputs on first request
    PASSTHROUGH = "passthrough"  # never transcode; serve the source


class StreamAction(StrEnum):
    COPY = "copy"
    ENCODE = "encode"


class AudioRole(StrEnum):
    STEREO = "stereo"
    SURROUND = "surround"


class UhdMode(StrEnum):
    KEEP_SOURCE = "keep_source"
    ENCODE = "encode"


class Output(StrEnum):
    COMPAT_MP4 = "compat_mp4"
    HLS = "hls"
    UHD = "uhd"
    THUMBNAILS = "thumbnails"
    SUBTITLES = "subtitles"


class OnDemandAction(StrEnum):
    SERVE_COMPAT = "serve_compat"  # the compat MP4 is ready
    SERVE_SOURCE = "serve_source"  # the source plays as is
    REMUX = "remux"  # only container/audio are wrong: copy video, seconds to minutes
    REALTIME = "realtime"  # live HLS transcode on a GPU worker (capped)
    PREPARING = "preparing"  # HTTP 503 TITLE_PREPARING until the compat MP4 is ready


@dataclass(frozen=True, slots=True)
class VideoPlan:
    """One video output: copied as is, or encoded with these transforms."""

    action: StreamAction
    source_index: int
    codec: str
    width: int
    height: int
    frame_rate: Fraction | None  # output rate; None when the source rate is unknown
    rate_divisor: int  # 1 keeps every frame; n keeps every n-th (H.264 level limit)
    scale: bool
    tonemap: bool  # HDR -> SDR
    deinterlace: bool
    ten_bit: bool
    reasons: tuple[str, ...] = ()  # why it is encoded rather than copied


@dataclass(frozen=True, slots=True)
class AudioPlan:
    source_index: int
    role: AudioRole
    action: StreamAction
    codec: str
    channels: int
    bitrate_k: int | None  # None when copied
    sample_rate: int | None  # None keeps the source rate
    language: str
    title: str | None  # None clears the source title
    default: bool


@dataclass(frozen=True, slots=True)
class SubtitlePlan:
    source_index: int
    codec: str
    language: str
    title: str | None
    forced: bool
    default: bool
    hearing_impaired: bool
    is_text: bool
    extract_formats: tuple[str, ...]  # ("vtt", "srt") for text subtitles
    burn_in_candidate: bool  # image subtitles (PGS/VobSub) can only be burned in


@dataclass(frozen=True, slots=True)
class CompatMp4Plan:
    video: VideoPlan
    audio: tuple[AudioPlan, ...]
    subtitles: tuple[SubtitlePlan, ...]  # text subtitles, muxed as mov_text

    @property
    def is_remux(self) -> bool:
        """Video is copied: the job only rewraps and fixes audio (fast)."""
        return self.video.action is StreamAction.COPY

    @property
    def expected_streams(self) -> tuple[int, int, int]:
        """(video, audio, subtitle) stream counts of the output, for verification."""
        return 1, len(self.audio), len(self.subtitles)


@dataclass(frozen=True, slots=True)
class HlsRungPlan:
    name: str
    video: VideoPlan
    bitrate_k: int
    maxrate_k: int
    bufsize_k: int
    default: bool
    native: bool  # extra rung at the source's own size

    @property
    def width(self) -> int:
        return self.video.width

    @property
    def height(self) -> int:
        return self.video.height


@dataclass(frozen=True, slots=True)
class HlsAudioPlan:
    source_index: int
    group: str  # HLS GROUP-ID: "aac", or the passthrough codec ("eac3")
    action: StreamAction
    codec: str
    channels: int
    bitrate_k: int | None
    language: str
    name: str  # HLS NAME, unique within the group
    default: bool


@dataclass(frozen=True, slots=True)
class HlsPlan:
    rungs: tuple[HlsRungPlan, ...]  # tallest first
    audio: tuple[HlsAudioPlan, ...]
    subtitles: tuple[SubtitlePlan, ...]  # text subtitles, as WebVTT renditions
    segment_s: int

    @property
    def default_rung(self) -> HlsRungPlan:
        return next(rung for rung in self.rungs if rung.default)


@dataclass(frozen=True, slots=True)
class UhdPlan:
    mode: UhdMode
    video: VideoPlan
    profile: str | None  # encoder profile (main10) when encoding
    bitrate_k: int | None
    maxrate_k: int | None
    bufsize_k: int | None
    reasons: tuple[str, ...] = ()  # why the source can't be kept


@dataclass(frozen=True, slots=True)
class ThumbnailPlan:
    source_index: int
    poster_at_ms: int
    poster_width: int
    poster_height: int
    interval_s: int
    tile_width: int
    tile_height: int
    columns: int
    rows: int
    tile_count: int
    sheet_count: int
    tonemap: bool
    deinterlace: bool


@dataclass(frozen=True, slots=True)
class ProcessingPlan:
    policy: ProcessingPolicy
    max_quality: int
    direct_playable: bool
    compat_mp4: CompatMp4Plan | None  # None only for passthrough libraries
    hls: HlsPlan | None
    uhd: UhdPlan | None
    thumbnails: ThumbnailPlan
    subtitles: tuple[SubtitlePlan, ...]  # every embedded subtitle track
    run_at_ingest: tuple[Output, ...]
    warnings: tuple[str, ...] = ()

    def required_codecs(self) -> frozenset[VideoCodec]:
        """Encoders the ingest jobs need (routes the job to a backend that has them)."""
        codecs: set[VideoCodec] = set()
        compat = self.compat_mp4
        if Output.COMPAT_MP4 in self.run_at_ingest and compat and not compat.is_remux:
            codecs.add(VideoCodec.H264)
        if Output.HLS in self.run_at_ingest and self.hls:
            codecs.add(VideoCodec.H264)
        if Output.UHD in self.run_at_ingest and self.uhd and self.uhd.mode is UhdMode.ENCODE:
            codecs.add(VideoCodec.HEVC)
        return frozenset(codecs)


@dataclass(frozen=True, slots=True)
class OnDemandDecision:
    action: OnDemandAction
    reason: str
    ensure_compat_job: bool  # a compat MP4 job should exist (callers de-duplicate)
    urgent: bool  # a viewer is waiting on that job: run it before ingest work


# --- planning -----------------------------------------------------------------------------


def plan_processing(
    probe: ProbeResult,
    *,
    policy: ProcessingPolicy,
    max_quality: int,
    profiles: Profiles,
) -> ProcessingPlan:
    """Decide the outputs for one media file (SPEC §7.3 "Planner")."""
    if max_quality not in QUALITY_CEILINGS:
        raise PlanError(f"max_quality must be one of {QUALITY_CEILINGS}")
    video = probe.video
    if video is None:
        raise PlanError("no_video_stream")

    subtitles = _subtitle_plans(probe)
    text_subtitles = tuple(s for s in subtitles if s.is_text)
    thumbnails = _thumbnail_plan(probe, video, profiles)
    warnings = _warnings(probe, video)

    compat: CompatMp4Plan | None = None
    hls: HlsPlan | None = None
    uhd: UhdPlan | None = None
    run: list[Output] = []
    if policy is not ProcessingPolicy.PASSTHROUGH:
        compat = CompatMp4Plan(
            video=_compat_video(video, profiles.compat_mp4, max_quality),
            audio=_compat_audio(probe, profiles.compat_mp4),
            subtitles=text_subtitles,
        )
        hls = _hls_plan(probe, video, profiles, max_quality, text_subtitles)
        uhd = _uhd_plan(probe, video, profiles, max_quality)
        if policy is ProcessingPolicy.INGEST:
            run += [Output.COMPAT_MP4, Output.HLS]
            if uhd is not None:
                run.append(Output.UHD)
    run.append(Output.THUMBNAILS)
    if text_subtitles:
        run.append(Output.SUBTITLES)

    return ProcessingPlan(
        policy=policy,
        max_quality=max_quality,
        direct_playable=is_direct_playable(probe, profiles),
        compat_mp4=compat,
        hls=hls,
        uhd=uhd,
        thumbnails=thumbnails,
        subtitles=subtitles,
        run_at_ingest=tuple(run),
        warnings=warnings,
    )


def is_direct_playable(probe: ProbeResult, profiles: Profiles) -> bool:
    """Whether the source itself plays in every client: a faststart MP4 whose video could
    be copied into the compat MP4 unchanged and whose main audio is AAC (or absent)."""
    video = probe.video
    if video is None or probe.container != "mp4" or probe.faststart is not True:
        return False
    compat = profiles.compat_mp4
    if _copy_blockers(video, compat.copy_video_when, compat.max_width, compat.max_height):
        return False
    audio = probe.default_audio
    return audio is None or audio.codec in compat.stereo.copy_codecs


def decide_on_demand(
    plan: ProcessingPlan,
    *,
    compat_ready: bool,
    realtime_enabled: bool,
    realtime_active: int,
    realtime_max: int,
) -> OnDemandDecision:
    """What to do for a play request (SPEC §7.3 "On-demand fallback")."""
    if compat_ready:
        return OnDemandDecision(OnDemandAction.SERVE_COMPAT, "compat_ready", False, False)
    if plan.policy is ProcessingPolicy.PASSTHROUGH or plan.compat_mp4 is None:
        return OnDemandDecision(OnDemandAction.SERVE_SOURCE, "passthrough", False, False)
    if plan.direct_playable:
        return OnDemandDecision(OnDemandAction.SERVE_SOURCE, "direct_playable", True, False)
    if plan.compat_mp4.is_remux:
        return OnDemandDecision(OnDemandAction.REMUX, "container_or_audio", True, True)
    if realtime_enabled and realtime_active < realtime_max:
        return OnDemandDecision(OnDemandAction.REALTIME, "video_needs_encode", True, False)
    reason = "realtime_disabled" if not realtime_enabled else "realtime_at_capacity"
    return OnDemandDecision(OnDemandAction.PREPARING, reason, True, True)


def fit_within(width: int, height: int, box_width: int, box_height: int) -> tuple[int, int]:
    """Scale (width, height) down to fit the box, keeping the aspect ratio; never up.
    Results are even (4:2:0 chroma needs even dimensions)."""
    scale = min(1.0, box_width / width, box_height / height)
    return _even(width * scale), _even(height * scale)


def level_frame_rate(
    width: int, height: int, rate: Fraction | None, level: str
) -> tuple[Fraction | None, int]:
    """The highest rate <= `rate` (an integer fraction of it) that the H.264 level allows
    at this frame size, and the divisor used (1080p59.94 at L4.1 -> 29.97, 2)."""
    limits = H264_LEVEL_LIMITS.get(level)
    if rate is None or limits is None:
        return rate, 1
    max_mbps = limits[0]
    macroblocks = math.ceil(width / 16) * math.ceil(height / 16)
    divisor = 1
    while rate / divisor * macroblocks > max_mbps:
        divisor += 1
    return rate / divisor, divisor


def _even(value: float) -> int:
    return max(2, round(value / 2) * 2)


def _quality_box(max_width: int, max_height: int, max_quality: int) -> tuple[int, int]:
    return min(max_width, _even(max_quality * 16 / 9)), min(max_height, max_quality)


def _source_rate(video: VideoStream) -> Fraction | None:
    rate = video.frame_rate
    if rate is None or rate > MAX_PLAUSIBLE_FPS:
        return None
    return rate


def _copy_blockers(
    video: VideoStream, rule: CopyVideoRule, box_width: int, box_height: int
) -> tuple[str, ...]:
    reasons = []
    if video.codec not in rule.codecs:
        reasons.append("codec")
    else:
        if (video.profile or "").lower() not in rule.profiles:
            reasons.append("profile")
        if video.level is None or video.level > rule.max_level:
            reasons.append("level")
    if video.pix_fmt not in rule.pix_fmts:
        reasons.append("pix_fmt")
    if video.hdr is not HdrKind.SDR:
        reasons.append("hdr")
    if video.width > box_width or video.height > box_height:
        reasons.append("resolution")
    return tuple(reasons)


def _encoded_video(  # noqa: PLR0913
    video: VideoStream,
    codec: VideoCodec,
    box: tuple[int, int],
    *,
    level: str | None,
    ten_bit: bool,
    tonemap: bool,
    reasons: tuple[str, ...],
) -> VideoPlan:
    width, height = fit_within(video.display_width, video.height, *box)
    rate = _source_rate(video)
    divisor = 1
    if level is not None and codec is VideoCodec.H264:
        rate, divisor = level_frame_rate(width, height, rate, level)
    return VideoPlan(
        action=StreamAction.ENCODE,
        source_index=video.index,
        codec=codec.value,
        width=width,
        height=height,
        frame_rate=rate,
        rate_divisor=divisor,
        scale=(width, height) != (video.width, video.height) or video.sar != 1,
        tonemap=tonemap,
        deinterlace=video.interlaced,
        ten_bit=ten_bit,
        reasons=reasons,
    )


def _compat_video(video: VideoStream, compat: CompatMp4Profile, max_quality: int) -> VideoPlan:
    box = _quality_box(compat.max_width, compat.max_height, max_quality)
    reasons = _copy_blockers(video, compat.copy_video_when, *box)
    if not reasons:
        return VideoPlan(
            action=StreamAction.COPY,
            source_index=video.index,
            codec=video.codec,
            width=video.width,
            height=video.height,
            frame_rate=_source_rate(video),
            rate_divisor=1,
            scale=False,
            tonemap=False,
            deinterlace=False,
            ten_bit=False,
        )
    return _encoded_video(
        video,
        compat.video.codec,
        box,
        level=compat.video.level,
        ten_bit=False,
        tonemap=video.hdr is not HdrKind.SDR,
        reasons=reasons,
    )


def _ordered_audio(probe: ProbeResult) -> list[AudioStream]:
    """The main (default) track first, then the others in file order; silent tracks dropped."""
    tracks = [a for a in probe.audio if a.channels > 0]
    main = probe.default_audio
    if main is not None and main in tracks:
        tracks.remove(main)
        tracks.insert(0, main)
    return tracks


def _compat_audio(probe: ProbeResult, compat: CompatMp4Profile) -> tuple[AudioPlan, ...]:
    """Per source track: AAC stereo (copied when already AAC <= 2 ch), then, for
    multichannel tracks, the original AC-3/E-AC-3/AAC or an AAC 5.1 encode."""
    plans: list[AudioPlan] = []
    stereo, surround = compat.stereo, compat.surround
    for track in _ordered_audio(probe):
        keep_title = track.title if track.commentary else None
        if track.codec in stereo.copy_codecs and track.channels <= stereo.channels:
            plans.append(_copied_audio(track, AudioRole.STEREO))
        else:
            plans.append(
                AudioPlan(
                    source_index=track.index,
                    role=AudioRole.STEREO,
                    action=StreamAction.ENCODE,
                    codec=stereo.codec,
                    channels=stereo.channels,
                    bitrate_k=stereo.bitrate_k,
                    sample_rate=_aac_rate(track),
                    language=track.language,
                    title=keep_title,
                    default=False,
                )
            )
        if track.channels > stereo.channels:
            if track.codec in surround.copy_codecs:
                plans.append(_copied_audio(track, AudioRole.SURROUND))
            else:
                plans.append(
                    AudioPlan(
                        source_index=track.index,
                        role=AudioRole.SURROUND,
                        action=StreamAction.ENCODE,
                        codec=surround.codec,
                        channels=min(track.channels, surround.channels),
                        bitrate_k=surround.bitrate_k,
                        sample_rate=_aac_rate(track),
                        language=track.language,
                        title=keep_title,
                        default=False,
                    )
                )
    if plans:
        plans[0] = replace(plans[0], default=True)
    return tuple(plans)


def _copied_audio(track: AudioStream, role: AudioRole) -> AudioPlan:
    return AudioPlan(
        source_index=track.index,
        role=role,
        action=StreamAction.COPY,
        codec=track.codec,
        channels=track.channels,
        bitrate_k=None,
        sample_rate=None,
        language=track.language,
        title=track.title,
        default=False,
    )


def _aac_rate(track: AudioStream) -> int | None:
    """AAC tops out at 96 kHz; hi-res sources are resampled to 48 kHz."""
    if track.sample_rate is not None and track.sample_rate > 48_000:
        return 48_000
    return None


def _subtitle_plans(probe: ProbeResult) -> tuple[SubtitlePlan, ...]:
    return tuple(
        SubtitlePlan(
            source_index=s.index,
            codec=s.codec,
            language=s.language,
            title=s.title,
            forced=s.forced,
            default=s.default,
            hearing_impaired=s.hearing_impaired,
            is_text=s.is_text,
            extract_formats=("vtt", "srt") if s.is_text else (),
            burn_in_candidate=s.is_image,
        )
        for s in probe.subtitles
    )


def _hls_plan(
    probe: ProbeResult,
    video: VideoStream,
    profiles: Profiles,
    max_quality: int,
    subtitles: tuple[SubtitlePlan, ...],
) -> HlsPlan:
    hls = profiles.hls
    tonemap = video.hdr is not HdrKind.SDR
    selected = _select_rungs(video, hls, max_quality)
    default_name = _default_rung_name(selected, hls)
    rungs = []
    for name, width, height, bitrate_k, native in selected:
        rate, divisor = level_frame_rate(width, height, _source_rate(video), hls.video.level)
        rungs.append(
            HlsRungPlan(
                name=name,
                video=VideoPlan(
                    action=StreamAction.ENCODE,
                    source_index=video.index,
                    codec=hls.video.codec.value,
                    width=width,
                    height=height,
                    frame_rate=rate,
                    rate_divisor=divisor,
                    scale=(width, height) != (video.width, video.height) or video.sar != 1,
                    tonemap=tonemap,
                    deinterlace=video.interlaced,
                    ten_bit=False,
                ),
                bitrate_k=bitrate_k,
                maxrate_k=round(bitrate_k * hls.maxrate_ratio),
                bufsize_k=round(bitrate_k * hls.bufsize_ratio),
                default=name == default_name,
                native=native,
            )
        )
    return HlsPlan(
        rungs=tuple(rungs),
        audio=_hls_audio(probe, hls),
        subtitles=subtitles,
        segment_s=hls.segment_s,
    )


def _select_rungs(
    video: VideoStream, hls: HlsProfile, max_quality: int
) -> list[tuple[str, int, int, int, bool]]:
    """(name, width, height, bitrate_k, native) for each rung, tallest first.

    A rung is kept when the source fills its box in at least one dimension (so 1920x800
    scope films keep 1080p). When the source sits well between two rungs (720x480 DVD),
    a native-size rung is added with a bitrate scaled from the rung above by pixel count.
    """
    src_w, src_h = video.display_width, video.height
    ladder = [rung for rung in hls.rungs if rung.height <= max_quality] or [hls.rungs[-1]]
    kept: list[tuple[str, int, int, int, bool]] = []
    skipped: list[HlsRung] = []
    for rung in ladder:
        if src_w >= rung.width or src_h >= rung.height:
            width, height = fit_within(src_w, src_h, rung.width, rung.height)
            kept.append((rung.name, width, height, rung.bitrate_k, False))
        else:
            skipped.append(rung)
    if skipped:
        above = skipped[-1]  # the smallest box the source doesn't fill
        best_height = kept[0][2] if kept else 0
        if not kept or src_h >= best_height * hls.native_rung_ratio:
            width, height = fit_within(src_w, src_h, above.width, above.height)
            bitrate_k = round(above.bitrate_k * width * height / (above.width * above.height))
            if kept:
                bitrate_k = max(bitrate_k, kept[0][3])
            kept.insert(0, (f"{height}p", width, height, bitrate_k, True))
    return kept


def _default_rung_name(rungs: list[tuple[str, int, int, int, bool]], hls: HlsProfile) -> str:
    names = [rung[0] for rung in rungs]
    if hls.default_rung in names:
        return hls.default_rung
    configured = hls.rung(hls.default_rung)
    target = configured.height if configured else rungs[-1][2]
    return min(rungs, key=lambda rung: (abs(rung[2] - target), rung[2]))[0]


def _hls_audio(probe: ProbeResult, hls: HlsProfile) -> tuple[HlsAudioPlan, ...]:
    tracks = _ordered_audio(probe)
    plans: list[HlsAudioPlan] = []
    names: set[str] = set()
    for position, track in enumerate(tracks):
        plans.append(
            HlsAudioPlan(
                source_index=track.index,
                group=hls.audio.codec,
                action=StreamAction.ENCODE,
                codec=hls.audio.codec,
                channels=hls.audio.channels,
                bitrate_k=hls.audio.bitrate_k,
                language=track.language,
                name=_unique_name(track.title or track.language, names),
                default=position == 0,
            )
        )
    passthrough = [t for t in tracks if t.codec in hls.passthrough_audio_codecs]
    names = set()
    for position, track in enumerate(passthrough):
        plans.append(
            HlsAudioPlan(
                source_index=track.index,
                group=track.codec,
                action=StreamAction.COPY,
                codec=track.codec,
                channels=track.channels,
                bitrate_k=None,
                language=track.language,
                name=_unique_name(track.title or track.language, names),
                default=position == 0,
            )
        )
    return tuple(plans)


def _unique_name(name: str, used: set[str]) -> str:
    candidate, suffix = name, 2
    while candidate in used:
        candidate = f"{name} {suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def _uhd_plan(
    probe: ProbeResult, video: VideoStream, profiles: Profiles, max_quality: int
) -> UhdPlan | None:
    uhd = profiles.uhd
    if max_quality < 2160 or video.height < uhd.min_source_height:
        return None
    keep = uhd.keep_source_when
    bitrate = probe.video_bitrate
    reasons = []
    if video.codec not in keep.codecs:
        reasons.append("codec")
    if bitrate is None or bitrate > keep.max_bitrate_k * 1000:
        reasons.append("bitrate")
    if probe.container not in keep.containers:
        reasons.append("container")
    if video.width > uhd.max_width or video.height > uhd.max_height:
        reasons.append("resolution")
    if not reasons:
        return UhdPlan(
            mode=UhdMode.KEEP_SOURCE,
            video=VideoPlan(
                action=StreamAction.COPY,
                source_index=video.index,
                codec=video.codec,
                width=video.width,
                height=video.height,
                frame_rate=_source_rate(video),
                rate_divisor=1,
                scale=False,
                tonemap=False,
                deinterlace=False,
                ten_bit=video.bit_depth > 8,
            ),
            profile=None,
            bitrate_k=None,
            maxrate_k=None,
            bufsize_k=None,
        )
    return UhdPlan(
        mode=UhdMode.ENCODE,
        video=_encoded_video(
            video,
            uhd.video.codec,
            (uhd.max_width, uhd.max_height),
            level=None,
            ten_bit=True,
            tonemap=False,
            reasons=tuple(reasons),
        ),
        profile=uhd.video.profile,
        bitrate_k=uhd.video.bitrate_k,
        maxrate_k=uhd.video.maxrate_k,
        bufsize_k=uhd.video.bufsize_k,
        reasons=tuple(reasons),
    )


def _thumbnail_plan(probe: ProbeResult, video: VideoStream, profiles: Profiles) -> ThumbnailPlan:
    thumbs = profiles.thumbnails
    sprite = thumbs.sprite
    duration_ms = probe.duration_ms or 0
    width, height = fit_within(
        video.display_width,
        video.height,
        _even(thumbs.poster_max_height * 16 / 9),
        thumbs.poster_max_height,
    )
    tiles = max(1, math.ceil(duration_ms / (sprite.interval_s * 1000)))
    return ThumbnailPlan(
        source_index=video.index,
        poster_at_ms=round(duration_ms * thumbs.poster_at_ratio),
        poster_width=width,
        poster_height=height,
        interval_s=sprite.interval_s,
        tile_width=sprite.tile_width,
        tile_height=sprite.tile_height,
        columns=sprite.columns,
        rows=sprite.rows,
        tile_count=tiles,
        sheet_count=math.ceil(tiles / sprite.tiles_per_sheet),
        tonemap=video.hdr is not HdrKind.SDR,
        deinterlace=video.interlaced,
    )


def _warnings(probe: ProbeResult, video: VideoStream) -> tuple[str, ...]:
    warnings = []
    if probe.duration_ms is None:
        warnings.append("unknown_duration")
    if _source_rate(video) is None:
        warnings.append("unknown_frame_rate")
    if not any(a.channels > 0 for a in probe.audio):
        warnings.append("no_audio")
    if any(s.is_image for s in probe.subtitles):
        warnings.append("image_subtitles")
    if video.hdr is HdrKind.DV and video.dv_bl_compat_id == 0:
        # Dolby Vision without an HDR10/SDR base layer: colours are wrong once the
        # enhancement metadata is dropped (profile 5).
        warnings.append("dolby_vision_without_fallback")
    return tuple(warnings)
