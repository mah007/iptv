"""HLS masters (ADR-0014): codec strings from init segments, measured bandwidths and the
master and subtitle playlist text."""

from pathlib import Path

import pytest

from apps.media.hls import (
    AudioEntry,
    SubtitleEntry,
    VariantEntry,
    hevc_codec_string,
    language_name,
    master_playlist,
    playlist_bitrates,
    subtitle_playlist,
    video_codec_string,
)
from apps.media.verify import parse_master_playlist, parse_media_playlist

# HEVCDecoderConfigurationRecord: version 1, Main 10 (profile 2) main tier, compatibility
# flags 0x20000000, constraint flags B0 00.., level 153 (5.1).
MAIN10_RECORD = bytes([1, 0x02, 0x20, 0, 0, 0, 0xB0, 0, 0, 0, 0, 0, 153])


def variant(name: str, height: int, **fields: object) -> VariantEntry:
    values: dict[str, object] = {
        "uri": f"{name}/index.m3u8",
        "width": height * 16 // 9,
        "height": height,
        "frame_rate": 23.976,
        "codecs": "avc1.640028",
        "peak_bps": height * 1000,
        "average_bps": height * 900,
    }
    values.update(fields)
    return VariantEntry(**values)  # type: ignore[arg-type]


def audio(uri: str, language: str, *, group: str = "aac", default: bool = False) -> AudioEntry:
    return AudioEntry(
        uri=uri, group=group, language=language, name=language_name(language), default=default,
        channels=2 if group == "aac" else 6, codec=group, peak_bps=170_000, average_bps=160_000,
    )  # fmt: skip


def test_hevc_codec_strings() -> None:
    assert hevc_codec_string(MAIN10_RECORD) == "hvc1.2.4.L153.B0"
    high_tier_main = bytes([1, 0x21, 0x60, 0, 0, 0, 0x90, 0, 0, 0, 0, 0, 120])
    assert hevc_codec_string(high_tier_main, "hev1") == "hev1.1.6.H120.90"
    profile_space = bytes([1, 0x42, 0x20, 0, 0, 0, 0, 0, 0, 0, 0, 0, 93])
    assert hevc_codec_string(profile_space) == "hvc1.A2.4.L93"


def test_video_codec_string_reads_avcc_and_hvcc(tmp_path: Path) -> None:
    avc = tmp_path / "avc.mp4"
    avc.write_bytes(b"\x00\x00\x00\x10ftyp....avcC\x01\x64\x00\x28\xff")
    assert video_codec_string(avc) == "avc1.640028"
    hevc = tmp_path / "hevc.mp4"
    hevc.write_bytes(b"....hvc1........hvcC" + MAIN10_RECORD)
    assert video_codec_string(hevc, "hevc") == "hvc1.2.4.L153.B0"
    assert video_codec_string(tmp_path / "missing.mp4", "hevc").startswith("hvc1.")
    assert video_codec_string(tmp_path / "missing.mp4") == "avc1.640029"


def test_playlist_bitrates(tmp_path: Path) -> None:
    (tmp_path / "a.m4s").write_bytes(b"x" * 6000)
    (tmp_path / "b.m4s").write_bytes(b"x" * 1000)
    playlist = tmp_path / "index.m3u8"
    playlist.write_text(
        "#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6.0,\na.m4s\n#EXTINF:2.0,\nb.m4s\n"
        "#EXT-X-ENDLIST\n"
    )
    peak, average = playlist_bitrates(playlist)
    assert peak == 8000  # 6000 bytes in 6 s
    assert average == 7000  # 7000 bytes in 8 s


