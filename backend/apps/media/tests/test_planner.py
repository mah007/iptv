"""Transcode planning (apps/media/planner.py): table-driven against SPEC §7.3."""

from __future__ import annotations

from fractions import Fraction
from typing import Any

import pytest

from apps.media.planner import (
    AudioRole,
    OnDemandAction,
    Output,
    PlanError,
    ProcessingPlan,
    ProcessingPolicy,
    StreamAction,
    UhdMode,
    decide_on_demand,
    fit_within,
    is_direct_playable,
    level_frame_rate,
    plan_processing,
)
from apps.media.probe import ProbeResult
from apps.media.profiles import Profiles, VideoCodec
from apps.media.tests.builders import audio, probe_result, subtitle, video

INGEST = ProcessingPolicy.INGEST


def _plan(
    profiles: Profiles,
    probe: ProbeResult,
    *,
    policy: ProcessingPolicy = INGEST,
    max_quality: int = 1080,
) -> ProcessingPlan:
    return plan_processing(probe, policy=policy, max_quality=max_quality, profiles=profiles)


# --- compat MP4 video: copy rather than re-encode ------------------------------------------

UHD_HEVC: dict[str, Any] = {
    "codec_name": "hevc",
    "profile": "Main 10",
    "level": 153,
    "width": 3840,
    "height": 2160,
    "pix_fmt": "yuv420p10le",
}


@pytest.mark.parametrize(
    ("overrides", "max_quality", "action", "reasons", "size", "rate", "flags"),
    [
        ({}, 1080, StreamAction.COPY, (), (1920, 1080), Fraction(24000, 1001), ""),
        ({"profile": "Main", "level": 31}, 1080, StreamAction.COPY, (), (1920, 1080), None, ""),
        ({"level": 42}, 1080, StreamAction.ENCODE, ("level",), (1920, 1080), None, ""),
        (
            {"codec_name": "hevc", "profile": "Main", "level": 120},
            1080,
            StreamAction.ENCODE,
            ("codec",),
            (1920, 1080),
            None,
            "",
        ),
        (
            {"profile": "High 10", "pix_fmt": "yuv420p10le"},
            1080,
            StreamAction.ENCODE,
            ("profile", "pix_fmt"),
            (1920, 1080),
            None,
            "",
        ),
        (
            {"color_transfer": "smpte2084"},
            1080,
            StreamAction.ENCODE,
            ("hdr",),
            (1920, 1080),
            None,
            "tonemap",
        ),
        (
            {"level": 51, "width": 3840, "height": 2160},
            1080,
            StreamAction.ENCODE,
            ("level", "resolution"),
            (1920, 1080),
            None,
            "scale",
        ),
        ({}, 720, StreamAction.ENCODE, ("resolution",), (1280, 720), None, "scale"),
        ({}, 480, StreamAction.ENCODE, ("resolution",), (854, 480), None, "scale"),
        (
            {"level": 42, "avg_frame_rate": "60000/1001"},
            1080,
            StreamAction.ENCODE,
            ("level",),
            (1920, 1080),
            Fraction(30000, 1001),
            "",
        ),
        (
            {
                "codec_name": "mpeg2video",
                "profile": "Main",
                "level": 8,
                "width": 720,
                "height": 480,
                "sample_aspect_ratio": "32:27",
                "field_order": "tt",
                "avg_frame_rate": "30000/1001",
            },
            1080,
            StreamAction.ENCODE,
            ("codec",),
            (852, 480),
            Fraction(30000, 1001),
            "scale,deinterlace",
        ),
    ],
)
def test_compat_video_copy_or_encode(  # noqa: PLR0917
    profiles: Profiles,
    overrides: dict[str, Any],
    max_quality: int,
    action: StreamAction,
    reasons: tuple[str, ...],
    size: tuple[int, int],
    rate: Fraction | None,
    flags: str,
) -> None:
    plan = _plan(profiles, probe_result(video(**overrides), audio()), max_quality=max_quality)
    assert plan.compat_mp4 is not None
    result = plan.compat_mp4.video
    assert result.action is action
    assert result.reasons == reasons
    assert (result.width, result.height) == size
    if rate is not None:
        assert result.frame_rate == rate
    assert result.scale == ("scale" in flags)
    assert result.tonemap == ("tonemap" in flags)
    assert result.deinterlace == ("deinterlace" in flags)
    assert not result.ten_bit
    if action is StreamAction.ENCODE:
        assert result.codec == "h264"
    assert plan.compat_mp4.is_remux == (action is StreamAction.COPY)


