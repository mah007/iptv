"""`manage.py plan_media`: plan every matched file again (ADR-0014).

Planning is idempotent: it queues only the outputs a file is missing (the HLS ladder,
UHD, thumbnails, subtitles beside its compat MP4), and never redoes a ready or failed
one. Run it once after deploying M8 so titles made before it get their new outputs;
beat's reconciliation only plans files that have no playable rendition at all.
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from apps.catalog.models import FileState, MediaFile
from apps.media import tasks


class Command(BaseCommand):
    help = "Queue planning of every matched file (missing outputs only)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--limit", type=int, default=0, help="at most this many files")

    def handle(self, *args: Any, **options: Any) -> None:
        files = MediaFile.objects.filter(state=FileState.MATCHED, removed_at__isnull=True)
        ids = list(files.order_by("created_at").values_list("pk", flat=True))
        if options["limit"] > 0:
            ids = ids[: options["limit"]]
        for file_id in ids:
            tasks.prepare_media_file.delay(str(file_id))
        self.stdout.write(f"Queued planning of {len(ids)} files.")