def test_master_playlist_lists_groups_defaults_and_variants() -> None:
    text = master_playlist(
        [
            variant("v0", 1080),
            variant("v1", 720),
            variant("v2", 540, default=True),
            variant("uhd", 2160, codecs="hvc1.2.4.L153.B0", video_range="PQ"),
        ],
        [
            audio("a0/index.m3u8", "ara", default=True),
            audio("a1/index.m3u8", "eng"),
            audio("a2/index.m3u8", "eng", group="eac3", default=True),
        ],
        [
            SubtitleEntry("subs/3.eng.m3u8", "eng", "English", default=False, forced=False),
            SubtitleEntry("subs/x1.ara.m3u8", "ara", "العربية", default=True, forced=False),
            SubtitleEntry("subs/x2.ara.m3u8", "ara", "العربية", default=True, forced=True),
        ],
    )
    master = parse_master_playlist(text)
    assert master.independent_segments
    # One variant set per audio group, the default rung first in each.
    assert len(master.variants) == 8
    assert [v.uri for v in master.variants[:4]] == [
        "v2/index.m3u8",
        "uhd/index.m3u8",
        "v0/index.m3u8",
        "v1/index.m3u8",
    ]
    first = master.variants[0].attributes
    assert first["CODECS"] == "avc1.640028,mp4a.40.2"
    assert first["BANDWIDTH"] == str(540_000 + 170_000)
    assert first["AVERAGE-BANDWIDTH"] == str(486_000 + 160_000)
    assert first["FRAME-RATE"] == "23.976"
    assert (first["AUDIO"], first["SUBTITLES"], first["VIDEO-RANGE"]) == ("aac", "subs", "SDR")
    uhd = master.variants[1].attributes
    assert (uhd["VIDEO-RANGE"], uhd["RESOLUTION"]) == ("PQ", "3840x2160")
    assert master.variants[4].attributes["AUDIO"] == "eac3"
    assert master.variants[4].attributes["CODECS"].endswith(",ec-3")
    media = master.media
    assert [(m["TYPE"], m["LANGUAGE"], m["DEFAULT"]) for m in media[:3]] == [
        ("AUDIO", "ar", "YES"),
        ("AUDIO", "en", "NO"),
        ("AUDIO", "en", "YES"),
    ]
    subtitles = [m for m in media if m["TYPE"] == "SUBTITLES"]
    # Only one default per group; names stay unique; forced tracks are not auto-selected.
    assert [s["DEFAULT"] for s in subtitles] == ["NO", "YES", "NO"]
    assert [s["NAME"] for s in subtitles] == ["English", "العربية", "العربية 2"]
    assert [s["AUTOSELECT"] for s in subtitles] == ["YES", "YES", "NO"]
    assert subtitles[2]["FORCED"] == "YES"


def test_master_without_audio_or_subtitles_and_without_variants() -> None:
    text = master_playlist([variant("v0", 360, frame_rate=None)])
    master = parse_master_playlist(text)
    attributes = master.variants[0].attributes
    assert "AUDIO" not in attributes
    assert "SUBTITLES" not in attributes
    assert "FRAME-RATE" not in attributes
    assert attributes["CODECS"] == "avc1.640028"
    with pytest.raises(ValueError, match="at least one variant"):
        master_playlist([])


def test_quoted_names_are_cleaned() -> None:
    text = master_playlist(
        [variant("v0", 720)],
        [AudioEntry("a0/index.m3u8", "aac", "und", 'Director\'s "cut"\n', True, 2, "aac", 1, 1)],
    )
    assert "NAME=\"Director's 'cut' \"" in text


def test_subtitle_playlist_is_one_segment() -> None:
    playlist = parse_media_playlist(subtitle_playlist("x1.ara.vtt", 7254.5))
    assert playlist.ended
    assert playlist.target_duration == 7255
    assert [(s.uri, s.duration_s) for s in playlist.segments] == [("x1.ara.vtt", 7254.5)]


def test_language_names() -> None:
    assert language_name("ara") == "العربية"
    assert language_name("eng", "Commentary") == "Commentary"
    assert language_name("xyz") == "xyz"
    assert language_name("") == "und"
