"""ffmpeg argv builders, HLS master writer and runner (apps/media/ffmpeg.py).

No real ffmpeg here: argv lists are compared exactly, and the runner is driven by a
small Python process that speaks ffmpeg's progress protocol. test_integration.py runs
the real thing.
"""

from __future__ import annotations

import sys
import threading
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from apps.media.ffmpeg import (
    Command,
    Encoding,
    FfmpegCancelled,
    FfmpegError,
    compat_mp4_command,
    gop_size,
    h264_level_idc,
    hls_command,
    poster_command,
    sprite_command,
    subtitles_command,
    thumbnails_vtt,
    trial_encode_command,
    uhd_command,
    write_hls_master,
)
from apps.media.ffmpeg import run as run_ffmpeg
from apps.media.planner import ProcessingPlan, ProcessingPolicy, plan_processing
from apps.media.probe import ProbeResult
from apps.media.profiles import Backend, Profiles, VideoCodec
from apps.media.progress import ProgressUpdate
from apps.media.tests.builders import audio, probe_result, subtitle, video

SOURCE = Path("/srv/library/Movie (2020)/movie.mkv")
OUTPUT = Path("/srv/scratch/job-1/compat.mp4")
HEAD = [
    "ffmpeg",
    "-hide_banner",
    "-nostdin",
    "-y",
    "-loglevel",
    "error",
    "-progress",
    "pipe:1",
    "-nostats",
]
INTEL = "/dev/dri/renderD129"


def _plan(profiles: Profiles, probe: ProbeResult, max_quality: int = 1080) -> ProcessingPlan:
    return plan_processing(
        probe, policy=ProcessingPolicy.INGEST, max_quality=max_quality, profiles=profiles
    )


def _compat_argv(
    profiles: Profiles,
    probe: ProbeResult,
    backend: Backend = Backend.CPU,
    device: str | None = None,
) -> list[str]:
    plan = _plan(profiles, probe)
    assert plan.compat_mp4 is not None
    encoding = Encoding(profiles=profiles, backend=backend, device=device)
    return list(
        compat_mp4_command(
            probe, plan.compat_mp4, source=SOURCE, output=OUTPUT, encoding=encoding
        ).argv
    )


def _after(argv: list[str], flag: str, count: int) -> list[str]:
    start = argv.index(flag)
    return argv[start : start + count]


HEVC_SOURCE = probe_result(
    video(codec_name="hevc", profile="Main", level=120),
    audio(1, codec_name="ac3", channels=6),
    subtitle(2, "subrip"),
)


def test_compat_encode_on_cpu(profiles: Profiles) -> None:
    command_plan = _plan(profiles, HEVC_SOURCE).compat_mp4
    assert command_plan is not None
    command = compat_mp4_command(
        HEVC_SOURCE,
        command_plan,
        source=SOURCE,
        output=OUTPUT,
        encoding=Encoding(profiles=profiles, backend=Backend.CPU),
    )
    assert list(command.argv) == [
        *HEAD,
        "-i", "file:/srv/library/Movie (2020)/movie.mkv",
        "-map", "0:0",
        "-vf", "format=yuv420p",
        "-c:v", "libx264", "-preset", "slow", "-profile:v", "high", "-level:v", "41",
        "-crf", "20", "-maxrate", "7800k", "-bufsize", "15600k",
        "-g", "48", "-keyint_min", "48", "-sc_threshold", "0",
        "-map", "0:1", "-c:a:0", "aac", "-b:a:0", "160k", "-ac:a:0", "2",
        "-metadata:s:a:0", "language=eng", "-metadata:s:a:0", "title=",
        "-disposition:a:0", "default",
        "-map", "0:1", "-c:a:1", "copy",
        "-metadata:s:a:1", "language=eng", "-metadata:s:a:1", "title=", "-disposition:a:1", "0",
        "-map", "0:2", "-c:s:0", "mov_text",
        "-metadata:s:s:0", "language=ara", "-metadata:s:s:0", "title=", "-disposition:s:0", "0",
        "-map_metadata", "-1", "-movflags", "+faststart", "-max_muxing_queue_size", "4096",
        "-f", "mp4", "file:/srv/scratch/job-1/compat.mp4",
    ]  # fmt: skip
    assert command.outputs == (OUTPUT,)
    assert command.directories == (OUTPUT.parent,)
    assert command.duration_ms == 5_400_000
    assert ("/srv/library/Movie (2020)/movie.mkv", "<source>") in command.redactions


