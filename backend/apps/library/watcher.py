"""The library watcher (SPEC §7.1): inotify (watchdog) on every enabled local library.

- A new or changed video file becomes eligible once its size has not changed for
  `library.watcher_stable_s` seconds (60 by default), so half-copied files are never
  probed; then `scan_path(library, path)` is queued.
- A deleted file or folder, or one moved away, is queued at once: the path scan sees it
  missing and soft-removes its files. A folder moved in is queued after the same wait.
- Hidden files and folders and non-video files are ignored; the reconciliation scan
  (beat, every `scan_interval_min`) catches anything an event missed.
- The set of watched libraries follows the database (checked every minute), and a
  heartbeat in redis-state (`hb:watcher`) feeds the container healthcheck.
"""

import logging
import queue
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final

from watchdog.events import (
    DirCreatedEvent,
    DirDeletedEvent,
    DirMovedEvent,
    FileClosedEvent,
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileSystemEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver, ObservedWatch
from watchdog.observers.polling import PollingObserver

from apps.library.models import Library
from apps.library.storage import is_video

logger = logging.getLogger(__name__)

HEARTBEAT_KEY: Final = "hb:watcher"
HEARTBEAT_TTL_S: Final = 300
POLL_S: Final = 5.0
SYNC_S: Final = 60.0
HEARTBEAT_S: Final = 30.0

type Enqueue = Callable[[str, str], None]


@dataclass
class _Pending:
    size: int
    since: float


class _Handler(FileSystemEventHandler):
    """Forwards events of one library to the watcher's queue (watchdog's thread)."""

    def __init__(self, library_id: str, events: "queue.Queue[tuple[str, FileSystemEvent]]") -> None:
        self.library_id = library_id
        self.events = events

    def on_any_event(self, event: FileSystemEvent) -> None:
        self.events.put((self.library_id, event))


class LibraryWatcher:
    """Turns file-system events into `scan_path` tasks. Single-threaded apart from
    watchdog's emitter threads, which only enqueue events."""

    def __init__(
        self,
        enqueue: Enqueue,
        *,
        stable_s: Callable[[], float],
        observer: BaseObserver | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.enqueue = enqueue
        self.stable_s = stable_s
        self.observer = observer or Observer()
        self.clock = clock
        self.events: queue.Queue[tuple[str, FileSystemEvent]] = queue.Queue()
        self.watched: dict[str, tuple[str, ObservedWatch]] = {}
        self.pending: dict[tuple[str, str], _Pending] = {}

    # --- libraries ---------------------------------------------------------------------------

    def sync_libraries(self) -> None:
        """Watch exactly the enabled libraries whose folder exists."""
        wanted = {
            str(pk): path
            for pk, path in Library.objects.filter(enabled=True).values_list("pk", "path")
            if Path(path).is_dir()
        }
        for library_id, (path, watch) in list(self.watched.items()):
            if wanted.get(library_id) != path:
                self.observer.unschedule(watch)
                del self.watched[library_id]
                logger.info("library unwatched", extra={"library": library_id})
        for library_id, path in wanted.items():
            if library_id in self.watched:
                continue
            try:
                watch = self.observer.schedule(
                    _Handler(library_id, self.events), path, recursive=True
                )
            except OSError:
                logger.exception("library cannot be watched", extra={"library": library_id})
                continue
            self.watched[library_id] = (path, watch)
            logger.info("library watched", extra={"library": library_id})

    # --- events ------------------------------------------------------------------------------------

    def _key(self, library_id: str, raw: bytes | str) -> str | None:
        """The library-relative path, or None for paths outside it or hidden ones."""
        entry = self.watched.get(library_id)
        if entry is None:
            return None
        path = raw.decode() if isinstance(raw, bytes) else raw
        try:
            relative = PurePosixPath(Path(path).relative_to(entry[0]).as_posix())
        except ValueError:
            return None
        if not relative.parts or any(part.startswith(".") for part in relative.parts):
            return None
        return relative.as_posix()

    def handle(self, library_id: str, event: FileSystemEvent) -> None:
        if isinstance(event, FileMovedEvent | DirMovedEvent):
            self._gone(library_id, event.src_path, directory=event.is_directory)
            self._arrived(library_id, event.dest_path, directory=event.is_directory)
        elif isinstance(event, FileDeletedEvent | DirDeletedEvent):
            self._gone(library_id, event.src_path, directory=event.is_directory)
        elif isinstance(event, FileCreatedEvent | FileModifiedEvent | FileClosedEvent):
            self._arrived(library_id, event.src_path, directory=False)
        elif isinstance(event, DirCreatedEvent):
            self._arrived(library_id, event.src_path, directory=True)

    def _gone(self, library_id: str, raw: bytes | str, *, directory: bool) -> None:
        key = self._key(library_id, raw)
        if key is None or (not directory and not is_video(key)):
            return
        self.pending.pop((library_id, key), None)
        self.enqueue(library_id, key)

    def _arrived(self, library_id: str, raw: bytes | str, *, directory: bool) -> None:
        key = self._key(library_id, raw)
        if key is None or (not directory and not is_video(key)):
            return
        size = self._size(library_id, key)
        if size is None:
            return
        entry = self.pending.get((library_id, key))
        if entry is None or entry.size != size:
            self.pending[(library_id, key)] = _Pending(size, self.clock())

    def _size(self, library_id: str, key: str) -> int | None:
        """A file's size, or the total size of a folder's files; None when it is gone."""
        path = Path(self.watched[library_id][0], key)
        try:
            if path.is_dir():
                return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
            return path.stat().st_size
        except OSError:
            return None

    def drain(self) -> None:
        """Handle every event queued so far."""
        while True:
            try:
                library_id, event = self.events.get_nowait()
            except queue.Empty:
                return
            try:
                self.handle(library_id, event)
            except Exception:
                logger.exception("watch event failed", extra={"library": library_id})

    def poll(self) -> None:
        """Queue the pending paths whose size held still long enough."""
        stable = self.stable_s()
        now = self.clock()
        for (library_id, key), entry in list(self.pending.items()):
            if library_id not in self.watched:
                del self.pending[(library_id, key)]
                continue
            size = self._size(library_id, key)
            if size is None:
                del self.pending[(library_id, key)]
            elif size != entry.size:
                self.pending[(library_id, key)] = _Pending(size, now)
            elif now - entry.since >= stable:
                del self.pending[(library_id, key)]
                self.enqueue(library_id, key)

    # --- the loop -----------------------------------------------------------------------------------

    def run(self, *, heartbeat: Callable[[], None], stop: Callable[[], bool]) -> None:
        self.sync_libraries()
        self.observer.start()
        last_sync = last_beat = self.clock()
        heartbeat()
        try:
            while not stop():
                time.sleep(POLL_S)
                self.drain()
                self.poll()
                now = self.clock()
                if now - last_sync >= SYNC_S:
                    self.sync_libraries()
                    last_sync = now
                if now - last_beat >= HEARTBEAT_S:
                    heartbeat()
                    last_beat = now
        finally:
            self.observer.stop()
            self.observer.join(timeout=10)


def make_observer(*, polling: bool) -> Any:
    """inotify on local disks; polling for network shares where inotify sees nothing."""
    return PollingObserver(timeout=10) if polling else Observer()
