"""ffprobe JSON mapping (apps/media/probe.py)."""

from __future__ import annotations

import shutil
import sys
from fractions import Fraction
from pathlib import Path

import pytest

from apps.media.probe import (
    HdrKind,
    ProbeError,
    bcp47_language,
    mp4_faststart,
    normalize_language,
    parse_probe,
    probe,
    subtitle_format,
)
from apps.media.tests.builders import audio, ffprobe_json, subtitle, video


def test_parses_a_typical_mkv() -> None:
    data = ffprobe_json(
        video(),
        audio(1),
        audio(
            2,
            codec_name="ac3",
            channels=6,
            channel_layout="5.1(side)",
            tags={"language": "fr", "title": "Français 5.1"},
            disposition={"default": 0},
        ),
        subtitle(3, "subrip", tags={"language": "ar"}),
        subtitle(4, "hdmv_pgs_subtitle", tags={}),
        video(index=5, codec_name="mjpeg", disposition={"attached_pic": 1}),
    )
    result = parse_probe(data, filename="movie.mkv")

    assert result.container == "mkv"
    assert result.duration_ms == 5_400_000
    assert result.bitrate == 8_000_000
    assert len(result.video_streams) == 1  # cover art is not a video stream
    main = result.video
    assert main is not None
    assert (main.codec, main.profile, main.level) == ("h264", "High", 40)
    assert (main.width, main.height, main.pix_fmt) == (1920, 1080, "yuv420p")
    assert main.frame_rate == Fraction(24000, 1001)
    assert main.fps == pytest.approx(23.976, abs=1e-3)
    assert main.hdr is HdrKind.SDR
    assert main.bit_depth == 8
    assert [(a.index, a.codec, a.channels, a.language) for a in result.audio] == [
        (1, "aac", 2, "eng"),
        (2, "ac3", 6, "fre"),
    ]
    assert result.audio[1].title == "Français 5.1"
    assert result.default_audio == result.audio[0]
    assert [(s.codec, s.language, s.is_text, s.is_image) for s in result.subtitles] == [
        ("subrip", "ara", True, False),
        ("hdmv_pgs_subtitle", "und", False, True),
    ]
    # The stored raw JSON never carries the storage path.
    assert "filename" not in result.raw["format"]
    assert result.raw["streams"][0]["codec_name"] == "h264"


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, HdrKind.SDR),
        ({"color_transfer": "smpte2084", "color_primaries": "bt2020"}, HdrKind.HDR10),
        ({"color_transfer": "arib-std-b67"}, HdrKind.HLG),
        ({"codec_tag_string": "dvh1", "color_transfer": "smpte2084"}, HdrKind.DV),
        (
            {
                "color_transfer": "smpte2084",
                "side_data_list": [
                    {
                        "side_data_type": "DOVI configuration record",
                        "dv_profile": 8,
                        "dv_bl_signal_compatibility_id": 1,
                    }
                ],
            },
            HdrKind.DV,
        ),
    ],
)
def test_hdr_kind(overrides: dict[str, object], expected: HdrKind) -> None:
    result = parse_probe(ffprobe_json(video(**overrides)))
    assert result.video is not None
    assert result.video.hdr is expected


def test_dolby_vision_configuration_is_kept() -> None:
    side = {"side_data_type": "DOVI configuration record", "dv_profile": 5}
    side["dv_bl_signal_compatibility_id"] = 0
    result = parse_probe(ffprobe_json(video(codec_name="hevc", side_data_list=[side])))
    assert result.video is not None
    assert (result.video.dv_profile, result.video.dv_bl_compat_id) == (5, 0)


@pytest.mark.parametrize(
    ("pix_fmt", "bits", "depth"),
    [
        ("yuv420p", None, 8),
        ("yuv420p10le", None, 10),
        ("p010le", None, 10),
        ("yuv422p12le", None, 12),
        (None, "10", 10),
        (None, None, 8),
    ],
)
def test_bit_depth(pix_fmt: str | None, bits: str | None, depth: int) -> None:
    result = parse_probe(ffprobe_json(video(pix_fmt=pix_fmt, bits_per_raw_sample=bits)))
    assert result.video is not None
    assert result.video.bit_depth == depth


@pytest.mark.parametrize(
    ("format_name", "filename", "container"),
    [
        ("matroska,webm", "a.mkv", "mkv"),
        ("matroska,webm", "a.webm", "webm"),
        ("mov,mp4,m4a,3gp,3g2,mj2", "a.mp4", "mp4"),
        ("mov,mp4,m4a,3gp,3g2,mj2", "a.MOV", "mov"),
        ("mpegts", "a.ts", "ts"),
        ("avi", "a.avi", "avi"),
        ("flv", "a.flv", "flv"),
    ],
)
def test_container(format_name: str, filename: str, container: str) -> None:
    assert (
        parse_probe(ffprobe_json(video(), format_name=format_name), filename=filename).container
        == container
    )