@pytest.mark.parametrize(
    ("backend", "device", "before_input", "filters", "encoder"),
    [
        (
            Backend.NVENC,
            None,
            [],
            "format=yuv420p",
            [
                "-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-spatial-aq", "1",
                "-profile:v", "high", "-level:v", "41",
                "-rc", "vbr", "-cq", "21", "-b:v", "0", "-maxrate", "7800k", "-bufsize", "15600k",
                "-g", "48", "-keyint_min", "48", "-no-scenecut", "1", "-forced-idr", "1",
            ],
        ),
        (
            Backend.QSV,
            INTEL,
            ["-init_hw_device", f"qsv=qsv:hw_any,child_device={INTEL}", "-filter_hw_device", "qsv"],
            "format=nv12,hwupload=extra_hw_frames=64",
            [
                "-c:v", "h264_qsv", "-preset", "slower", "-extbrc", "1", "-look_ahead_depth", "40",
                "-profile:v", "high", "-level:v", "41",
                "-global_quality", "21", "-b:v", "5000k", "-maxrate", "7800k", "-bufsize", "15600k",
                "-g", "48", "-idr_interval", "0", "-adaptive_i", "0", "-adaptive_b", "0",
            ],
        ),
        (
            Backend.VAAPI,
            INTEL,
            ["-vaapi_device", INTEL],
            "format=nv12,hwupload",
            [
                "-c:v", "h264_vaapi", "-profile:v", "high", "-level:v", "41",
                "-rc_mode", "QVBR", "-global_quality", "21", "-b:v", "5000k",
                "-maxrate", "7800k", "-bufsize", "15600k", "-g", "48", "-idr_interval", "0",
            ],
        ),
    ],
)  # fmt: skip
def test_compat_encode_on_hardware(  # noqa: PLR0917
    profiles: Profiles,
    backend: Backend,
    device: str | None,
    before_input: list[str],
    filters: str,
    encoder: list[str],
) -> None:
    argv = _compat_argv(profiles, HEVC_SOURCE, backend, device)
    assert argv[len(HEAD) : argv.index("-i")] == before_input
    assert argv[argv.index("-vf") + 1] == filters
    assert _after(argv, "-c:v", len(encoder)) == encoder


def test_hardware_backends_need_a_device(profiles: Profiles) -> None:
    with pytest.raises(ValueError, match="needs a device"):
        _compat_argv(profiles, HEVC_SOURCE, Backend.VAAPI, None)


def test_remux_copies_video_without_hardware(profiles: Profiles) -> None:
    source = probe_result(video(), audio(1, codec_name="dts", channels=6, sample_rate="96000"))
    argv = _compat_argv(profiles, source, Backend.VAAPI, None)  # no device needed to copy
    assert "-vf" not in argv
    assert "-vaapi_device" not in argv
    assert _after(argv, "-map", 4) == ["-map", "0:0", "-c:v", "copy"]
    assert _after(argv, "-c:a:0", 8) == [
        "-c:a:0",
        "aac",
        "-b:a:0",
        "160k",
        "-ac:a:0",
        "2",
        "-ar:a:0",
        "48000",
    ]
    assert _after(argv, "-c:a:1", 8) == [
        "-c:a:1",
        "aac",
        "-b:a:1",
        "384k",
        "-ac:a:1",
        "6",
        "-ar:a:1",
        "48000",
    ]