def test_level_limits_halve_1080p60_only(profiles: Profiles) -> None:
    plan = _plan(profiles, probe_result(video(level=42, avg_frame_rate="60000/1001"), audio()))
    assert plan.compat_mp4 is not None
    assert plan.compat_mp4.video.rate_divisor == 2
    assert plan.hls is not None
    rates = {rung.name: (rung.video.frame_rate, rung.video.rate_divisor) for rung in plan.hls.rungs}
    assert rates["1080p"] == (Fraction(30000, 1001), 2)
    assert rates["720p"] == (Fraction(60000, 1001), 1)


# --- compat MP4 audio -----------------------------------------------------------------------

S, R = AudioRole.STEREO, AudioRole.SURROUND
COPY, ENC = StreamAction.COPY, StreamAction.ENCODE


@pytest.mark.parametrize(
    ("tracks", "expected"),
    [
        ([audio()], [(1, S, COPY, "aac", 2, None, None, True)]),
        ([audio(channels=1)], [(1, S, COPY, "aac", 1, None, None, True)]),
        (
            [audio(codec_name="ac3", channels=6)],
            [(1, S, ENC, "aac", 2, 160, None, True), (1, R, COPY, "ac3", 6, None, None, False)],
        ),
        (
            [audio(codec_name="eac3", channels=8)],
            [(1, S, ENC, "aac", 2, 160, None, True), (1, R, COPY, "eac3", 8, None, None, False)],
        ),
        (
            [audio(codec_name="aac", channels=6)],
            [(1, S, ENC, "aac", 2, 160, None, True), (1, R, COPY, "aac", 6, None, None, False)],
        ),
        (
            [audio(codec_name="dts", channels=6)],
            [(1, S, ENC, "aac", 2, 160, None, True), (1, R, ENC, "aac", 6, 384, None, False)],
        ),
        (
            [audio(codec_name="truehd", channels=8, sample_rate="96000")],
            [(1, S, ENC, "aac", 2, 160, 48000, True), (1, R, ENC, "aac", 6, 384, 48000, False)],
        ),
        (
            [audio(codec_name="flac", sample_rate="96000")],
            [(1, S, ENC, "aac", 2, 160, 48000, True)],
        ),
        (
            [
                audio(1, disposition={"default": 0}),
                audio(2, codec_name="ac3", channels=6, tags={"language": "fra"}),
            ],
            [
                (2, S, ENC, "aac", 2, 160, None, True),
                (2, R, COPY, "ac3", 6, None, None, False),
                (1, S, COPY, "aac", 2, None, None, False),
            ],
        ),
        ([audio(channels=0), audio(2)], [(2, S, COPY, "aac", 2, None, None, True)]),
        ([], []),
    ],
)
def test_compat_audio(
    profiles: Profiles, tracks: list[dict[str, Any]], expected: list[tuple[Any, ...]]
) -> None:
    plan = _plan(profiles, probe_result(video(), *tracks))
    assert plan.compat_mp4 is not None
    got = [
        (
            a.source_index,
            a.role,
            a.action,
            a.codec,
            a.channels,
            a.bitrate_k,
            a.sample_rate,
            a.default,
        )
        for a in plan.compat_mp4.audio
    ]
    assert got == expected


def test_compat_audio_titles(profiles: Profiles) -> None:
    tracks = [
        audio(1, codec_name="dts", channels=6, tags={"language": "eng", "title": "DTS-HD MA 5.1"}),
        audio(
            2,
            codec_name="ac3",
            tags={"language": "eng", "title": "Director"},
            disposition={"comment": 1},
        ),
    ]
    plan = _plan(profiles, probe_result(video(), *tracks))
    assert plan.compat_mp4 is not None
    # Encoded tracks lose a title that described the source codec; commentary keeps it.
    assert [a.title for a in plan.compat_mp4.audio] == [None, None, "Director"]