def test_duration_falls_back_to_stream_tags_and_bad_numbers_are_none() -> None:
    data = ffprobe_json(
        video(tags={"DURATION": "01:30:00.500000000"}, bit_rate="N/A"),
        audio(bit_rate="inf", tags={"BPS-eng": "640000"}),
        duration=None,
        bit_rate="inf",
    )
    result = parse_probe(data)
    assert result.duration_ms == 5_400_500
    assert result.bitrate is None
    assert result.video is not None
    assert result.video.bitrate is None
    assert result.audio[0].bitrate == 640_000
    # Without a stream bitrate, the size over the duration bounds the video bitrate.
    assert result.video_bitrate == 5_400_000_000 * 8 * 1000 // 5_400_500


def test_anamorphic_and_interlaced_video() -> None:
    result = parse_probe(
        ffprobe_json(
            video(
                codec_name="mpeg2video",
                width=720,
                height=480,
                sample_aspect_ratio="32:27",
                field_order="tt",
            )
        )
    )
    main = result.video
    assert main is not None
    assert main.display_width == 853
    assert main.interlaced


def test_streams_without_size_are_skipped_and_no_video_is_none() -> None:
    result = parse_probe(ffprobe_json(video(width=0), audio()))
    assert result.video is None
    assert result.video_streams == ()


def test_summary_has_the_media_file_columns() -> None:
    result = parse_probe(ffprobe_json(video(), audio(), subtitle()), filename="m.mkv")
    summary = result.summary()
    assert summary["video_codec"] == "h264"
    assert summary["fps"] == pytest.approx(23.976)
    assert summary["hdr"] == "sdr"
    assert summary["audio"][0]["language"] == "eng"
    assert summary["subtitles"][0] == {
        "stream_index": 2,
        "codec": "subrip",
        "language": "ara",
        "title": None,
        "default": False,
        "forced": False,
        "format": "srt",
        "text": True,
    }


@pytest.mark.parametrize(
    ("tag", "code"),
    [
        ("en", "eng"),
        ("en-US", "eng"),
        ("ara", "ara"),
        ("AR", "ara"),
        ("", "und"),
        (None, "und"),
        ("x1", "und"),
    ],
)
def test_normalize_language(tag: str | None, code: str) -> None:
    assert normalize_language(tag) == code


@pytest.mark.parametrize(
    ("code", "tag"), [("eng", "en"), ("fre", "fr"), ("fra", "fr"), ("und", None), ("tlh", "tlh")]
)
def test_bcp47_language(code: str, tag: str | None) -> None:
    assert bcp47_language(code) == tag


def test_subtitle_format() -> None:
    assert subtitle_format("subrip") == "srt"
    assert subtitle_format("ass") == "ass"
    assert subtitle_format("hdmv_pgs_subtitle") == "pgs"
    assert subtitle_format("dvd_subtitle") is None


def _box(kind: bytes, payload: bytes = b"") -> bytes:
    return (8 + len(payload)).to_bytes(4, "big") + kind + payload


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (_box(b"ftyp", b"isom") + _box(b"moov") + _box(b"mdat", b"x" * 16), True),
        (_box(b"ftyp", b"isom") + _box(b"mdat", b"x" * 16) + _box(b"moov"), False),
        (_box(b"ftyp", b"isom") + _box(b"free") + _box(b"moov"), True),
        (
            (1).to_bytes(4, "big") + b"mdat" + (24).to_bytes(8, "big") + b"y" * 8 + _box(b"moov"),
            False,
        ),
        (b"\x00\x00\x00\x04junk", None),
        (b"", None),
    ],
)
def test_mp4_faststart(tmp_path: Path, content: bytes, expected: bool | None) -> None:
    file = tmp_path / "f.mp4"
    file.write_bytes(content)
    assert mp4_faststart(file) is expected


def test_mp4_faststart_missing_file(tmp_path: Path) -> None:
    assert mp4_faststart(tmp_path / "missing.mp4") is None


def test_probe_reports_a_missing_binary(tmp_path: Path) -> None:
    with pytest.raises(ProbeError, match="not found"):
        probe(tmp_path / "x.mkv", ffprobe=str(tmp_path / "no-ffprobe"))


def test_probe_reports_bad_json(tmp_path: Path) -> None:
    fake = tmp_path / "fake-ffprobe"
    fake.write_text(f"#!{sys.executable}\nprint('not json')\n")
    fake.chmod(0o755)
    with pytest.raises(ProbeError, match="invalid JSON"):
        probe(tmp_path / "x.mkv", ffprobe=str(fake))


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe not installed")
def test_probe_failure_does_not_leak_the_path(tmp_path: Path) -> None:
    secret = tmp_path / "library" / "Secret Movie (2020).mkv"
    secret.parent.mkdir()
    secret.write_bytes(b"not a media file at all")
    with pytest.raises(ProbeError) as error:
        probe(secret)
    assert str(tmp_path) not in str(error.value)
    assert "Secret Movie" not in str(error.value)
