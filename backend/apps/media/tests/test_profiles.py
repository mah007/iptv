"""profiles.yaml and its loader (apps/media/profiles.py)."""

from __future__ import annotations

import copy
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from apps.media.profiles import (
    DEFAULT_PROFILES_PATH,
    PROFILES_ENV,
    Backend,
    ProfileError,
    Profiles,
    VideoCodec,
    load_profiles,
    parse_profiles,
    render_args,
    resolve_profiles_path,
)

Mutation = Callable[[dict[str, Any]], None]


@pytest.fixture(scope="module")
def raw() -> dict[str, Any]:
    data = yaml.safe_load(DEFAULT_PROFILES_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_compat_mp4_matches_the_spec(profiles: Profiles) -> None:
    compat = profiles.compat_mp4
    assert profiles.keyframe_interval_s == 2
    assert (compat.max_width, compat.max_height) == (1920, 1080)
    assert (compat.video.codec, compat.video.profile, compat.video.level) == (
        VideoCodec.H264,
        "high",
        "4.1",
    )
    assert (compat.video.maxrate_k, compat.video.bufsize_k) == (7800, 15600)
    assert profiles.encoder(Backend.CPU, VideoCodec.H264).quality == 20  # CRF 20
    assert (compat.stereo.codec, compat.stereo.bitrate_k, compat.stereo.channels) == ("aac", 160, 2)
    assert compat.stereo.copy_codecs == {"aac"}
    assert (compat.surround.codec, compat.surround.bitrate_k, compat.surround.channels) == (
        "aac",
        384,
        6,
    )
    assert compat.surround.copy_codecs == {"ac3", "eac3", "aac"}
    assert compat.subtitle_codec == "mov_text"
    assert compat.movflags == "+faststart"
    assert compat.copy_video_when.max_level == 41


def test_hls_ladder_matches_the_spec(profiles: Profiles) -> None:
    hls = profiles.hls
    assert hls.segment_s == 6
    assert [(r.name, r.height, r.bitrate_k) for r in hls.rungs] == [
        ("1080p", 1080, 6000),
        ("720p", 720, 3000),
        ("540p", 540, 2000),
        ("360p", 360, 730),
    ]
    assert hls.default_rung == "540p"
    assert hls.passthrough_audio_codecs == {"eac3"}


def test_uhd_and_thumbnails_match_the_spec(profiles: Profiles) -> None:
    uhd = profiles.uhd
    assert uhd.keep_source_when.codecs == {"hevc", "av1"}
    assert uhd.keep_source_when.max_bitrate_k == 25000
    assert (uhd.video.codec, uhd.video.profile, uhd.video.bitrate_k) == (
        VideoCodec.HEVC,
        "main10",
        16800,
    )
    sprite = profiles.thumbnails.sprite
    assert profiles.thumbnails.poster_at_ratio == 0.10
    assert (sprite.interval_s, sprite.tile_width, sprite.tile_height) == (10, 160, 90)
    assert (sprite.columns, sprite.rows, sprite.tiles_per_sheet) == (10, 10, 100)


@pytest.mark.parametrize(
    ("backend", "codec", "encoder", "preset_args"),
    [
        (Backend.NVENC, VideoCodec.H264, "h264_nvenc", ("-preset", "p5", "-tune", "hq")),
        (Backend.NVENC, VideoCodec.HEVC, "hevc_nvenc", ("-preset", "p5", "-tune", "hq")),
        (Backend.QSV, VideoCodec.H264, "h264_qsv", ("-preset", "slower")),
        (Backend.QSV, VideoCodec.HEVC, "hevc_qsv", ("-preset", "slower")),
        (Backend.VAAPI, VideoCodec.H264, "h264_vaapi", ()),
        (Backend.VAAPI, VideoCodec.HEVC, "hevc_vaapi", ()),
        (Backend.CPU, VideoCodec.H264, "libx264", ("-preset", "slow")),
        (Backend.CPU, VideoCodec.HEVC, "libx265", ("-preset", "slow")),
    ],
)
def test_backend_presets(
    profiles: Profiles,
    backend: Backend,
    codec: VideoCodec,
    encoder: str,
    preset_args: tuple[str, ...],
) -> None:
    preset = profiles.encoder(backend, codec)
    assert preset.encoder == encoder
    assert preset.args[: len(preset_args)] == preset_args
    assert profiles.backend(backend).queue == f"transcode.{backend.value}"
    if backend is Backend.QSV:
        assert "-look_ahead_depth" in preset.args
    if codec is VideoCodec.HEVC:
        assert preset.pix_fmt_10bit is not None


def test_preference_and_cpu_fallback(profiles: Profiles) -> None:
    assert profiles.backend_preference == (Backend.NVENC, Backend.QSV, Backend.VAAPI, Backend.CPU)


def test_render_args() -> None:
    assert render_args(["-b:v", "{bitrate}", "x"], {"bitrate": "2000k"}) == ["-b:v", "2000k", "x"]
    with pytest.raises(ProfileError, match="bitrate"):
        render_args(["{bitrate}"], {})


def _set(path: str, value: Any) -> Mutation:
    def mutate(data: dict[str, Any]) -> None:
        *parents, last = path.split(".")
        node: Any = data
        for part in parents:
            node = node[int(part)] if isinstance(node, list) else node[part]
        if value is _DELETE:
            del node[last]
        else:
            node[last] = value

    return mutate


_DELETE = object()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (_set("version", 2), "unsupported version"),
        (_set("compat_mp4.surprise", 1), "unknown keys surprise"),
        (_set("compat_mp4.max_width", _DELETE), "compat_mp4.max_width: missing"),
        (_set("compat_mp4.max_width", "1920"), "expected an integer"),
        (_set("compat_mp4.max_width", True), "expected an integer"),
        (_set("keyframe_interval_s", 0), "must be >= 1"),
        (_set("hls.default_rung", "480p"), "is not a rung"),
        (_set("hls.rungs", []), "at least one rung"),
        (_set("hls.rungs.1.name", "1080p"), "unique"),
        (_set("hls.video.codec", "vp9"), "is not one of"),
        (_set("backend_preference", ["nvenc", "amf"]), "unknown backend 'amf'"),
        (_set("backend_preference", ["cpu", "cpu"]), "duplicates"),
        (_set("backends.cpu", _DELETE), "backend_preference: 'cpu' is not configured"),
        (_set("backends.amf", {}), "unknown backend 'amf'"),
        (_set("backends.cpu.encoders.av1", {}), "unknown codec 'av1'"),
        (_set("backends.nvenc.encoders.h264.args", ["-preset", 5]), "list of strings"),
        (
            _set("backends.nvenc.encoders.h264.quality_args", ["-cq", "{qp}"]),
            "unknown placeholder {qp}",
        ),
        (_set("backends.nvenc.encoders.h264.gop_args", ["-g", "{gop"]), "invalid template"),
        (_set("backends.nvenc.input_args", ["{gop}"]), "unknown placeholder {gop}"),
        (_set("backends.cpu.encoders.hevc.pix_fmt_10bit", _DELETE), "required for hevc"),
        (_set("backends.cpu.encoders.h264", _DELETE), "an h264 encoder is required"),
        (_set("thumbnails.sprite.columns", 64), "must be <= 32"),
        (_set("hls.maxrate_ratio", "1.07"), "expected a number"),
        (_set("compat_mp4.copy_video_when.codecs", "h264"), "list of strings"),
        (_set("filters", []), "expected a mapping"),
    ],
)
def test_invalid_profiles_are_rejected(
    raw: dict[str, Any], mutation: Mutation, message: str
) -> None:
    data = copy.deepcopy(raw)
    mutation(data)
    with pytest.raises(ProfileError, match=message.replace("{", r"\{").replace("}", r"\}")):
        parse_profiles(data)