# --- HLS ladder -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "max_quality", "expected"),
    [
        (
            {},
            1080,
            [
                ("1080p", 1920, 1080, 6000, False, False),
                ("720p", 1280, 720, 3000, False, False),
                ("540p", 960, 540, 2000, True, False),
                ("360p", 640, 360, 730, False, False),
            ],
        ),
        (
            {"width": 1280, "height": 720},
            1080,
            [
                ("720p", 1280, 720, 3000, False, False),
                ("540p", 960, 540, 2000, True, False),
                ("360p", 640, 360, 730, False, False),
            ],
        ),
        (
            {"width": 1920, "height": 800},
            1080,
            [
                ("1080p", 1920, 800, 6000, False, False),
                ("720p", 1280, 534, 3000, False, False),
                ("540p", 960, 400, 2000, True, False),
                ("360p", 640, 266, 730, False, False),
            ],
        ),
        (
            {"width": 960, "height": 540},
            1080,
            [("540p", 960, 540, 2000, True, False), ("360p", 640, 360, 730, False, False)],
        ),
        (
            {"width": 720, "height": 480, "sample_aspect_ratio": "32:27"},
            1080,
            [("480p", 852, 480, 1578, True, True), ("360p", 640, 360, 730, False, False)],
        ),
        ({"width": 320, "height": 240}, 1080, [("240p", 320, 240, 243, True, True)]),
        (
            {"width": 3840, "height": 2160},
            2160,
            [
                ("1080p", 1920, 1080, 6000, False, False),
                ("720p", 1280, 720, 3000, False, False),
                ("540p", 960, 540, 2000, True, False),
                ("360p", 640, 360, 730, False, False),
            ],
        ),
        (
            {},
            720,
            [
                ("720p", 1280, 720, 3000, False, False),
                ("540p", 960, 540, 2000, True, False),
                ("360p", 640, 360, 730, False, False),
            ],
        ),
        ({}, 480, [("360p", 640, 360, 730, True, False)]),
    ],
)
def test_hls_rungs_never_upscale(
    profiles: Profiles, overrides: dict[str, Any], max_quality: int, expected: list[tuple[Any, ...]]
) -> None:
    plan = _plan(profiles, probe_result(video(**overrides), audio()), max_quality=max_quality)
    assert plan.hls is not None
    got = [(r.name, r.width, r.height, r.bitrate_k, r.default, r.native) for r in plan.hls.rungs]
    assert got == expected
    assert plan.hls.default_rung.default
    assert plan.hls.segment_s == 6
    for rung in plan.hls.rungs:
        assert rung.video.action is StreamAction.ENCODE
        assert rung.maxrate_k == round(rung.bitrate_k * 1.07)
        assert rung.bufsize_k == round(rung.bitrate_k * 1.5)


def test_hls_audio_groups(profiles: Profiles) -> None:
    tracks = [
        audio(1),
        audio(2, codec_name="eac3", channels=6, tags={"language": "fra"}, disposition={}),
        audio(3, tags={"language": "eng"}, disposition={}),
    ]
    plan = _plan(profiles, probe_result(video(), *tracks))
    assert plan.hls is not None
    got = [
        (a.source_index, a.group, a.action, a.codec, a.channels, a.bitrate_k, a.name, a.default)
        for a in plan.hls.audio
    ]
    assert got == [
        (1, "aac", ENC, "aac", 2, 160, "eng", True),
        (2, "aac", ENC, "aac", 2, 160, "fra", False),
        (3, "aac", ENC, "aac", 2, 160, "eng 2", False),
        (2, "eac3", COPY, "eac3", 6, None, "fra", True),
    ]


