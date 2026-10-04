"""`manage.py media_ready`: wait until the catalogue has a playable movie and series.

The quality gate's live Xtream checks (`make compat-live`, `make e2e-iptvnator`) need
ready titles. This adds the sample libraries when no library covers them
(`/media/movies`, `/media/series`, filled by `make sample-media`), scans them, and
waits for the pipeline (match, plan, transcode) to make one movie and one series
ready. It changes nothing when they already are.
"""

import time
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.catalog.models import Movie, Series, TitleStatus
from apps.library import services
from apps.library.models import Library, LibraryKind, ScanTrigger

SAMPLE_LIBRARIES = (
    ("Movies", LibraryKind.MOVIES, "movies"),
    ("Series", LibraryKind.SERIES, "series"),
)
POLL_S = 3.0


def _ready() -> bool:
    return (
        Movie.objects.filter(status=TitleStatus.READY).exists()
        and Series.objects.filter(status=TitleStatus.READY).exists()
    )


class Command(BaseCommand):
    help = "Add and scan the sample libraries if needed; wait for a ready movie and series."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--timeout", type=float, default=600.0, help="seconds to wait (600)")

    def handle(self, *args: Any, **options: Any) -> None:
        if _ready():
            self.stdout.write("A ready movie and a ready series exist.")
            return
        for name, kind, folder in SAMPLE_LIBRARIES:
            path = Path(settings.LIBRARY_ROOT) / folder
            library = Library.objects.filter(path=str(path)).first()
            if library is None:
                if not path.is_dir() or Library.objects.filter(name=name).exists():
                    continue
                library = services.create_library(
                    {"name": name, "kind": kind, "path": str(path)}, actor=None, ip=None
                )
                self.stdout.write(f"Added the {name} library.")
            services.request_scan(library, trigger=ScanTrigger.SCHEDULE)
        deadline = time.monotonic() + options["timeout"]
        while not _ready():
            if time.monotonic() > deadline:
                msg = (
                    "No ready movie and series yet: check `make logs s=worker` and the transcoder."
                )
                raise CommandError(msg)
            time.sleep(POLL_S)
        self.stdout.write("A ready movie and a ready series exist.")