@pytest.mark.parametrize(
    ("overrides", "filters", "gop", "extra"),
    [
        (
            {
                "codec_name": "hevc",
                "profile": "Main 10",
                "pix_fmt": "yuv420p10le",
                "color_transfer": "smpte2084",
            },
            "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
            "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p",
            "48",
            ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"],
        ),
        ({"level": 42, "avg_frame_rate": "60000/1001"}, "fps=30000/1001,format=yuv420p", "60", []),
        (
            {"level": 42, "avg_frame_rate": "0/0", "r_frame_rate": "0/0"},
            "format=yuv420p",
            "120",
            ["-force_key_frames", "expr:gte(t,n_forced*2)"],
        ),
        (
            {
                "codec_name": "mpeg2video",
                "width": 720,
                "height": 480,
                "sample_aspect_ratio": "32:27",
                "field_order": "tt",
                "avg_frame_rate": "30000/1001",
            },
            "bwdif=mode=send_frame:parity=auto:deint=interlaced,scale=852:480,setsar=1,format=yuv420p",
            "60",
            [],
        ),
    ],
)
def test_video_transforms(
    profiles: Profiles, overrides: dict[str, Any], filters: str, gop: str, extra: list[str]
) -> None:
    argv = _compat_argv(profiles, probe_result(video(**overrides), audio()))
    assert argv[argv.index("-vf") + 1] == filters
    assert argv[argv.index("-g") + 1] == gop
    if extra:
        assert _after(argv, extra[0], len(extra)) == extra


def test_audio_and_subtitle_metadata(profiles: Profiles) -> None:
    source = probe_result(
        video(),
        audio(1, tags={"language": "ara", "title": "Arabic"}),
        subtitle(
            2,
            "ass",
            tags={"language": "eng", "title": "Signs"},
            disposition={"default": 1, "forced": 1},
        ),
    )
    argv = _compat_argv(profiles, source)
    assert _after(argv, "-metadata:s:a:0", 6) == [
        "-metadata:s:a:0", "language=ara", "-metadata:s:a:0", "title=Arabic",
        "-disposition:a:0", "default",
    ]  # fmt: skip
    assert _after(argv, "-disposition:s:0", 2) == ["-disposition:s:0", "default+forced"]
    assert "title=Signs" in argv


TWO_RUNGS = probe_result(
    video(width=960, height=540),
    audio(1),
    audio(2, codec_name="eac3", channels=6, tags={"language": "fra"}, disposition={}),
    subtitle(3, "subrip", disposition={"forced": 1}),
    duration="5.000",
)


def test_hls_command(profiles: Profiles) -> None:
    plan = _plan(profiles, TWO_RUNGS).hls
    assert plan is not None
    out = Path("/srv/scratch/job-1/hls")
    command = hls_command(
        TWO_RUNGS,
        plan,
        output_dir=out,
        source=SOURCE,
        encoding=Encoding(profiles=profiles, backend=Backend.CPU),
    )
    argv = list(command.argv)
    assert argv[argv.index("-filter_complex") + 1] == (
        "[0:0]split=2[s0][s1];[s0]format=yuv420p[v0];[s1]scale=640:360,setsar=1,format=yuv420p[v1]"
    )

    def muxer(directory: str) -> list[str]:
        return [
            "-max_muxing_queue_size", "4096", "-f", "hls", "-hls_time", "6",
            "-hls_playlist_type", "vod", "-hls_segment_type", "fmp4",
            "-hls_flags", "independent_segments", "-hls_fmp4_init_filename", "init.mp4",
            "-hls_segment_filename", f"file:{out}/{directory}/seg_%05d.m4s",
            f"file:{out}/{directory}/index.m3u8",
        ]  # fmt: skip

    def section(first: str, expected: list[str]) -> list[str]:
        start = argv.index(first) - 1
        return argv[start : start + len(expected)]

    rung0 = [
        "-map", "[v0]", "-c:v", "libx264", "-preset", "slow",
        "-profile:v", "high", "-level:v", "41",
        "-b:v", "2000k", "-maxrate", "2140k", "-bufsize", "3000k",
        "-g", "48", "-keyint_min", "48", "-sc_threshold", "0", *muxer("v0"),
    ]  # fmt: skip
    assert section("[v0]", rung0) == rung0
    rung1 = [
        "-map", "[v1]", "-c:v", "libx264", "-preset", "slow",
        "-profile:v", "high", "-level:v", "41",
        "-b:v", "730k", "-maxrate", "781k", "-bufsize", "1095k",
    ]  # fmt: skip
    assert section("[v1]", rung1) == rung1
    audio0 = ["-map", "0:1", "-c:a", "aac", "-b:a", "160k", "-ac", "2", *muxer("a0")]
    assert section("0:1", audio0) == audio0
    eac3 = ["-map", "0:2", "-c:a", "copy", *muxer("a2")]
    end = argv.index(f"file:{out}/a2/index.m3u8") + 1
    assert argv[end - len(eac3) : end] == eac3
    assert argv[-7:] == [
        "-map",
        "0:3",
        "-c:s",
        "webvtt",
        "-f",
        "webvtt",
        f"file:{out}/s0/subtitles.vtt",
    ]
    assert command.outputs == (out,)
    assert set(command.directories) == {
        out,
        *(out / d for d in ("v0", "v1", "a0", "a1", "a2", "s0")),
    }