# --- UHD ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "format_name", "filename", "max_quality", "mode", "reasons"),
    [
        (
            {**UHD_HEVC, "bit_rate": "15000000"},
            "matroska,webm",
            "a.mkv",
            2160,
            UhdMode.KEEP_SOURCE,
            (),
        ),
        (
            {**UHD_HEVC, "codec_name": "av1", "bit_rate": "9000000"},
            "mov,mp4,m4a,3gp,3g2,mj2",
            "a.mp4",
            2160,
            UhdMode.KEEP_SOURCE,
            (),
        ),
        (
            {**UHD_HEVC, "bit_rate": "40000000"},
            "matroska,webm",
            "a.mkv",
            2160,
            UhdMode.ENCODE,
            ("bitrate",),
        ),
        (
            {**UHD_HEVC, "codec_name": "h264", "bit_rate": "20000000"},
            "matroska,webm",
            "a.mkv",
            2160,
            UhdMode.ENCODE,
            ("codec",),
        ),
        (
            {**UHD_HEVC, "bit_rate": "15000000"},
            "avi",
            "a.avi",
            2160,
            UhdMode.ENCODE,
            ("container",),
        ),
        (
            {**UHD_HEVC, "width": 4096, "bit_rate": "15000000"},
            "matroska,webm",
            "a.mkv",
            2160,
            UhdMode.ENCODE,
            ("resolution",),
        ),
        ({**UHD_HEVC, "bit_rate": "15000000"}, "matroska,webm", "a.mkv", 1080, None, ()),
        ({"codec_name": "hevc", "bit_rate": "5000000"}, "matroska,webm", "a.mkv", 2160, None, ()),
    ],
)
def test_uhd(  # noqa: PLR0917
    profiles: Profiles,
    overrides: dict[str, Any],
    format_name: str,
    filename: str,
    max_quality: int,
    mode: UhdMode | None,
    reasons: tuple[str, ...],
) -> None:
    source = probe_result(video(**overrides), audio(), format_name=format_name, filename=filename)
    plan = _plan(profiles, source, max_quality=max_quality)
    if mode is None:
        assert plan.uhd is None
        assert Output.UHD not in plan.run_at_ingest
        return
    assert plan.uhd is not None
    assert plan.uhd.mode is mode
    assert plan.uhd.reasons == reasons
    assert Output.UHD in plan.run_at_ingest
    if mode is UhdMode.ENCODE:
        assert plan.uhd.video.codec == "hevc"
        assert plan.uhd.video.ten_bit
        assert (plan.uhd.profile, plan.uhd.bitrate_k) == ("main10", 16800)
        assert plan.uhd.video.width <= 3840
        assert VideoCodec.HEVC in plan.required_codecs()
    else:
        assert plan.uhd.video.action is StreamAction.COPY


def test_dci_uhd_is_fitted_into_the_uhd_box(profiles: Profiles) -> None:
    source = probe_result(video(**UHD_HEVC, bit_rate="15000000") | {"width": 4096})
    plan = _plan(profiles, source, max_quality=2160)
    assert plan.uhd is not None
    assert (plan.uhd.video.width, plan.uhd.video.height) == (3840, 2024)


# --- thumbnails, subtitles, warnings --------------------------------------------------------


def test_thumbnails(profiles: Profiles) -> None:
    plan = _plan(profiles, probe_result(video(color_transfer="smpte2084"), audio()))
    thumbs = plan.thumbnails
    assert thumbs.poster_at_ms == 540_000
    assert (thumbs.poster_width, thumbs.poster_height) == (1920, 1080)
    assert (thumbs.interval_s, thumbs.tile_width, thumbs.tile_height) == (10, 160, 90)
    assert (thumbs.tile_count, thumbs.sheet_count) == (540, 6)
    assert thumbs.tonemap


def test_subtitles(profiles: Profiles) -> None:
    source = probe_result(
        video(),
        audio(),
        subtitle(2, "subrip", tags={"language": "ara"}, disposition={"forced": 1}),
        subtitle(3, "hdmv_pgs_subtitle"),
        subtitle(4, "ass", tags={"language": "eng", "title": "Signs"}),
    )
    plan = _plan(profiles, source)
    got = [
        (s.source_index, s.is_text, s.extract_formats, s.burn_in_candidate, s.forced)
        for s in plan.subtitles
    ]
    assert got == [
        (2, True, ("vtt", "srt"), False, True),
        (3, False, (), True, False),
        (4, True, ("vtt", "srt"), False, False),
    ]
    assert plan.compat_mp4 is not None
    assert [s.source_index for s in plan.compat_mp4.subtitles] == [2, 4]
    assert plan.compat_mp4.expected_streams == (1, 1, 2)
    assert plan.hls is not None
    assert [s.source_index for s in plan.hls.subtitles] == [2, 4]
    assert Output.SUBTITLES in plan.run_at_ingest
    assert "image_subtitles" in plan.warnings


