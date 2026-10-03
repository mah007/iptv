"""End-to-end runs of the host's ffmpeg on tiny generated clips (testsrc2 + sine).

Covers the compat MP4 (encode and remux), a two-rung HLS ladder with its master
playlist, HDR tone mapping, thumbnails, subtitle extraction, progress parsing during a
real encode, and hardware detection followed by a real encode on every backend that
works on this host. Skipped when ffmpeg/ffprobe are not installed.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator, Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from apps.media.ffmpeg import (
    Encoding,
    compat_mp4_command,
    hls_command,
    poster_command,
    run,
    sprite_command,
    subtitle_path,
    subtitles_command,
    thumbnails_vtt,
    uhd_command,
    write_hls_master,
)
from apps.media.hwdetect import HostCapabilities, detect
from apps.media.planner import (
    ProcessingPlan,
    ProcessingPolicy,
    StreamAction,
    plan_processing,
)
from apps.media.probe import HdrKind, ProbeResult, probe
from apps.media.profiles import Backend, Profiles, VideoCodec, default_profiles
from apps.media.progress import ProgressUpdate
from apps.media.verify import (
    expected_compat,
    expected_uhd,
    keyframe_times,
    read_media_playlist,
    verify_file,
    verify_hls,
)

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed",
)

SRT = """1
00:00:00,500 --> 00:00:02,000
مرحبا بالعالم

