"""`manage.py ensure_libraries`: the default Movies and Series libraries.

`scripts/install.sh` creates `movies/` and `series/` under the media folder; this adds
a library for each folder that exists and that no library covers yet, then asks for
a scan. Folders and libraries the admin made are left alone. Idempotent.
"""

from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.library import services
from apps.library.models import Library, LibraryKind, ScanTrigger

DEFAULT_LIBRARIES = (
    ("Movies", LibraryKind.MOVIES, "movies"),
    ("Series", LibraryKind.SERIES, "series"),
)


class Command(BaseCommand):
    help = "Add the Movies and Series libraries for /media/movies and /media/series if missing."

    def handle(self, *args: Any, **options: Any) -> None:
        for name, kind, folder in DEFAULT_LIBRARIES:
            path = Path(settings.LIBRARY_ROOT) / folder
            if not path.is_dir():
                self.stdout.write(f"No {path} folder; skipped the {name} library.")
                continue
            if Library.objects.filter(path=str(path)).exists():
                self.stdout.write(f"A library already covers {path}.")
                continue
            if Library.objects.filter(name=name).exists():
                self.stdout.write(f"A library named {name} exists elsewhere; left it alone.")
                continue
            library = services.create_library(
                {"name": name, "kind": kind, "path": str(path)}, actor=None, ip=None
            )
            services.request_scan(library, trigger=ScanTrigger.SCHEDULE)
            self.stdout.write(f"Added the {name} library ({path}) and started a scan.")
