"""The library side of an import (ADR-0025): what may be imported, where it is staged,
how much room is left, and the atomic move into the library.

- Only video containers (the scanner's `VIDEO_EXTENSIONS`) and subtitle sidecars
  (`SIDECAR_EXTENSIONS`) are imported; a folder brings those and nothing else.
- A job stages its files in `<library>/.smart-iptv-import/<job>/`: inside the library
  folder, so on the same file system (the move is a rename), and hidden, so the scanner
  and the watcher never look at half-copied files.
- A finished file is renamed into place; a file that already exists there is never
  overwritten. Subtitles move before videos, so a video's sidecars are there when the
  watcher sees it.
"""

import contextlib
import errno
import os
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final
from urllib.parse import unquote, urlsplit

from apps.imports.models import FileKind
from apps.library.parsing import VIDEO_EXTENSIONS
from apps.library.storage import library_file
from apps.media.subtitles import SIDECAR_EXTENSIONS

STAGING_FOLDER: Final = ".smart-iptv-import"
SUBTITLE_EXTENSIONS: Final = frozenset(extension.lstrip(".") for extension in SIDECAR_EXTENSIONS)
ALLOWED_EXTENSIONS: Final = VIDEO_EXTENSIONS | SUBTITLE_EXTENSIONS
#: Settings say GB: decimal gigabytes, as disks are sold and the admin displays sizes.
GB: Final = 1000**3


class PathRefused(ValueError):
    """A name or folder we never write: the message says why (no paths in it)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def extension(name: str) -> str:
    stem, dot, ext = name.rpartition(".")
    return ext.lower() if dot and stem else ""


def file_kind(name: str) -> FileKind | None:
    """Video, subtitle, or None for a type that is never imported."""
    ext = extension(PurePosixPath(name).name)
    if ext in VIDEO_EXTENSIONS:
        return FileKind.VIDEO
    if ext in SUBTITLE_EXTENSIONS:
        return FileKind.SUBTITLE
    return None


def _parts(value: str) -> tuple[str, ...]:
    text = value.strip().strip("/")
    if not text:
        return ()
    if "\\" in text or any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise PathRefused("invalid_path", "Use a plain folder path.")
    parts = PurePosixPath(text).parts
    if any(part in {".", ".."} or part.startswith(".") for part in parts):
        raise PathRefused("invalid_path", "Folder names may not start with a dot or be ..")
    return parts


def clean_subfolder(value: str) -> str:
    """A folder inside a library, relative; '' is the library's top level."""
    if len(value) > 500:
        raise PathRefused("too_long", "At most 500 characters.")
    return "/".join(_parts(value))


def clean_file_name(value: str) -> str:
    """One file name for a direct link: no folders, not hidden, an importable type."""
    name = value.strip()
    if not name or "/" in name or "\\" in name or name.startswith(".") or len(name) > 255:
        raise PathRefused("invalid_name", "Give a plain file name, such as Metropolis (1927).mp4.")
    if any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise PathRefused("invalid_name", "Give a plain file name, such as Metropolis (1927).mp4.")
    if file_kind(name) is None:
        raise PathRefused("unsupported_type", "Only video files and subtitles can be imported.")
    return name


def name_from_url(url: str) -> str:
    """The last segment of a link's path, decoded ('' when there is none)."""
    segment = urlsplit(url).path.rstrip("/").rpartition("/")[2]
    return unquote(segment).strip()


def library_key(subfolder: str, item: str, is_dir: bool, inner: str = "") -> str:
    """Where a remote file lands, relative to the library: the subfolder, then the
    selected folder's or file's own name, then the file's path inside the folder."""
    base = PurePosixPath(item.strip("/")).name
    parts = [*_parts(subfolder), *_parts(base)]
    if is_dir:
        parts += list(_parts(inner))
    return "/".join(parts)


@dataclass(frozen=True, slots=True)
class Space:
    free: int
    needed: int
    reserve: int

    @property
    def fits(self) -> bool:
        return self.free >= self.needed + self.reserve


def free_bytes(path: str | Path) -> int:
    return shutil.disk_usage(path).free


def device(path: str | Path) -> int:
    return os.stat(path).st_dev


def staging_dir(library_path: str, job_hex: str) -> Path:
    return Path(library_path, STAGING_FOLDER, job_hex)


def staged_bytes(folder: Path) -> int:
    """What a previous attempt already copied (counted towards the space it needs)."""
    if not folder.is_dir():
        return 0
    total = 0
    for directory, _folders, names in os.walk(folder):
        for name in names:
            try:
                total += Path(directory, name).stat().st_size
            except OSError:
                continue
    return total


def remove_staging(folder: Path) -> None:
    shutil.rmtree(folder, ignore_errors=True)
    parent = folder.parent
    if parent.name == STAGING_FOLDER:
        with contextlib.suppress(OSError):
            parent.rmdir()  # only when no other job stages there


def make_dirs(path: Path) -> None:
    """Folders readable by every service (the scanner, the edge) whatever the umask."""
    missing: list[Path] = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    for folder in reversed(missing):
        try:
            folder.mkdir(mode=0o755)
        except FileExistsError:
            continue
        os.chmod(folder, 0o755)  # noqa: S103 (the scanner, the edge and web read it)


def place(staged: Path, library_path: str, key: str) -> Path:
    """Move one staged file to `key` inside the library, atomically, never overwriting.

    A rename when both are on one file system (they are, unless the subfolder is a
    mount of its own); otherwise a copy to a hidden name beside the target, then a
    rename, so the watcher still sees only whole files."""
    target = library_file(library_path, key)
    if target.exists():
        raise PathRefused("target_exists", "A file with this name is already in the library.")
    make_dirs(target.parent)
    os.chmod(staged, 0o644)
    try:
        # A hard link fails if the name was taken meanwhile: never an overwrite.
        os.link(staged, target)
    except FileExistsError:
        raise PathRefused(
            "target_exists", "A file with this name is already in the library."
        ) from None
    except OSError as exc:
        if exc.errno == errno.EXDEV:
            hidden = target.with_name(f".{target.name}.importing")
            shutil.copyfile(staged, hidden)
            os.chmod(hidden, 0o644)
            staged.unlink()
            staged = hidden
        # No hard links here (some network file systems): a rename after the check.
        os.rename(staged, target)
        return target
    staged.unlink()
    return target