2
00:00:03,000 --> 00:00:05,000
Second line
"""


def _ffmpeg(*args: str) -> None:
    subprocess.run(  # noqa: S603 - fixed argv
        ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", *args],  # noqa: S607
        check=True,
        timeout=120,
    )


def _lavfi_video(size: str, seconds: int) -> list[str]:
    return ["-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30:duration={seconds}"]


def _lavfi_tone(frequency: int, seconds: int) -> list[str]:
    return ["-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=48000:duration={seconds}"]


class Clips:
    """Source clips generated once per module."""

    def __init__(self, root: Path) -> None:
        self.root = root
        srt = root / "subs.ara.srt"
        srt.write_text(SRT, encoding="utf-8")
        # HEVC + AC-3 5.1 (default) + AAC stereo + an SRT track: needs a full encode.
        self.feature = root / "Feature (2020).mkv"
        _ffmpeg(
            *_lavfi_video("1280x720", 6),
            *_lavfi_tone(440, 6),
            *_lavfi_tone(660, 6),
            "-i", str(srt),
            "-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3:s",
            "-c:v", "libx265", "-preset", "ultrafast", "-x265-params", "log-level=error",
            "-pix_fmt", "yuv420p",
            "-c:a:0", "ac3", "-ac:a:0", "6", "-b:a:0", "384k",
            "-c:a:1", "aac", "-ac:a:1", "2", "-b:a:1", "128k",
            "-c:s", "srt",
            "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=ara",
            "-metadata:s:s:0", "language=ara",
            "-disposition:a:0", "default", "-disposition:a:1", "0",
            str(self.feature),
        )  # fmt: skip
        # H.264 High@4.0 + AAC in MKV: only the container is wrong (remux).
        self.remux = root / "remux.mkv"
        _ffmpeg(
            *_lavfi_video("1280x720", 4),
            *_lavfi_tone(440, 4),
            "-c:v", "libx264", "-preset", "veryfast", "-profile:v", "high", "-level:v", "4.0",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
            "-metadata:s:a:0", "language=eng",
            str(self.remux),
        )  # fmt: skip
        # 540p: a two-rung ladder (540p + 360p), AAC + E-AC-3 audio, one subtitle.
        self.ladder = root / "ladder.mkv"
        _ffmpeg(
            *_lavfi_video("960x540", 6),
            *_lavfi_tone(440, 6),
            *_lavfi_tone(660, 6),
            "-i", str(srt),
            "-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3:s",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a:0", "aac", "-ac:a:0", "2", "-b:a:0", "128k",
            "-c:a:1", "eac3", "-ac:a:1", "6", "-b:a:1", "384k",
            "-c:s", "srt",
            "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=fra",
            "-metadata:s:s:0", "language=ara",
            "-disposition:a:0", "default", "-disposition:a:1", "0",
            str(self.ladder),
        )  # fmt: skip
        # 10-bit HEVC flagged HDR10 (PQ, BT.2020): exercises the tone-mapping chain.
        self.hdr = root / "hdr.mkv"
        _ffmpeg(
            *_lavfi_video("640x360", 2),
            "-c:v", "libx265", "-preset", "ultrafast", "-pix_fmt", "yuv420p10le",
            "-x265-params",
            "log-level=error:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc",
            str(self.hdr),
        )  # fmt: skip


@pytest.fixture(scope="module")
def clips() -> Iterator[Clips]:
    with tempfile.TemporaryDirectory(prefix="iptv-media-it-") as directory:
        yield Clips(Path(directory))


@pytest.fixture
def out(clips: Clips) -> Iterator[Path]:
    with tempfile.TemporaryDirectory(dir=clips.root) as directory:
        yield Path(directory)


def _plan(source: ProbeResult, profiles: Profiles) -> ProcessingPlan:
    return plan_processing(
        source, policy=ProcessingPolicy.INGEST, max_quality=1080, profiles=profiles
    )


def _stream_entries(path: Path, kind: str, entries: str) -> list[dict[str, object]]:
    completed = subprocess.run(  # noqa: S603
        [  # noqa: S607
            "ffprobe", "-v", "error", "-select_streams", kind, "-show_entries",
            f"stream={entries}:stream_tags=language", "-of", "json", str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )  # fmt: skip
    streams: list[dict[str, object]] = json.loads(completed.stdout)["streams"]
    return streams


def _assert_two_second_gops(times: Sequence[float], duration_s: float) -> None:
    expected = [2.0 * n for n in range(int(duration_s // 2) + (duration_s % 2 > 0))]
    assert len(times) == len(expected), times
    for got, want in zip(times, expected, strict=True):
        assert abs(got - want) < 0.05, times


def test_compat_mp4_encode_with_progress(clips: Clips, out: Path, profiles: Profiles) -> None:
    source = probe(clips.feature)
    assert source.video is not None
    assert (source.video.codec, source.container) == ("hevc", "mkv")
    plan = _plan(source, profiles).compat_mp4
    assert plan is not None
    assert plan.video.action is StreamAction.ENCODE
    output = out / "compat.mp4"
    command = compat_mp4_command(
        source, plan, source=clips.feature, output=output, encoding=Encoding(profiles, Backend.CPU)
    )
    updates: list[ProgressUpdate] = []
    result = run(command, on_progress=updates.append, timeout_s=300)

    assert updates
    assert updates[-1].done
    assert updates[-1].percent == 100.0
    times = [u.out_time_ms for u in updates if u.out_time_ms is not None]
    assert times == sorted(times)
    assert times[-1] >= 5_900
    assert result.progress == updates[-1]

    produced = verify_file(output, expected_compat(plan, source.duration_ms))
    video = produced.video
    assert video is not None
    assert (video.codec, video.profile, video.level, video.pix_fmt) == (
        "h264",
        "High",
        41,
        "yuv420p",
    )
    assert (video.width, video.height) == (1280, 720)
    assert produced.faststart is True
    assert [(a.codec, a.channels, a.language) for a in produced.audio] == [
        ("aac", 2, "eng"),
        ("ac3", 6, "eng"),
        ("aac", 2, "ara"),
    ]
    assert produced.audio[0].default
    assert not produced.audio[1].default
    assert [(s.codec, s.language) for s in produced.subtitles] == [("mov_text", "ara")]
    assert source.duration_ms is not None
    _assert_two_second_gops(keyframe_times(output), source.duration_ms / 1000)
    # The stored probe and the error tail never carry the scratch path.
    assert str(out) not in json.dumps(produced.raw)


def test_remux_copies_the_video(clips: Clips, out: Path, profiles: Profiles) -> None:
    source = probe(clips.remux)
    plan = _plan(source, profiles).compat_mp4
    assert plan is not None
    assert plan.is_remux
    output = out / "remux.mp4"
    run(
        compat_mp4_command(
            source,
            plan,
            source=clips.remux,
            output=output,
            encoding=Encoding(profiles, Backend.CPU),
        )
    )
    produced = verify_file(output, expected_compat(plan, source.duration_ms))
    assert source.video is not None
    assert produced.video is not None
    assert (produced.video.profile, produced.video.level) == ("High", 40)
    assert (produced.video.width, produced.video.height) == (1280, 720)
    assert [a.codec for a in produced.audio] == ["aac"]


def test_hls_two_rung_ladder(clips: Clips, out: Path, profiles: Profiles) -> None:
    source = probe(clips.ladder)
    # 2 s segments so a 6 s clip shows how segments are cut on the 2 s keyframes.
    short = replace(profiles, hls=replace(profiles.hls, segment_s=2))
    plan = _plan(source, short).hls
    assert plan is not None
    assert [(r.name, r.width, r.height) for r in plan.rungs] == [
        ("540p", 960, 540),
        ("360p", 640, 360),
    ]
    command = hls_command(
        source, plan, output_dir=out, source=clips.ladder, encoding=Encoding(short, Backend.CPU)
    )
    run(command, timeout_s=300)
    text = write_hls_master(plan, out, duration_ms=source.duration_ms)

    master = verify_hls(out, expected_duration_ms=source.duration_ms)
    assert master.independent_segments
    variants = [variant.attributes for variant in master.variants]
    assert [v["RESOLUTION"] for v in variants] == ["960x540", "640x360", "960x540", "640x360"]
    assert [v["AUDIO"] for v in variants] == ["aac", "aac", "eac3", "eac3"]
    assert variants[0]["CODECS"] == "avc1.640029,mp4a.40.2"  # High@4.1 from the init segment
    assert variants[2]["CODECS"] == "avc1.640029,ec-3"
    for variant in variants:
        assert variant["FRAME-RATE"] == "30.000"
        assert variant["SUBTITLES"] == "subs"
        assert 0 < int(variant["AVERAGE-BANDWIDTH"]) <= int(variant["BANDWIDTH"])
    # The 540p rung's video bitrate is near its 2000k target, the 360p one near 730k.
    assert int(variants[0]["AVERAGE-BANDWIDTH"]) > int(variants[1]["AVERAGE-BANDWIDTH"])
    media = {(m["TYPE"], m["GROUP-ID"], m.get("LANGUAGE")) for m in master.media}
    assert media == {
        ("AUDIO", "aac", "en"),
        ("AUDIO", "aac", "fr"),
        ("AUDIO", "eac3", "fr"),
        ("SUBTITLES", "subs", "ar"),
    }
    # Segments are cut on the 2 s keyframes, identically in every rung (ABR switching);
    # a start offset in the source (audio priming) may leave a one-frame tail segment.
    cuts = []
    for rung in ("v0", "v1"):
        playlist = read_media_playlist(out / rung / "index.m3u8")
        assert playlist.independent_segments
        assert playlist.playlist_type == "VOD"
        durations = [round(s.duration_s, 2) for s in playlist.segments]
        assert durations[:3] == [2.0, 2.0, 2.0]
        assert all(d < 0.1 for d in durations[3:])
        cuts.append(durations)
    assert cuts[0] == cuts[1]
    assert (out / "s0" / "subtitles.vtt").read_text(encoding="utf-8").startswith("WEBVTT")
    assert "مرحبا بالعالم" in (out / "s0" / "subtitles.vtt").read_text(encoding="utf-8")
    assert text == (out / "master.m3u8").read_text()
    eac3 = probe(out / "a2" / "init.mp4")
    assert [a.codec for a in eac3.audio] == ["eac3"]


def test_hdr_is_tone_mapped_to_sdr(clips: Clips, out: Path, profiles: Profiles) -> None:
    source = probe(clips.hdr)
    assert source.video is not None
    assert source.video.hdr is HdrKind.HDR10
    plan = _plan(source, profiles).compat_mp4
    assert plan is not None
    assert plan.video.tonemap
    output = out / "sdr.mp4"
    run(
        compat_mp4_command(
            source, plan, source=clips.hdr, output=output, encoding=Encoding(profiles, Backend.CPU)
        )
    )
    produced = verify_file(output, expected_compat(plan, source.duration_ms))
    assert produced.video is not None
    assert produced.video.hdr is HdrKind.SDR
    assert (produced.video.pix_fmt, produced.video.color_transfer) == ("yuv420p", "bt709")


def test_thumbnails(clips: Clips, out: Path, profiles: Profiles) -> None:
    source = probe(clips.feature)
    plan = _plan(source, profiles).thumbnails
    assert source.duration_ms is not None
    run(poster_command(plan, source=clips.feature, output=out / "poster.jpg", profiles=profiles))
    sprites = sprite_command(
        plan,
        source=clips.feature,
        output_dir=out / "sprites",
        profiles=profiles,
        duration_ms=source.duration_ms,
    )
    run(sprites)
    assert [path.is_file() for path in sprites.outputs] == [True]
    poster = _stream_entries(out / "poster.jpg", "v:0", "width,height")
    sheet = _stream_entries(sprites.outputs[0], "v:0", "width,height")
    assert (poster[0]["width"], poster[0]["height"]) == (1280, 720)
    assert (sheet[0]["width"], sheet[0]["height"]) == (1600, 900)  # 10 x 10 tiles of 160x90
    seconds, millis = divmod(source.duration_ms, 1000)
    assert thumbnails_vtt(plan, source.duration_ms).splitlines()[2:4] == [
        f"00:00:00.000 --> 00:00:{seconds:02d}.{millis:03d}",
        "sprite_001.jpg#xywh=0,0,160,90",
    ]


def test_subtitle_extraction(clips: Clips, out: Path, profiles: Profiles) -> None:
    source = probe(clips.feature)
    plan = _plan(source, profiles)
    run(subtitles_command(plan.subtitles, source=clips.feature, output_dir=out))
    (track,) = plan.subtitles
    vtt = subtitle_path(out, track, "vtt").read_text(encoding="utf-8")
    srt = subtitle_path(out, track, "srt").read_text(encoding="utf-8")
    assert vtt.startswith("WEBVTT")
    # Cue times keep the source timeline (here shifted by the audio priming offset).
    starts = [float(m) for m in re.findall(r"^(?:00:)?00:(\d\d\.\d{3}) -->", vtt, re.MULTILINE)]
    assert len(starts) == 2
    assert 0.5 <= starts[0] < 0.55
    assert 3.0 <= starts[1] < 3.05
    assert "مرحبا بالعالم" in vtt
    assert "مرحبا بالعالم" in srt
    assert re.search(r"^00:00:03,0\d\d --> 00:00:05,0\d\d$", srt, re.MULTILINE)


@pytest.fixture(scope="module")
def host() -> HostCapabilities:
    return detect(default_profiles(), timeout_s=60)


def test_hwdetect_on_this_host(host: HostCapabilities) -> None:
    summary = {
        f"{c.backend.value}/{c.codec.value}": "works" if c.works else (c.reason or "?")
        for c in host.checks
    }
    print(f"\nffmpeg {host.ffmpeg_version}; GPUs {list(host.nvidia_gpus)}")  # noqa: T201
    print(f"encoders: {json.dumps(summary)}")  # noqa: T201
    print(f"queues: {list(host.queues)}; best: {host.best.value}")  # noqa: T201
    assert host.supports(Backend.CPU, VideoCodec.H264)
    assert host.supports(Backend.CPU, VideoCodec.HEVC)
    assert host.queues[-1] == "transcode.cpu"
    assert host.best in host.backends


def test_compat_mp4_on_every_working_backend(
    clips: Clips, out: Path, profiles: Profiles, host: HostCapabilities
) -> None:
    source = probe(clips.feature)
    plan = _plan(source, profiles).compat_mp4
    assert plan is not None
    for backend in host.backends:
        output = out / f"compat-{backend.value}.mp4"
        encoding = Encoding(profiles, backend, device=host.devices.get(backend))
        run(
            compat_mp4_command(
                source, plan, source=clips.feature, output=output, encoding=encoding
            ),
            timeout_s=300,
        )
        produced = verify_file(output, expected_compat(plan, source.duration_ms))
        assert produced.video is not None
        assert (produced.video.profile, produced.video.level) == ("High", 41), backend
        assert source.duration_ms is not None
        _assert_two_second_gops(keyframe_times(output), source.duration_ms / 1000)


def test_hls_ladder_on_every_working_hardware_backend(
    clips: Clips, out: Path, profiles: Profiles, host: HostCapabilities
) -> None:
    source = probe(clips.ladder)
    plan = _plan(source, profiles).hls
    assert plan is not None
    for backend in host.backends:
        if backend is Backend.CPU:
            continue  # test_hls_two_rung_ladder
        directory = out / backend.value
        encoding = Encoding(profiles, backend, device=host.devices.get(backend))
        run(hls_command(source, plan, output_dir=directory, source=clips.ladder, encoding=encoding))
        write_hls_master(plan, directory, duration_ms=source.duration_ms)
        master = verify_hls(directory, expected_duration_ms=source.duration_ms)
        # High (0x64) @ 4.1 (0x29); the constraint byte is the encoder's (VAAPI LP: 0x0c).
        assert re.fullmatch(
            r"avc1\.64[0-9a-f]{2}29,mp4a\.40\.2", master.variants[0].attributes["CODECS"]
        ), backend


def test_uhd_hevc_main10_on_every_backend(
    clips: Clips, out: Path, profiles: Profiles, host: HostCapabilities
) -> None:
    # The UHD rules apply from 1440p and this small HEVC source could be kept as is;
    # both are relaxed here so a 360p clip exercises the Main10 encode.
    keep = replace(profiles.uhd.keep_source_when, codecs=frozenset())
    small = replace(
        profiles, uhd=replace(profiles.uhd, min_source_height=360, keep_source_when=keep)
    )
    source = probe(clips.hdr)
    plan = plan_processing(source, policy=ProcessingPolicy.INGEST, max_quality=2160, profiles=small)
    assert plan.uhd is not None
    assert plan.compat_mp4 is not None
    for backend in (b for b in host.backends if host.supports(b, VideoCodec.HEVC)):
        output = out / f"uhd-{backend.value}.mp4"
        encoding = Encoding(small, backend, device=host.devices.get(backend))
        command = uhd_command(
            source,
            plan.uhd,
            plan.compat_mp4.audio,
            source=clips.hdr,
            output=output,
            encoding=encoding,
        )
        run(command, timeout_s=300)
        produced = verify_file(
            output, expected_uhd(plan.uhd, plan.compat_mp4.audio, source.duration_ms)
        )
        assert produced.video is not None
        video = produced.video
        assert (video.codec, video.profile, video.codec_tag, video.bit_depth) == (
            "hevc",
            "Main 10",
            "hvc1",
            10,
        ), backend
        assert video.hdr is HdrKind.HDR10, backend  # PQ signalling kept
