"""The library watcher (SPEC §7.1): the stable-size rule, removals, moves and the set
of watched libraries. Events are fed by hand; a fake clock replaces waiting."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from watchdog.events import (
    DirCreatedEvent,
    DirDeletedEvent,
    DirModifiedEvent,
    FileClosedEvent,
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
)

from apps.library.models import Library
from apps.library.watcher import LibraryWatcher, _Handler, make_observer

pytestmark = pytest.mark.django_db


class FakeObserver:
    def __init__(self) -> None:
        self.scheduled: dict[str, Any] = {}
        self.started = self.stopped = False

    def schedule(self, handler: Any, path: str, recursive: bool) -> str:
        if path.endswith("broken"):
            raise OSError("inotify limit")
        self.scheduled[path] = handler
        return path

    def unschedule(self, watch: str) -> None:
        del self.scheduled[watch]

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def join(self, timeout: float) -> None:
        pass


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def setup(
    make_library: Callable[..., Library],
) -> tuple[LibraryWatcher, Library, list[tuple[str, str]], Clock, FakeObserver]:
    library = make_library()
    queued: list[tuple[str, str]] = []
    clock = Clock()
    observer = FakeObserver()
    watcher = LibraryWatcher(
        lambda library_id, path: queued.append((library_id, path)),
        stable_s=lambda: 60.0,
        observer=observer,  # type: ignore[arg-type]
        clock=clock,
    )
    watcher.sync_libraries()
    return watcher, library, queued, clock, observer


def test_a_new_file_waits_until_its_size_holds(setup: Any) -> None:
    watcher, library, queued, clock, _ = setup
    path = Path(library.path) / "Film (2001).mkv"
    path.write_bytes(b"half")
    watcher.handle(str(library.pk), FileCreatedEvent(str(path)))
    watcher.poll()
    clock.now += 30
    path.write_bytes(b"half and more")  # still copying
    watcher.handle(str(library.pk), FileModifiedEvent(str(path)))
    clock.now += 40
    watcher.poll()
    assert queued == []  # 40 s since the size last changed
    clock.now += 21
    watcher.poll()
    assert queued == [(str(library.pk), "Film (2001).mkv")]
    watcher.poll()
    assert len(queued) == 1


def test_a_size_change_seen_only_while_polling_restarts_the_wait(setup: Any) -> None:
    watcher, library, queued, clock, _ = setup
    path = Path(library.path) / "Film (2001).mkv"
    path.write_bytes(b"a")
    watcher.handle(str(library.pk), FileClosedEvent(str(path)))
    clock.now += 50
    path.write_bytes(b"ab")
    watcher.poll()
    clock.now += 50
    watcher.poll()
    assert queued == []
    clock.now += 11
    watcher.poll()
    assert queued == [(str(library.pk), "Film (2001).mkv")]


def test_removals_are_queued_at_once(setup: Any) -> None:
    watcher, library, queued, _clock, _ = setup
    root = Path(library.path)
    watcher.handle(str(library.pk), FileDeletedEvent(str(root / "Gone.mkv")))
    watcher.handle(str(library.pk), DirDeletedEvent(str(root / "Old Folder")))
    watcher.handle(str(library.pk), FileDeletedEvent(str(root / "notes.txt")))
    assert queued == [(str(library.pk), "Gone.mkv"), (str(library.pk), "Old Folder")]


def test_moves_remove_the_old_path_and_wait_for_the_new(setup: Any) -> None:
    watcher, library, queued, clock, _ = setup
    root = Path(library.path)
    (root / "New.mkv").write_bytes(b"x")
    watcher.handle(str(library.pk), FileMovedEvent(str(root / "Old.mkv"), str(root / "New.mkv")))
    assert queued == [(str(library.pk), "Old.mkv")]
    clock.now += 61
    watcher.poll()
    assert queued[-1] == (str(library.pk), "New.mkv")


def test_new_folders_wait_like_files(setup: Any) -> None:
    watcher, library, queued, clock, _ = setup
    folder = Path(library.path) / "Film (2001)"
    folder.mkdir()
    (folder / "Film.mkv").write_bytes(b"x")
    watcher.handle(str(library.pk), DirCreatedEvent(str(folder)))
    clock.now += 61
    watcher.poll()
    assert queued == [(str(library.pk), "Film (2001)")]


def test_ignored_events(setup: Any) -> None:
    watcher, library, queued, clock, _ = setup
    root = Path(library.path)
    (root / ".partial.mkv").write_bytes(b"x")
    (root / "poster.jpg").write_bytes(b"x")
    for event in (
        FileCreatedEvent(str(root / ".partial.mkv")),
        FileCreatedEvent(str(root / "poster.jpg")),
        FileCreatedEvent("/elsewhere/Film.mkv"),
        FileCreatedEvent(str(root / "Vanished.mkv")),  # gone before it is stat'ed
        DirModifiedEvent(str(root)),
    ):
        watcher.handle(str(library.pk), event)
    watcher.handle("not-a-library", FileCreatedEvent(str(root / "x.mkv")))
    clock.now += 61
    watcher.poll()
    assert queued == []


def test_a_file_deleted_while_pending_is_dropped(setup: Any) -> None:
    watcher, library, queued, clock, _ = setup
    path = Path(library.path) / "Film.mkv"
    path.write_bytes(b"x")
    watcher.handle(str(library.pk), FileCreatedEvent(str(path)))
    path.unlink()
    clock.now += 61
    watcher.poll()
    assert queued == []
    assert watcher.pending == {}


def test_the_watched_set_follows_the_database(
    setup: Any, make_library: Callable[..., Library]
) -> None:
    watcher, library, queued, clock, observer = setup
    assert set(observer.scheduled) == {library.path}
    extra = make_library("Series", "series", "series")
    broken = make_library("Broken", folder="broken")
    Library.objects.filter(pk=library.pk).update(enabled=False)
    path = Path(library.path) / "x.mkv"
    path.write_bytes(b"x")
    watcher.handle(str(library.pk), FileCreatedEvent(str(path)))
    watcher.sync_libraries()
    assert set(observer.scheduled) == {extra.path}
    assert str(broken.pk) not in watcher.watched
    clock.now += 61
    watcher.poll()
    assert queued == []  # pending paths of unwatched libraries are dropped


def test_events_flow_through_the_queue_and_the_loop(
    setup: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    watcher, library, queued, _clock, observer = setup
    monkeypatch.setattr("apps.library.watcher.time.sleep", lambda seconds: None)
    handler = observer.scheduled[library.path]
    assert isinstance(handler, _Handler)
    handler.on_any_event(FileDeletedEvent(str(Path(library.path) / "Gone.mkv")))
    watcher.drain()
    assert queued == [(str(library.pk), "Gone.mkv")]
    beats: list[int] = []
    rounds = iter([False, True])
    watcher.run(heartbeat=lambda: beats.append(1), stop=lambda: next(rounds))
    assert observer.started
    assert observer.stopped
    assert beats == [1]


def test_make_observer() -> None:
    assert type(make_observer(polling=True)).__name__ == "PollingObserver"
    assert type(make_observer(polling=False)).__name__ != "PollingObserver"
