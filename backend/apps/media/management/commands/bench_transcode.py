"""`manage.py bench_transcode <file>...`: time the production encodes per backend.

Runs the exact production command (planner + profiles.yaml presets) for each file on
each requested backend that works on this host, verifies the output and prints a
Markdown table (docs/benchmarks/transcode.md). `--profile` picks the output:

- `compat_mp4` (default): the progressive MP4;
- `hls`: the whole SDR ladder in one run (one decode, every rung, every audio rendition);
- `uhd`: the HEVC Main10 UHD version (sources taller than profiles.yaml's UHD minimum).

Outputs go to a temporary folder and are deleted. Files are paths under the library
root (LIBRARY_ROOT).
"""

import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.media import hwdetect
from apps.media.ffmpeg import (
    Encoding,
    FfmpegError,
    RunResult,
    compat_mp4_command,
    hls_command,
    run,
    uhd_command,
    write_hls_master,
)
from apps.media.planner import ProcessingPlan, ProcessingPolicy, UhdMode, plan_processing
from apps.media.probe import ProbeResult, probe
from apps.media.profiles import Backend, VideoCodec, default_profiles
from apps.media.verify import (
    VerificationError,
    expected_compat,
    expected_uhd,
    verify_file,
    verify_hls,
)

PROFILES = ("compat_mp4", "hls", "uhd")


def row(*cells: object) -> str:
    return "| " + " | ".join(str(cell) for cell in cells) + " |"


def _encode(  # noqa: PLR0913
    profile: str,
    *,
    result: ProbeResult,
    plan: ProcessingPlan,
    source: Path,
    out: Path,
    encoding: Encoding,
) -> tuple[RunResult, str]:
    """Run one output; returns the run and a description of what it made."""
    if profile == "hls":
        if plan.hls is None:
            raise CommandError("no HLS plan")
        hls = replace(plan.hls, subtitles=())
        ran = run(hls_command(result, hls, output_dir=out, source=source, encoding=encoding))
        write_hls_master(hls, out, duration_ms=result.duration_ms)
        verify_hls(out, expected_duration_ms=result.duration_ms)
        rungs = "/".join(str(r.height) for r in hls.rungs)
        return ran, f"{len(hls.rungs)} rungs ({rungs}p)"
    if profile == "uhd":
        uhd, compat = plan.uhd, plan.compat_mp4
        if uhd is None or compat is None or uhd.mode is UhdMode.KEEP_SOURCE:
            raise CommandError("not a UHD source that needs an encode")
        output = out / "uhd.mp4"
        command = uhd_command(
            result, uhd, compat.audio, source=source, output=output, encoding=encoding
        )
        ran = run(command)
        made = verify_file(output, expected_uhd(uhd, compat.audio, result.duration_ms))
    else:
        compat = plan.compat_mp4
        if compat is None:
            raise CommandError("no compat plan")
        output = out / "compat.mp4"
        ran = run(
            compat_mp4_command(result, compat, source=source, output=output, encoding=encoding)
        )
        made = verify_file(output, expected_compat(compat, result.duration_ms))
    video = made.video
    shape = f"{video.width}x{video.height}" if video else "?"
    return ran, f"{shape} {(made.bitrate or 0) / 1e6:.1f} Mb/s"


class Command(BaseCommand):
    help = "Time the compat MP4, HLS ladder or UHD encode of library files per backend."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("files", nargs="+", help="paths relative to the library root")
        parser.add_argument("--backends", default="", help="comma-separated (default: all working)")
        parser.add_argument("--profile", choices=PROFILES, default="compat_mp4")

    def handle(self, *args: Any, **options: Any) -> None:
        profiles = default_profiles()
        caps = hwdetect.detect(profiles)
        wanted = [Backend(b) for b in options["backends"].split(",") if b] or list(caps.backends)
        profile = options["profile"]
        codec = VideoCodec.HEVC if profile == "uhd" else VideoCodec.H264
        root = Path(settings.LIBRARY_ROOT)
        self.stdout.write(
            "| File | Source | Backend | Encoder | Wall s | Speed | Avg fps | Output | Result |"
        )
        self.stdout.write("| --- | --- | --- | --- | ---: | ---: | ---: | --- | --- |")
        for name in options["files"]:
            source = (root / name).resolve()
            if not source.is_relative_to(root.resolve()) or not source.is_file():
                raise CommandError(f"not a file under the library root: {name}")
            result = probe(source)
            video = result.video
            quality = 2160 if profile == "uhd" else 1080
            plan = plan_processing(
                result, policy=ProcessingPolicy.INGEST, max_quality=quality, profiles=profiles
            )
            described = (
                f"{video.codec} {video.width}x{video.height} {video.bit_depth}-bit {video.hdr}"
                if video
                else "?"
            )
            for backend in wanted:
                if not caps.supports(backend, codec):
                    self.stdout.write(
                        row(source.name, described, backend, *[""] * 5, "unavailable")
                    )
                    continue
                encoding = Encoding(profiles, backend, device=caps.devices.get(backend))
                encoder = encoding.preset(codec).encoder
                with tempfile.TemporaryDirectory() as tmp:
                    try:
                        ran, made = _encode(
                            profile, result=result, plan=plan, source=source,
                            out=Path(tmp), encoding=encoding,
                        )  # fmt: skip
                    except (FfmpegError, VerificationError) as exc:
                        failed = row(source.name, described, backend, encoder, *[""] * 4, exc)
                        self.stdout.write(failed)
                        continue
                    seconds = (result.duration_ms or 0) / 1000
                    frames = ran.progress.frame if ran.progress and ran.progress.frame else 0
                    self.stdout.write(
                        row(
                            source.name,
                            described,
                            backend,
                            encoder,
                            f"{ran.elapsed_s:.1f}",
                            f"{seconds / ran.elapsed_s:.2f}x",
                            f"{frames / ran.elapsed_s:.0f}",
                            made,
                            "ok",
                        )
                    )