def test_single_rung_graph_has_no_split(profiles: Profiles) -> None:
    source = probe_result(video(width=640, height=360), audio())
    plan = _plan(profiles, source).hls
    assert plan is not None
    command = hls_command(
        source,
        plan,
        output_dir=Path("/x"),
        source=SOURCE,
        encoding=Encoding(profiles=profiles, backend=Backend.VAAPI, device=INTEL),
    )
    argv = list(command.argv)
    assert argv[argv.index("-filter_complex") + 1] == "[0:0]format=nv12,hwupload[v0]"
    assert _after(argv, "-vaapi_device", 2) == ["-vaapi_device", INTEL]


def _write_rendition(
    directory: Path, sizes: list[int], durations: list[float], init: bytes
) -> None:
    directory.mkdir(parents=True)
    lines = ["#EXTM3U", "#EXT-X-VERSION:7", "#EXT-X-TARGETDURATION:2", '#EXT-X-MAP:URI="init.mp4"']
    for number, (size, duration) in enumerate(zip(sizes, durations, strict=True)):
        name = f"seg_{number:05d}.m4s"
        (directory / name).write_bytes(b"\0" * size)
        lines += [f"#EXTINF:{duration:.6f},", name]
    (directory / "index.m3u8").write_text("\n".join([*lines, "#EXT-X-ENDLIST", ""]))
    (directory / "init.mp4").write_bytes(init)