@pytest.mark.parametrize(
    ("streams", "duration", "warnings"),
    [
        ((video(),), "60", ("no_audio",)),
        ((video(), audio()), None, ("unknown_duration",)),
        (
            (video(avg_frame_rate="0/0", r_frame_rate="90000/1"), audio()),
            "60",
            ("unknown_frame_rate",),
        ),
        (
            (
                video(
                    codec_name="hevc",
                    side_data_list=[
                        {
                            "side_data_type": "DOVI configuration record",
                            "dv_profile": 5,
                            "dv_bl_signal_compatibility_id": 0,
                        }
                    ],
                ),
                audio(),
            ),
            "60",
            ("dolby_vision_without_fallback",),
        ),
    ],
)
def test_warnings(
    profiles: Profiles,
    streams: tuple[dict[str, Any], ...],
    duration: str | None,
    warnings: tuple[str, ...],
) -> None:
    plan = _plan(profiles, probe_result(*streams, duration=duration))
    assert plan.warnings == warnings


def test_unknown_frame_rate_keeps_rate_unset(profiles: Profiles) -> None:
    plan = _plan(
        profiles, probe_result(video(level=42, avg_frame_rate="0/0", r_frame_rate="0/0"), audio())
    )
    assert plan.compat_mp4 is not None
    assert (plan.compat_mp4.video.frame_rate, plan.compat_mp4.video.rate_divisor) == (None, 1)


# --- policies -------------------------------------------------------------------------------


def test_policies(profiles: Profiles) -> None:
    source = probe_result(video(), audio(), subtitle())
    ingest = _plan(profiles, source, policy=ProcessingPolicy.INGEST)
    assert ingest.run_at_ingest == (
        Output.COMPAT_MP4,
        Output.HLS,
        Output.THUMBNAILS,
        Output.SUBTITLES,
    )
    on_demand = _plan(profiles, source, policy=ProcessingPolicy.ON_DEMAND)
    assert on_demand.run_at_ingest == (Output.THUMBNAILS, Output.SUBTITLES)
    assert on_demand.compat_mp4 is not None
    assert on_demand.hls is not None
    passthrough = _plan(profiles, source, policy=ProcessingPolicy.PASSTHROUGH)
    assert (passthrough.compat_mp4, passthrough.hls, passthrough.uhd) == (None, None, None)
    assert passthrough.run_at_ingest == (Output.THUMBNAILS, Output.SUBTITLES)


@pytest.mark.parametrize(
    ("policy", "streams", "codecs"),
    [
        (INGEST, (video(), audio()), {VideoCodec.H264}),  # compat is a remux, HLS encodes
        (ProcessingPolicy.ON_DEMAND, (video(), audio()), set()),
        (ProcessingPolicy.PASSTHROUGH, (video(codec_name="hevc"), audio()), set()),
    ],
)
def test_required_codecs(
    profiles: Profiles,
    policy: ProcessingPolicy,
    streams: tuple[dict[str, Any], ...],
    codecs: set[VideoCodec],
) -> None:
    assert _plan(profiles, probe_result(*streams), policy=policy).required_codecs() == codecs


def test_errors(profiles: Profiles) -> None:
    with pytest.raises(PlanError, match="no_video_stream"):
        _plan(profiles, probe_result(audio()))
    with pytest.raises(PlanError, match="max_quality"):
        _plan(profiles, probe_result(), max_quality=1440)


# --- direct play and the on-demand fallback -------------------------------------------------

MP4 = "mov,mp4,m4a,3gp,3g2,mj2"


@pytest.mark.parametrize(
    ("source", "playable"),
    [
        (probe_result(format_name=MP4, filename="a.mp4", faststart=True), True),
        (probe_result(format_name=MP4, filename="a.mp4", faststart=False), False),
        (probe_result(), False),  # MKV
        (
            probe_result(
                video(),
                audio(codec_name="ac3", channels=6),
                format_name=MP4,
                filename="a.mp4",
                faststart=True,
            ),
            False,
        ),
        (probe_result(video(), format_name=MP4, filename="a.mp4", faststart=True), True),  # silent
        (
            probe_result(
                video(codec_name="hevc"), audio(), format_name=MP4, filename="a.mp4", faststart=True
            ),
            False,
        ),
    ],
)
def test_direct_playable(profiles: Profiles, source: ProbeResult, playable: bool) -> None:
    assert is_direct_playable(source, profiles) is playable


