"""Builders for ffprobe-shaped JSON and probe results used across the media tests."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from apps.media.probe import ProbeResult, parse_probe


def video(**overrides: Any) -> dict[str, Any]:
    """A 1080p23.976 H.264 High@4.0 SDR video stream."""
    stream: dict[str, Any] = {
        "index": 0,
        "codec_name": "h264",
        "codec_type": "video",
        "profile": "High",
        "level": 40,
        "width": 1920,
        "height": 1080,
        "sample_aspect_ratio": "1:1",
        "pix_fmt": "yuv420p",
        "avg_frame_rate": "24000/1001",
        "r_frame_rate": "24000/1001",
        "field_order": "progressive",
        "color_primaries": "bt709",
        "color_transfer": "bt709",
        "color_space": "bt709",
        "codec_tag_string": "avc1",
        "disposition": {"default": 1, "attached_pic": 0},
    }
    stream.update(overrides)
    return stream


def audio(index: int = 1, **overrides: Any) -> dict[str, Any]:
    """An English AAC-LC stereo track."""
    stream: dict[str, Any] = {
        "index": index,
        "codec_name": "aac",
        "codec_type": "audio",
        "profile": "LC",
        "channels": 2,
        "channel_layout": "stereo",
        "sample_rate": "48000",
        "bit_rate": "160000",
        "tags": {"language": "eng"},
        "disposition": {"default": 1, "forced": 0, "comment": 0},
    }
    stream.update(overrides)
    return stream


def subtitle(index: int = 2, codec: str = "subrip", **overrides: Any) -> dict[str, Any]:
    stream: dict[str, Any] = {
        "index": index,
        "codec_name": codec,
        "codec_type": "subtitle",
        "tags": {"language": "ara"},
        "disposition": {"default": 0, "forced": 0, "hearing_impaired": 0},
    }
    stream.update(overrides)
    return stream


def ffprobe_json(
    *streams: dict[str, Any],
    format_name: str = "matroska,webm",
    duration: str | None = "5400.000000",
    bit_rate: str | None = "8000000",
    filename: str = "/srv/media/movies/Some Movie (2020)/Some.Movie.2020.mkv",
) -> dict[str, Any]:
    fmt: dict[str, Any] = {
        "filename": filename,
        "nb_streams": len(streams),
        "format_name": format_name,
        "size": "5400000000",
    }
    if duration is not None:
        fmt["duration"] = duration
    if bit_rate is not None:
        fmt["bit_rate"] = bit_rate
    return {"streams": list(streams), "format": fmt, "chapters": []}


def probe_result(
    *streams: dict[str, Any],
    format_name: str = "matroska,webm",
    filename: str = "movie.mkv",
    duration: str | None = "5400.000000",
    bit_rate: str | None = "8000000",
    faststart: bool | None = None,
) -> ProbeResult:
    """A parsed probe; defaults to an MKV with the default video and audio streams."""
    data = ffprobe_json(
        *(streams or (video(), audio())),
        format_name=format_name,
        duration=duration,
        bit_rate=bit_rate,
    )
    result = parse_probe(data, filename=filename)
    if faststart is not None:
        result = replace(result, faststart=faststart)
    return result
