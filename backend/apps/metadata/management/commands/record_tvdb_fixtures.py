"""Record real TheTVDB responses as test fixtures (ADR-0023).

    manage.py record_tvdb_fixtures --series 81189 --query "Breaking Bad" [--dvd] [--root DIR]

The key and PIN come from Settings → Integrations → TheTVDB. Recorded files are TheTVDB
data under TheTVDB's licence: keep them out of public repositories (write them to
`--root` outside the checkout, or don't commit them).
"""

from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.core.services import get_secret
from apps.metadata.tvdb.fixtures import DEFAULT_FIXTURES_DIR
from apps.metadata.tvdb.recording import record_series


class Command(BaseCommand):
    help = "Record TheTVDB responses (searches, series, episodes, artworks) as fixtures."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--series", type=int, action="append", default=[])
        parser.add_argument("--query", action="append", default=[])
        parser.add_argument("--dvd", action="store_true", help="Also record the DVD order.")
        parser.add_argument("--root", type=Path, default=DEFAULT_FIXTURES_DIR)

    def handle(self, *args: Any, **options: Any) -> None:
        api_key = get_secret("metadata.tvdb_api_key")
        if not api_key:
            msg = "Set TheTVDB's API key first (Settings, Integrations, TheTVDB)."
            raise CommandError(msg)
        if not options["series"] and not options["query"]:
            msg = "Name at least one --series or --query."
            raise CommandError(msg)
        saved = record_series(
            api_key=api_key,
            pin=get_secret("metadata.tvdb_pin") or None,
            series_ids=options["series"],
            queries=options["query"],
            season_types=("official", "dvd") if options["dvd"] else ("official",),
            root=options["root"],
        )
        for path in saved:
            self.stdout.write(str(path))
        self.stdout.write(self.style.SUCCESS(f"{len(saved)} fixture files written."))
