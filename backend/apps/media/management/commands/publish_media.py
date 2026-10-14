"""Publish renditions to the S3 origin now (M15, ADR-0022).

    manage.py publish_media --all            every file with ready renditions
    manage.py publish_media <file id> ...    those files
    manage.py publish_media --status         counts by state, and the bytes published

Runs in this process (use it on the worker, which has internet access); the Settings →
Storage switch must be on. The beat job does the same in the background every 5 minutes.
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.catalog.models import MediaFile
from apps.core.services import get_setting
from apps.media import publisher
from apps.media.models import RenditionStatus


class Command(BaseCommand):
    help = "Publish renditions to the S3 origin (Settings → Storage)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("files", nargs="*", help="media file ids")
        parser.add_argument("--all", action="store_true", help="every file with ready renditions")
        parser.add_argument("--status", action="store_true", help="print the counts and exit")

    def handle(self, *args: Any, **options: Any) -> None:
        if options["status"]:
            summary = publisher.summary()
            for status, count in sorted(summary["by_status"].items()):
                self.stdout.write(f"{status}: {count}")
            self.stdout.write(f"published bytes: {summary['published_bytes']}")
            return
        if not get_setting("storage.s3_enabled"):
            msg = "Publishing is off: turn on Settings → Storage → Publish to S3 first."
            raise CommandError(msg)
        ids: list[str] = list(options["files"])
        if options["all"]:
            ids = [
                str(pk)
                for pk in MediaFile.objects.filter(
                    removed_at__isnull=True, renditions__status=RenditionStatus.READY
                )
                .values_list("pk", flat=True)
                .distinct()
            ]
        if not ids:
            msg = "Name media file ids, or pass --all."
            raise CommandError(msg)
        results: dict[str, int] = {}
        for file_id in ids:
            outcome = publisher.publish_file_id(file_id)
            results[outcome] = results.get(outcome, 0) + 1
            self.stdout.write(f"{file_id}: {outcome}")
        self.stdout.write(", ".join(f"{k} {v}" for k, v in sorted(results.items())))
        if results.get("failed") or results.get("not_configured"):
            raise CommandError("Some files were not published (see above and the admin).")