def test_cpu_backend_is_required_even_when_not_preferred(raw: dict[str, Any]) -> None:
    data = copy.deepcopy(raw)
    data["backend_preference"] = ["nvenc"]
    del data["backends"]["cpu"]
    with pytest.raises(ProfileError, match="cpu fallback backend is required"):
        parse_profiles(data)


def test_load_from_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw: dict[str, Any]
) -> None:
    custom = copy.deepcopy(raw)
    custom["hls"]["segment_s"] = 4
    path = tmp_path / "profiles.yaml"
    path.write_text(yaml.safe_dump(custom), encoding="utf-8")
    monkeypatch.setenv(PROFILES_ENV, str(path))
    assert resolve_profiles_path() == path
    assert load_profiles().hls.segment_s == 4
    monkeypatch.delenv(PROFILES_ENV)
    assert resolve_profiles_path() == DEFAULT_PROFILES_PATH


def test_load_errors(tmp_path: Path) -> None:
    with pytest.raises(ProfileError, match="cannot read"):
        load_profiles(tmp_path / "missing.yaml")
    broken = tmp_path / "broken.yaml"
    broken.write_text("version: [1\n", encoding="utf-8")
    with pytest.raises(ProfileError, match="invalid YAML"):
        load_profiles(broken)
    with pytest.raises(ProfileError, match="expected a mapping"):
        parse_profiles(["not", "a", "mapping"])