ON_DEMAND = ProcessingPolicy.ON_DEMAND


@pytest.mark.parametrize(
    ("source", "policy", "compat_ready", "realtime", "expected"),
    [
        (
            probe_result(video(codec_name="hevc")),
            ON_DEMAND,
            True,
            (True, 0, 2),
            (OnDemandAction.SERVE_COMPAT, "compat_ready", False, False),
        ),
        (
            probe_result(video(codec_name="hevc")),
            ProcessingPolicy.PASSTHROUGH,
            False,
            (True, 0, 2),
            (OnDemandAction.SERVE_SOURCE, "passthrough", False, False),
        ),
        (
            probe_result(format_name=MP4, filename="a.mp4", faststart=True),
            ON_DEMAND,
            False,
            (True, 0, 2),
            (OnDemandAction.SERVE_SOURCE, "direct_playable", True, False),
        ),
        (
            probe_result(video(), audio(codec_name="dts", channels=6)),
            ON_DEMAND,
            False,
            (True, 0, 2),
            (OnDemandAction.REMUX, "container_or_audio", True, True),
        ),
        (
            probe_result(format_name=MP4, filename="a.mp4", faststart=False),
            ON_DEMAND,
            False,
            (False, 0, 2),
            (OnDemandAction.REMUX, "container_or_audio", True, True),
        ),
        (
            probe_result(video(codec_name="hevc")),
            ON_DEMAND,
            False,
            (True, 1, 2),
            (OnDemandAction.REALTIME, "video_needs_encode", True, False),
        ),
        (
            probe_result(video(codec_name="hevc")),
            ON_DEMAND,
            False,
            (True, 2, 2),
            (OnDemandAction.PREPARING, "realtime_at_capacity", True, True),
        ),
        (
            probe_result(video(codec_name="hevc")),
            ON_DEMAND,
            False,
            (False, 0, 2),
            (OnDemandAction.PREPARING, "realtime_disabled", True, True),
        ),
    ],
)
def test_on_demand_fallback(  # noqa: PLR0917
    profiles: Profiles,
    source: ProbeResult,
    policy: ProcessingPolicy,
    compat_ready: bool,
    realtime: tuple[bool, int, int],
    expected: tuple[OnDemandAction, str, bool, bool],
) -> None:
    plan = _plan(profiles, source, policy=policy)
    enabled, active, cap = realtime
    decision = decide_on_demand(
        plan,
        compat_ready=compat_ready,
        realtime_enabled=enabled,
        realtime_active=active,
        realtime_max=cap,
    )
    assert (
        decision.action,
        decision.reason,
        decision.ensure_compat_job,
        decision.urgent,
    ) == expected


# --- helpers --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("size", "box", "expected"),
    [
        ((1920, 1080), (1280, 720), (1280, 720)),
        ((1280, 720), (1920, 1080), (1280, 720)),
        ((1919, 1081), (1920, 1080), (1918, 1080)),
        ((3840, 1600), (1920, 1080), (1920, 800)),
        ((1, 1), (1920, 1080), (2, 2)),
    ],
)
def test_fit_within(size: tuple[int, int], box: tuple[int, int], expected: tuple[int, int]) -> None:
    assert fit_within(*size, *box) == expected


@pytest.mark.parametrize(
    ("size", "rate", "level", "expected"),
    [
        ((1920, 1080), Fraction(60000, 1001), "4.1", (Fraction(30000, 1001), 2)),
        ((1920, 1080), Fraction(30), "4.1", (Fraction(30), 1)),
        ((1280, 720), Fraction(60000, 1001), "4.1", (Fraction(60000, 1001), 1)),
        ((1920, 1080), Fraction(120), "4.1", (Fraction(30), 4)),
        ((1920, 1080), None, "4.1", (None, 1)),
        ((1920, 1080), Fraction(60), "9.9", (Fraction(60), 1)),
    ],
)
def test_level_frame_rate(
    size: tuple[int, int], rate: Fraction | None, level: str, expected: tuple[Fraction | None, int]
) -> None:
    assert level_frame_rate(*size, rate, level) == expected
