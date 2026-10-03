"""Library folders on the read-only media mount (SPEC §7.1).

Libraries live under `settings.LIBRARY_ROOT` (`/media` in the containers). Files are
identified by their path relative to the library (`MediaFile.storage_key`), which is
what the admin sees; absolute paths stay inside this module and the workers.
"""

import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Final

from django.conf import settings
from django.core.exceptions import ValidationError

from apps.library.parsing import VIDEO_EXTENSIONS

#: Folders never walked: hidden ones and NAS housekeeping (the parser ignores them too).
_SKIPPED_DIRS: Final = frozenset({"@eadir", "#recycle", "$recycle.bin", "lost+found", "#snapshot"})


@dataclass(frozen=True, slots=True)
class FoundFile:
    """A video file seen on disk: its library-relative key, size and modification time."""

    key: str
    size: int
    mtime: datetime


def library_root() -> Path:
    return Path(settings.LIBRARY_ROOT)


def validate_library_path(value: str) -> str:
    """The absolute path of an existing, readable folder under the media root.

    Accepts an absolute container path (`/media/movies`) or one relative to the media
    root (`movies`). Symlinks are resolved, and the result must stay under the root.
    """
    root = library_root().resolve()
    candidate = Path(value.strip())
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValidationError(f"{value} does not exist.", code="missing") from None
    if resolved != root and root not in resolved.parents:
        raise ValidationError(f"Libraries must be folders under {root}.", code="outside_root")
    if resolved == root:
        raise ValidationError(
            f"Choose a folder inside {root}, not the media root itself.", code="root"
        )
    if not resolved.is_dir():
        raise ValidationError(f"{value} is not a folder.", code="not_a_folder")
    if not os.access(resolved, os.R_OK | os.X_OK):
        raise ValidationError(f"{value} is not readable.", code="unreadable")
    return str(resolved)


def overlaps(path: str, other: str) -> bool:
    """Whether one folder contains the other (or they are the same)."""
    first, second = PurePosixPath(path), PurePosixPath(other)
    return first == second or first in second.parents or second in first.parents


def library_file(library_path: str, key: str) -> Path:
    """The absolute path of `key` inside a library; refuses keys that leave it."""
    relative = PurePosixPath(key)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        msg = "storage keys are relative paths inside their library"
        raise ValueError(msg)
    return Path(library_path, *relative.parts)


def is_video(name: str) -> bool:
    stem, dot, extension = name.rpartition(".")
    return bool(dot and stem) and extension.lower() in VIDEO_EXTENSIONS


def walk(library_path: str, subpath: str = "") -> Iterator[FoundFile]:
    """Every video file under the library (or under `subpath` inside it), sorted by path.

    Hidden files and folders and NAS system folders are skipped; symlinked folders are
    not followed. A `subpath` naming a file yields just that file when it is a video.
    """
    base = Path(library_path)
    start = library_file(library_path, subpath) if subpath else base
    if start.is_file():
        found = _found(base, start)
        if found is not None:
            yield found
        return
    if not start.is_dir():
        return
    for directory, folders, files in os.walk(start, followlinks=False):
        folders[:] = sorted(
            name
            for name in folders
            if not name.startswith(".") and name.lower() not in _SKIPPED_DIRS
        )
        for name in sorted(files):
            if name.startswith(".") or not is_video(name):
                continue
            found = _found(base, Path(directory, name))
            if found is not None:
                yield found


def _found(base: Path, path: Path) -> FoundFile | None:
    try:
        stat = path.stat()
    except OSError:
        return None  # vanished or unreadable since it was listed
    if not is_video(path.name) or not path.is_file():
        return None
    key = path.relative_to(base).as_posix()
    return FoundFile(key=key, size=stat.st_size, mtime=datetime.fromtimestamp(stat.st_mtime, UTC))