def test_write_hls_master(profiles: Profiles, tmp_path: Path) -> None:
    plan = _plan(profiles, TWO_RUNGS).hls
    assert plan is not None
    durations = [2.0, 2.0, 1.0]
    _write_rendition(
        tmp_path / "v0", [5000, 2500, 1000], durations, b"\0\0\0\x1bavcC\x01\x64\x00\x1f\xff"
    )
    _write_rendition(tmp_path / "v1", [2000, 1000, 500], durations, b"no codec box")
    _write_rendition(tmp_path / "a0", [400, 400, 400], durations, b"")
    _write_rendition(tmp_path / "a1", [400, 400, 200], durations, b"")
    _write_rendition(tmp_path / "a2", [1000, 1000, 1000], durations, b"")

    text = write_hls_master(plan, tmp_path, duration_ms=5000)

    assert text == (tmp_path / "master.m3u8").read_text()
    assert text == "\n".join(
        [
            "#EXTM3U",
            "#EXT-X-VERSION:7",
            "#EXT-X-INDEPENDENT-SEGMENTS",
            "",
            '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aac",LANGUAGE="en",NAME="eng",DEFAULT=YES,AUTOSELECT=YES,CHANNELS="2",URI="a0/index.m3u8"',
            '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aac",LANGUAGE="fr",NAME="fra",DEFAULT=NO,AUTOSELECT=YES,CHANNELS="2",URI="a1/index.m3u8"',
            '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="eac3",LANGUAGE="fr",NAME="fra",DEFAULT=YES,AUTOSELECT=YES,CHANNELS="6",URI="a2/index.m3u8"',
            '#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",LANGUAGE="ar",NAME="ara",DEFAULT=NO,AUTOSELECT=YES,FORCED=YES,URI="s0/index.m3u8"',
            "",
            '#EXT-X-STREAM-INF:BANDWIDTH=23200,AVERAGE-BANDWIDTH=15520,CODECS="avc1.64001f,mp4a.40.2",RESOLUTION=960x540,FRAME-RATE=23.976,AUDIO="aac",SUBTITLES="subs"',
            "v0/index.m3u8",
            '#EXT-X-STREAM-INF:BANDWIDTH=11200,AVERAGE-BANDWIDTH=7520,CODECS="avc1.640029,mp4a.40.2",RESOLUTION=640x360,FRAME-RATE=23.976,AUDIO="aac",SUBTITLES="subs"',
            "v1/index.m3u8",
            '#EXT-X-STREAM-INF:BANDWIDTH=28000,AVERAGE-BANDWIDTH=18400,CODECS="avc1.64001f,ec-3",RESOLUTION=960x540,FRAME-RATE=23.976,AUDIO="eac3",SUBTITLES="subs"',
            "v0/index.m3u8",
            '#EXT-X-STREAM-INF:BANDWIDTH=16000,AVERAGE-BANDWIDTH=10400,CODECS="avc1.640029,ec-3",RESOLUTION=640x360,FRAME-RATE=23.976,AUDIO="eac3",SUBTITLES="subs"',
            "v1/index.m3u8",
            "",
        ]
    )
    assert (tmp_path / "s0" / "index.m3u8").read_text() == (
        "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:5\n#EXT-X-MEDIA-SEQUENCE:0\n"
        "#EXT-X-PLAYLIST-TYPE:VOD\n#EXTINF:5.000,\nsubtitles.vtt\n#EXT-X-ENDLIST\n"
    )


def test_master_without_audio(profiles: Profiles, tmp_path: Path) -> None:
    source = probe_result(video(width=640, height=360), duration="2.000")
    plan = _plan(profiles, source).hls
    assert plan is not None
    _write_rendition(tmp_path / "v0", [1000], [2.0], b"")
    text = write_hls_master(plan, tmp_path, duration_ms=2000)
    assert text.splitlines()[-2:] == [
        '#EXT-X-STREAM-INF:BANDWIDTH=4000,AVERAGE-BANDWIDTH=4000,CODECS="avc1.640029",RESOLUTION=640x360,FRAME-RATE=23.976',
        "v0/index.m3u8",
    ]


def test_uhd_command(profiles: Profiles) -> None:
    source = probe_result(video(width=3840, height=2160, level=51, bit_rate="30000000"), audio())
    plan = _plan(profiles, source, max_quality=2160)
    assert plan.uhd is not None
    assert plan.compat_mp4 is not None
    command = uhd_command(
        source,
        plan.uhd,
        plan.compat_mp4.audio,
        source=SOURCE,
        output=Path("/out/uhd.mp4"),
        encoding=Encoding(profiles=profiles, backend=Backend.CPU),
    )
    argv = list(command.argv)
    assert argv[argv.index("-vf") + 1] == "format=yuv420p10le"
    assert _after(argv, "-c:v", 18) == [
        "-c:v", "libx265", "-preset", "slow", "-profile:v", "main10",
        "-b:v", "16800k", "-maxrate", "20160k", "-bufsize", "33600k",
        "-g", "48", "-keyint_min", "48", "-x265-params", "scenecut=0:open-gop=0:log-level=error",
    ]  # fmt: skip
    assert _after(argv, "-color_primaries", 8) == [
        "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
        "-tag:v", "hvc1",
    ]  # fmt: skip
    keep = probe_result(
        video(codec_name="hevc", width=3840, height=2160, bit_rate="15000000"), audio()
    )
    keep_plan = _plan(profiles, keep, max_quality=2160)
    assert keep_plan.uhd is not None
    with pytest.raises(ValueError, match="keeps the source"):
        uhd_command(
            keep,
            keep_plan.uhd,
            (),
            source=SOURCE,
            output=Path("/out/uhd.mp4"),
            encoding=Encoding(profiles=profiles, backend=Backend.CPU),
        )


