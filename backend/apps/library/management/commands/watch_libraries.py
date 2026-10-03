"""`manage.py watch_libraries`: the watcher service (SPEC §7.1, apps.library.watcher)."""

import signal
import time
from types import FrameType
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.core.services import get_setting
from apps.core.stores import state_redis
from apps.library import tasks
from apps.library.watcher import HEARTBEAT_KEY, HEARTBEAT_TTL_S, LibraryWatcher, make_observer


class Command(BaseCommand):
    help = "Watch the enabled libraries and queue scans of new, changed and removed files."

    def handle(self, *args: Any, **options: Any) -> None:
        stopping = False

        def stop(signum: int, frame: FrameType | None) -> None:
            nonlocal stopping
            stopping = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)

        def heartbeat() -> None:
            state_redis().set(HEARTBEAT_KEY, int(time.time()), ex=HEARTBEAT_TTL_S)

        watcher = LibraryWatcher(
            lambda library_id, path: tasks.scan_path.delay(library_id, path),
            stable_s=lambda: float(get_setting("library.watcher_stable_s")),
            observer=make_observer(polling=settings.LIBRARY_WATCHER_POLLING),
        )
        self.stdout.write("Watching libraries.")
        watcher.run(heartbeat=heartbeat, stop=lambda: stopping)
