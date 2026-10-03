"""`manage.py run_transcoder`: the transcoder service (SPEC §7.3 hardware detection).

Detects this host's encoders (test-encoding each candidate), registers them under
`worker:caps:<host>` and replaces itself with a Celery worker that consumes only the
matching `transcode.<backend>` queues, best first. A host without a working GPU
encoder consumes `transcode.cpu`.
"""

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from apps.core.stores import state_redis
from apps.media import hwdetect
from apps.media.profiles import default_profiles


class Command(BaseCommand):
    help = "Detect the encoders and run a Celery worker on the matching transcode queues."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--concurrency", type=int, default=int(os.environ.get("TRANSCODER_CONCURRENCY", "1"))
        )
        parser.add_argument(
            "--detect-only", action="store_true", help="Print the capabilities and exit."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        capabilities = hwdetect.detect(default_profiles())
        document = capabilities.to_json()
        document["devices"] = {
            backend.value: device for backend, device in capabilities.devices.items()
        }
        summary = {
            "host": capabilities.host,
            "ffmpeg": capabilities.ffmpeg_version,
            "queues": list(capabilities.queues),
            "checks": [
                f"{c['backend']}/{c['codec']}: {'ok' if c['works'] else c['reason']}"
                for c in document["checks"]
            ],
        }
        self.stdout.write(json.dumps(summary, indent=2))
        if options["detect_only"]:
            return
        queues = capabilities.queues or ("transcode.cpu",)
        path = Path(
            os.environ.get("TRANSCODER_CAPS_FILE")
            or Path(tempfile.gettempdir()) / "transcoder-caps.json"
        )
        path.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
        os.environ["TRANSCODER_CAPS_FILE"] = str(path)
        hwdetect.register(state_redis(), capabilities)
        argv = [
            "celery", "-A", "config", "worker",
            "--queues", ",".join(queues),
            "--concurrency", str(max(1, options["concurrency"])),
            "--hostname", f"transcoder@{capabilities.host}",
            "--loglevel", "INFO",
        ]  # fmt: skip
        os.execvp(argv[0], argv)  # noqa: S606 - fixed argv, no shell
