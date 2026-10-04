"""`manage.py bench_transcode <file>...`: time the compat MP4 encode per backend.

Runs the exact production command (planner + profiles.yaml presets) for each file on
each requested backend that works on this host, verifies the output and prints a
Markdown table (docs/benchmarks/transcode.md). Outputs go to a temporary folder and
are deleted. Files are paths under the library root (LIBRARY_ROOT).
"""

import tempfile
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.media import hwdetect
from apps.media.ffmpeg import Encoding, FfmpegError, compat_mp4_command, run
from apps.media.planner import ProcessingPolicy, plan_processing
from apps.media.probe import probe
from apps.media.profiles import Backend, VideoCodec, default_profiles
from apps.media.verify import VerificationError, expected_compat, verify_file


def row(*cells: object) -> str:
    return "| " + " | ".join(str(cell) for cell in cells) + " |"


class Command(BaseCommand):
    help = "Time the compat MP4 encode of library files on each working backend."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("files", nargs="+", help="paths relative to the library root")
        parser.add_argument("--backends", default="", help="comma-separated (default: all working)")

    def handle(self, *args: Any, **options: Any) -> None:
        profiles = default_profiles()
        caps = hwdetect.detect(profiles)
        wanted = [Backend(b) for b in options["backends"].split(",") if b] or list(caps.backends)
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
            plan = plan_processing(
                result, policy=ProcessingPolicy.INGEST, max_quality=1080, profiles=profiles
            )
            assert plan.compat_mp4 is not None  # noqa: S101 - ingest plans always have one
            described = (
                f"{video.codec} {video.width}x{video.height} {video.bit_depth}-bit {video.hdr}"
                if video
                else "?"
            )
            for backend in wanted:
                if not caps.supports(backend, VideoCodec.H264):
                    self.stdout.write(
                        row(source.name, described, backend, *[""] * 5, "unavailable")
                    )
                    continue
                encoding = Encoding(profiles, backend, device=caps.devices.get(backend))
                encoder = encoding.preset(VideoCodec.H264).encoder
                with tempfile.TemporaryDirectory() as tmp:
                    output = Path(tmp) / "compat.mp4"
                    command = compat_mp4_command(
                        result, plan.compat_mp4, source=source, output=output, encoding=encoding
                    )
                    try:
                        ran = run(command)
                        made = verify_file(
                            output, expected_compat(plan.compat_mp4, result.duration_ms)
                        )
                    except (FfmpegError, VerificationError) as exc:
                        failed = row(source.name, described, backend, encoder, *[""] * 4, exc)
                        self.stdout.write(failed)
                        continue
                    seconds = (result.duration_ms or 0) / 1000
                    frames = ran.progress.frame if ran.progress and ran.progress.frame else 0
                    out_video = made.video
                    shape = f"{out_video.width}x{out_video.height}" if out_video else "?"
                    mbps = (made.bitrate or 0) / 1e6
                    self.stdout.write(
                        row(
                            source.name,
                            described,
                            backend,
                            encoder,
                            f"{ran.elapsed_s:.1f}",
                            f"{seconds / ran.elapsed_s:.2f}x",
                            f"{frames / ran.elapsed_s:.0f}",
                            f"{shape} {mbps:.1f} Mb/s",
                            "ok",
                        )
                    )
