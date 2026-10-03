"""Output verification (apps/media/verify.py)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from apps.media.planner import ProcessingPolicy, plan_processing
from apps.media.probe import parse_probe
from apps.media.profiles import Profiles
from apps.media.tests.builders import audio, ffprobe_json, probe_result, subtitle, video
from apps.media.verify import (
    Expected,
    VerificationError,
    ceil_target_duration,
    check_probe,
    expected_compat,
    parse_attributes,
    parse_master_playlist,
    parse_media_playlist,
    verify_file,
    verify_hls,
)

MP4 = "mov,mp4,m4a,3gp,3g2,mj2"


@pytest.mark.parametrize(
    ("duration", "faststart", "expected", "problems"),
    [
        ("60.400", True, Expected(duration_ms=60_000, audio=1), ()),
        ("60.600", True, Expected(duration_ms=60_000, audio=1), ("duration",)),
        ("59.400", True, Expected(duration_ms=60_000, audio=1), ("duration",)),
        ("60.000", True, Expected(duration_ms=None, audio=2), ("audio_streams",)),
        ("60.000", True, Expected(duration_ms=None, audio=1, subtitle=1), ("subtitle_streams",)),
        ("60.000", True, Expected(duration_ms=None, video=0, audio=1), ("video_streams",)),
        ("60.000", True, Expected(duration_ms=None, audio=1, video_codec="hevc"), ("video_codec",)),
        (
            "60.000",
            True,
            Expected(duration_ms=None, audio=1, audio_codecs=("ac3",)),
            ("audio_codecs",),
        ),
        ("60.000", False, Expected(duration_ms=None, audio=1, faststart=True), ("faststart",)),
    ],
)
def test_check_probe(
    duration: str, faststart: bool, expected: Expected, problems: tuple[str, ...]
) -> None:
    result = probe_result(
        video(), audio(), format_name=MP4, filename="o.mp4", duration=duration, faststart=faststart
    )
    assert check_probe(result, expected) == problems


def test_unknown_duration_is_a_problem() -> None:
    result = parse_probe(ffprobe_json(video(), duration=None))
    assert check_probe(result, Expected(duration_ms=1000)) == ("duration_unknown",)


def test_expected_compat(profiles: Profiles) -> None:
    source = probe_result(video(), audio(codec_name="ac3", channels=6), subtitle())
    plan = plan_processing(
        source, policy=ProcessingPolicy.INGEST, max_quality=1080, profiles=profiles
    )
    assert plan.compat_mp4 is not None
    assert expected_compat(plan.compat_mp4, 5_400_000) == Expected(
        duration_ms=5_400_000,
        video=1,
        audio=2,
        subtitle=1,
        video_codec="h264",
        audio_codecs=("aac", "ac3"),
        faststart=True,
    )


def test_verify_file_missing_output(tmp_path: Path) -> None:
    with pytest.raises(VerificationError) as error:
        verify_file(tmp_path / "nothing.mp4", Expected(duration_ms=None))
    assert error.value.problems == ("missing_output",)


def test_parse_attributes() -> None:
    assert parse_attributes('BANDWIDTH=1000,CODECS="avc1.640029,mp4a.40.2",RESOLUTION=960x540') == {
        "BANDWIDTH": "1000",
        "CODECS": "avc1.640029,mp4a.40.2",
        "RESOLUTION": "960x540",
    }


MEDIA = """#EXTM3U
#EXT-X-VERSION:7
#EXT-X-TARGETDURATION:2
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-PLAYLIST-TYPE:VOD
#EXT-X-INDEPENDENT-SEGMENTS
#EXT-X-MAP:URI="init.mp4"
#EXTINF:2.000000,
seg_00000.m4s
#EXTINF:2.000000,
seg_00001.m4s
#EXTINF:1.500000,
seg_00002.m4s
#EXT-X-ENDLIST
"""


def test_parse_media_playlist() -> None:
    playlist = parse_media_playlist(MEDIA)
    assert playlist.target_duration == 2
    assert playlist.init_uri == "init.mp4"
    assert [s.uri for s in playlist.segments] == ["seg_00000.m4s", "seg_00001.m4s", "seg_00002.m4s"]
    assert playlist.total_s == pytest.approx(5.5)
    assert playlist.ended
    assert playlist.independent_segments
    assert playlist.playlist_type == "VOD"
    with pytest.raises(VerificationError):
        parse_media_playlist("not a playlist")
    with pytest.raises(VerificationError):
        parse_media_playlist("#EXTM3U\nseg.m4s\n")


def _package(root: Path, media: str = MEDIA, master_uri: str = "v0/index.m3u8") -> None:
    variant = root / "v0"
    variant.mkdir(parents=True)
    (variant / "index.m3u8").write_text(media)
    for name in ("init.mp4", "seg_00000.m4s", "seg_00001.m4s", "seg_00002.m4s"):
        (variant / name).write_bytes(b"x")
    (root / "master.m3u8").write_text(
        "#EXTM3U\n#EXT-X-INDEPENDENT-SEGMENTS\n"
        '#EXT-X-STREAM-INF:BANDWIDTH=1,AVERAGE-BANDWIDTH=1,CODECS="avc1.640029",RESOLUTION=2x2\n'
        f"{master_uri}\n"
    )


def test_verify_hls_accepts_a_complete_package(tmp_path: Path) -> None:
    _package(tmp_path)
    master = verify_hls(tmp_path, expected_duration_ms=5500)
    assert [v.uri for v in master.variants] == ["v0/index.m3u8"]
    assert master.independent_segments


@pytest.mark.parametrize(
    ("damage", "problem"),
    [
        (lambda root: (root / "v0" / "seg_00001.m4s").unlink(), "missing_segment"),
        (lambda root: (root / "v0" / "init.mp4").unlink(), "missing_init_segment"),
        (lambda root: (root / "master.m3u8").unlink(), "missing_master"),
        (
            lambda root: (root / "v0" / "index.m3u8").write_text(
                MEDIA.replace("#EXT-X-ENDLIST\n", "")
            ),
            "playlist_not_ended",
        ),
        (
            lambda root: (root / "v0" / "index.m3u8").write_text(
                MEDIA.replace("1.500000", "2.600000")
            ),
            "segment_exceeds_target",
        ),
        (lambda root: (root / "v0" / "index.m3u8").write_text("garbage"), "invalid_playlist"),
    ],
)
def test_verify_hls_problems(
    tmp_path: Path, damage: Callable[[Path], object], problem: str
) -> None:
    _package(tmp_path)
    damage(tmp_path)
    with pytest.raises(VerificationError) as error:
        verify_hls(tmp_path, expected_duration_ms=None)
    assert problem in error.value.problems


def test_verify_hls_duration_and_escapes(tmp_path: Path) -> None:
    _package(tmp_path / "a")
    with pytest.raises(VerificationError) as error:
        verify_hls(tmp_path / "a", expected_duration_ms=9000)
    assert error.value.problems == ("duration",)
    _package(tmp_path / "b", master_uri="../a/v0/index.m3u8")
    with pytest.raises(VerificationError) as error:
        verify_hls(tmp_path / "b", expected_duration_ms=None)
    assert error.value.problems == ("missing_playlist",)


def test_master_without_attributes(tmp_path: Path) -> None:
    master = parse_master_playlist("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=5\nv0/index.m3u8\n")
    assert master.variants[0].attributes == {"BANDWIDTH": "5"}
    _package(tmp_path)
    (tmp_path / "master.m3u8").write_text("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=5\nv0/index.m3u8\n")
    with pytest.raises(VerificationError) as error:
        verify_hls(tmp_path, expected_duration_ms=None)
    assert set(error.value.problems) == {
        "variant_average-bandwidth",
        "variant_codecs",
        "variant_resolution",
    }


def test_ceil_target_duration() -> None:
    assert ceil_target_duration([2.0, 2.0, 1.5]) == 2
    assert ceil_target_duration([6.006]) == 7
    assert ceil_target_duration([]) == 1
