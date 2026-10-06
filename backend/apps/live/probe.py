"""Testing a live source with ffprobe (the admin's "Test source", ADR-0017).

Runs on the worker (it has FFmpeg and internet access); the web process has neither.
The result says what the source carries, whether the packager can copy it into
MPEG-TS segments as it is, and, when it failed, why, with the URL masked.
"""

import json
import subprocess
from collections.abc import Iterable
from functools import cache
from typing import Any
from urllib.parse import urlsplit

from django.utils import timezone

from apps.live import conf, egress, sources

PROBE_TIMEOUT_S = 25
#: Codecs the packager copies into MPEG-TS segments that IPTV apps play.
COPY_VIDEO = frozenset({"h264", "hevc", "mpeg2video"})
COPY_AUDIO = frozenset({"aac", "ac3", "eac3", "mp2", "mp3"})


@cache
def supported_protocols() -> frozenset[str]:
    """The input protocols this host's FFmpeg build has."""
    try:
        completed = subprocess.run(  # noqa: S603
            [conf.ffprobe_binary(), "-hide_banner", "-protocols"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return frozenset()
    names: set[str] = set()
    reading = False
    for line in completed.stdout.splitlines():
        text = line.strip()
        if text.startswith("Input:"):
            reading = True
        elif text.startswith("Output:"):
            break
        elif reading and text:
            names.add(text)
    return frozenset(names)


def scheme_supported(url: str) -> bool:
    scheme = urlsplit(url).scheme.lower()
    if scheme in ("rtsp", "rtsps"):
        return True  # a demuxer, not a protocol: always built in
    protocols = supported_protocols()
    return not protocols or scheme in protocols


def _fps(value: str) -> float:
    try:
        numerator, _, denominator = value.partition("/")
        return round(float(numerator) / float(denominator or 1), 3)
    except (ValueError, ZeroDivisionError):
        return 0.0


def summarise(document: dict[str, Any]) -> dict[str, Any]:
    """What the admin and the planner need from ffprobe's JSON."""
    streams = document.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    try:
        bitrate = int((document.get("format") or {}).get("bit_rate") or 0) // 1000
    except (TypeError, ValueError):
        bitrate = 0
    video_codec = str(video.get("codec_name", "")) if video else ""
    audio_codec = str(audio.get("codec_name", "")) if audio else ""
    return {
        "ok": video is not None,
        "video": {
            "codec": video_codec,
            "profile": str(video.get("profile", "")) if video else "",
            "width": int(video.get("width") or 0) if video else 0,
            "height": int(video.get("height") or 0) if video else 0,
            "fps": _fps(str(video.get("avg_frame_rate") or "0/1")) if video else 0.0,
        },
        "audio": {
            "codec": audio_codec,
            "channels": int(audio.get("channels") or 0) if audio else 0,
        },
        "height": int(video.get("height") or 0) if video else 0,
        "bitrate_kbps": bitrate,
        "copy_ok": video_codec in COPY_VIDEO and (not audio_codec or audio_codec in COPY_AUDIO),
        "error": "" if video is not None else "no_video",
    }


def probe(  # noqa: PLR0911 (one answer per way a probe ends)
    url: str, *, timeout_s: int = PROBE_TIMEOUT_S, allowed: Iterable[str] = ()
) -> dict[str, Any]:
    """ffprobe the source; never raises. `error` is a stable code, `detail` redacted."""
    moment = timezone.now().isoformat()
    try:
        egress.check_url(url, allowed=allowed)
    except egress.UnsafeDestination as refused:
        return {"ok": False, "error": "unsafe_destination", "detail": refused.reason, "at": moment}
    if not scheme_supported(url):
        return {"ok": False, "error": "unsupported_scheme", "detail": "", "at": moment}
    redactor = sources.Redactor(url)
    command = [
        conf.ffprobe_binary(),
        "-v", "error",
        *sources.input_args(url, rw_timeout_s=min(timeout_s, 15)),
        "-analyzeduration", "5000000",
        "-probesize", "5000000",
        "-show_entries",
        "stream=codec_type,codec_name,profile,width,height,avg_frame_rate,channels"
        ":format=bit_rate,format_name",
        "-of", "json",
        "-i", url,
    ]  # fmt: skip
    try:
        completed = subprocess.run(  # noqa: S603
            command, capture_output=True, text=True, timeout=timeout_s, check=False
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout", "detail": "", "at": moment}
    except OSError:
        return {"ok": False, "error": "ffprobe_missing", "detail": "", "at": moment}
    if completed.returncode != 0:
        detail = redactor(completed.stderr.strip().splitlines()[-1] if completed.stderr else "")
        return {"ok": False, "error": "unreachable", "detail": detail[:300], "at": moment}
    try:
        document = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        return {"ok": False, "error": "unreadable", "detail": "", "at": moment}
    return {**summarise(document), "detail": "", "at": moment}
