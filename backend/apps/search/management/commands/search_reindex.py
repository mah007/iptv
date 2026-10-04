"""`manage.py search_reindex`: rebuild the Meilisearch index now (atomic swap)."""

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.search import index
from apps.search.meili import MeiliError


class Command(BaseCommand):
    help = "Rebuild the search index from the catalogue and swap it in atomically."

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            counts = index.rebuild()
        except MeiliError as exc:
            raise CommandError(str(exc)) from None
        summary = ", ".join(f"{count} {kind}" for kind, count in sorted(counts.items()))
        self.stdout.write(f"Search index rebuilt: {summary or 'empty catalogue'}.")