def test_thumbnail_commands(profiles: Profiles) -> None:
    plan = _plan(profiles, probe_result()).thumbnails
    poster = poster_command(plan, source=SOURCE, output=Path("/out/poster.jpg"), profiles=profiles)
    assert list(poster.argv) == [
        *HEAD, "-ss", "540.000", "-i", f"file:{SOURCE}", "-map", "0:0", "-frames:v", "1",
        "-vf", "scale=1920:1080,setsar=1,format=yuvj420p", "-c:v", "mjpeg", "-q:v", "2",
        "-f", "image2", "-update", "1", "file:/out/poster.jpg",
    ]  # fmt: skip
    sprites = sprite_command(
        plan,
        source=SOURCE,
        output_dir=Path("/out/sprites"),
        profiles=profiles,
        duration_ms=5_400_000,
    )
    argv = list(sprites.argv)
    assert argv[argv.index("-vf") + 1] == (
        "fps=1/10,scale=160:90:force_original_aspect_ratio=decrease,format=yuvj420p,"
        "pad=160:90:(ow-iw)/2:(oh-ih)/2,setsar=1,tile=10x10"
    )
    assert argv[-5:] == ["-f", "image2", "-start_number", "1", "file:/out/sprites/sprite_%03d.jpg"]
    assert [p.name for p in sprites.outputs] == [f"sprite_00{n}.jpg" for n in range(1, 7)]


def test_thumbnails_vtt(profiles: Profiles) -> None:
    short = _plan(profiles, probe_result(duration="25.000")).thumbnails
    assert thumbnails_vtt(short, 25_000) == (
        "WEBVTT\n\n"
        "00:00:00.000 --> 00:00:10.000\nsprite_001.jpg#xywh=0,0,160,90\n\n"
        "00:00:10.000 --> 00:00:20.000\nsprite_001.jpg#xywh=160,0,160,90\n\n"
        "00:00:20.000 --> 00:00:25.000\nsprite_001.jpg#xywh=320,0,160,90\n"
    )
    long = _plan(profiles, probe_result(duration="3725.500")).thumbnails
    cues = thumbnails_vtt(long, 3_725_500).split("\n\n")[1:]
    assert cues[11] == "00:01:50.000 --> 00:02:00.000\nsprite_001.jpg#xywh=160,90,160,90"
    assert cues[100] == "00:16:40.000 --> 00:16:50.000\nsprite_002.jpg#xywh=0,0,160,90"
    assert cues[-1].startswith("01:02:00.000 --> 01:02:05.500\nsprite_004.jpg#xywh=")


def test_subtitles_command(profiles: Profiles) -> None:
    source = probe_result(video(), audio(), subtitle(2, "subrip"), subtitle(3, "hdmv_pgs_subtitle"))
    plan = _plan(profiles, source)
    command = subtitles_command(plan.subtitles, source=SOURCE, output_dir=Path("/out/subs"))
    assert list(command.argv)[len(HEAD) + 2 :] == [
        "-map", "0:2", "-c:s", "webvtt", "-f", "webvtt", "file:/out/subs/2.ara.vtt",
        "-map", "0:2", "-c:s", "srt", "-f", "srt", "file:/out/subs/2.ara.srt",
    ]  # fmt: skip
    assert [p.name for p in command.outputs] == ["2.ara.vtt", "2.ara.srt"]
    with pytest.raises(ValueError, match="no text subtitles"):
        subtitles_command(plan.subtitles[1:], source=SOURCE, output_dir=Path("/out/subs"))


@pytest.mark.parametrize(
    ("backend", "codec", "device", "expected"),
    [
        (Backend.CPU, VideoCodec.H264, None, ["-vf", "format=yuv420p", "-c:v", "libx264"]),
        (Backend.CPU, VideoCodec.HEVC, None, ["-vf", "format=yuv420p10le", "-c:v", "libx265"]),
        (
            Backend.VAAPI,
            VideoCodec.HEVC,
            INTEL,
            ["-vf", "format=p010le,hwupload", "-c:v", "hevc_vaapi"],
        ),
        (Backend.NVENC, VideoCodec.H264, None, ["-vf", "format=yuv420p", "-c:v", "h264_nvenc"]),
    ],
)
def test_trial_encode_command(
    profiles: Profiles, backend: Backend, codec: VideoCodec, device: str | None, expected: list[str]
) -> None:
    argv = list(trial_encode_command(profiles, backend, codec, device=device).argv)
    assert _after(argv, "-vf", 4) == expected
    assert _after(argv, "-f", 4) == [
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=1280x720:rate=30:duration=2",
    ]
    assert argv[-3:] == ["-f", "null", "-"]
    assert "-progress" not in argv


def test_small_helpers() -> None:
    assert [h264_level_idc(level) for level in ("4.1", "4", "5.2", "3.0")] == [
        "41",
        "40",
        "52",
        "30",
    ]
    assert gop_size(Fraction(24000, 1001), 2) == 48
    assert gop_size(Fraction(25), 2) == 50
    assert gop_size(None, 2) == 120
    assert gop_size(Fraction(1, 10), 2) == 1


# --- runner -------------------------------------------------------------------------------

FAKE_FFMPEG = """
import sys, time
source = sys.argv[1]
for frame, us in ((24, 1000000), (48, 2000000)):
    block = f"frame={frame}\\nfps=48.0\\nout_time_us={us}\\nspeed=2.0x\\nprogress=continue"
    print(block, flush=True)
    time.sleep(float(sys.argv[3]))
print("frame=96\\nout_time_us=4000000\\nspeed=2.0x\\nprogress=end", flush=True)
for n in range(60):
    print(f"[mp4] line {n} reading file:{source}/movie.mkv", file=sys.stderr)
sys.exit(int(sys.argv[2]))
"""


def _fake(tmp_path: Path, exit_code: int = 0, pause_s: float = 0.0, **extra: Any) -> Command:
    source = tmp_path / "library"
    return Command(
        argv=(sys.executable, "-c", FAKE_FFMPEG, str(source), str(exit_code), str(pause_s)),
        outputs=(),
        directories=(tmp_path / "made" / "here",),
        duration_ms=4000,
        redactions=((str(source), "<source>"),),
        **extra,
    )


def test_run_reports_progress(tmp_path: Path) -> None:
    updates: list[ProgressUpdate] = []
    result = run_ffmpeg(_fake(tmp_path), on_progress=updates.append)
    assert [(u.out_time_ms, u.percent, u.done) for u in updates] == [
        (1000, 25.0, False),
        (2000, 50.0, False),
        (4000, 100.0, True),
    ]
    assert result.returncode == 0
    assert result.progress == updates[-1]
    assert (tmp_path / "made" / "here").is_dir()
    assert len(result.stderr_tail) == 50
    assert result.stderr_tail[-1] == "[mp4] line 59 reading file:<source>/movie.mkv"


def test_run_failure_keeps_a_redacted_tail(tmp_path: Path) -> None:
    with pytest.raises(FfmpegError) as error:
        run_ffmpeg(_fake(tmp_path, exit_code=1))
    assert error.value.returncode == 1
    assert not error.value.timed_out
    assert len(error.value.stderr_tail) == 50
    assert all(str(tmp_path) not in line for line in error.value.stderr_tail)
    assert "<source>" in str(error.value)


def test_run_timeout(tmp_path: Path) -> None:
    with pytest.raises(FfmpegError) as error:
        run_ffmpeg(_fake(tmp_path, pause_s=5), timeout_s=0.3)
    assert error.value.timed_out


def test_run_cancel(tmp_path: Path) -> None:
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(FfmpegCancelled):
        run_ffmpeg(_fake(tmp_path, pause_s=5), cancel=cancel)


def test_run_callback_errors_stop_ffmpeg(tmp_path: Path) -> None:
    def explode(update: ProgressUpdate) -> None:
        raise RuntimeError("db down")

    with pytest.raises(RuntimeError, match="db down"):
        run_ffmpeg(_fake(tmp_path, pause_s=5), on_progress=explode)
