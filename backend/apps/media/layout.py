"""Where a media file's outputs live in its asset directory (ADR-0010, ADR-0014).

The edge serves `<root>/<asset>/<tail>` for a token whose rendition `R` covers the file
`R.<ext>` or anything under `R/` (ADR-0007). So every playable entry point is either a
progressive file or a directory named after its token rendition:

```
<asset>/compat.mp4                compat MP4                    token rendition `compat`
<asset>/source.<ext>              symlink to the library file   `source`
<asset>/uhd.<ext>                 UHD version (or a source link) `uhd`
<asset>/hls/master.m3u8           SDR ladder, every rung        `hls`
<asset>/hls/v0/ v1/ ...           one folder per rung (index.m3u8, init.mp4, seg_*.m4s)
<asset>/hls/a0/ a1/ ...           one folder per audio rendition
<asset>/hls720/master.m3u8        the ladder capped at 720p      `hls720`
<asset>/hls720/v1 -> ../hls/v1    (links to the rungs it lists, and to every a*)
<asset>/hls2160/master.m3u8       the ladder plus the UHD rung   `hls2160`
<asset>/hls2160/uhd/              the UHD rung (HEVC fMP4), only reachable here
<asset>/subs/<key>.vtt .srt .m3u8 converted subtitles and their one-segment playlists
<asset>/thumbs/thumbs.vtt         scrubbing previews and their sprite sheets
<asset>/<scope>/subs -> ../subs   in every presentation and progressive scope
<asset>/<scope>/thumbs -> ../thumbs
```

A capped presentation (`hls480`, `hls720`) exists so a plan's quality ceiling holds at
the edge too: its token cannot reach a taller rung, because no link to one exists under
its directory. Links are relative and stay inside the asset, which the edge mounts
read-only; the edge follows them (`disable_symlinks off`).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Final
from uuid import uuid4

from apps.library import storage
from apps.media import conf
from apps.playback import tokens

if TYPE_CHECKING:
    from apps.catalog.models import MediaFile

COMPAT: Final = "compat"
SOURCE: Final = "source"
UHD: Final = "uhd"
HLS: Final = "hls"
SUBS: Final = "subs"
THUMBS: Final = "thumbs"
MASTER: Final = "master.m3u8"
PLAYLIST: Final = "index.m3u8"
INIT: Final = "init.mp4"
THUMBS_VTT: Final = "thumbs.vtt"
#: The folder of the UHD rung inside its presentation (`hls2160/uhd/`).
UHD_RUNG: Final = "uhd"
#: Plan quality ceilings (accounts.MaxQuality) that get their own HLS presentation
#: below the full ladder; 2160 is the UHD presentation.
CAPPED_CEILINGS: Final = (480, 720)
UHD_CEILING: Final = 2160
#: Folders linked into every presentation and progressive scope.
SHARED: Final = (SUBS, THUMBS)


def asset_key(file: MediaFile) -> str:
    """The file's asset directory key under the renditions root (the token's `title`)."""
    return file.pk.hex


def asset_dir(key: str) -> Path:
    if not tokens.TITLE.fullmatch(key):
        msg = "asset keys are [A-Za-z0-9_-]{1,64}"
        raise ValueError(msg)
    return conf.renditions_root() / key


def source_path(file: MediaFile) -> Path:
    """The library file (workers only: never leaves the server)."""
    return storage.library_file(file.library.path, file.storage_key)


def presentation(ceiling: int | None = None) -> str:
    """The directory (and token rendition) of an HLS presentation: `hls` for the full
    SDR ladder, `hls<ceiling>` for a capped or UHD one."""
    return HLS if ceiling is None else f"{HLS}{ceiling}"


def rung_dir(position: int) -> str:
    return f"v{position}"


def audio_dir(position: int) -> str:
    return f"a{position}"


def subtitle_file(key: str, extension: str) -> str:
    return f"{SUBS}/{key}.{extension}"


def is_hidden(name: str) -> bool:
    """Work in progress (`.tmp-<job>`, `.old-*`): never part of a published output."""
    return name.startswith(".")


# --- Filesystem helpers (atomic where a reader may be looking) -----------------------------


def link(target: str, at: Path) -> None:
    """Make `at` a symlink to `target` (relative), replacing whatever is there atomically."""
    at.parent.mkdir(parents=True, exist_ok=True)
    try:
        if at.is_symlink() and os.readlink(at) == target:
            return
    except OSError:
        pass
    if at.is_dir() and not at.is_symlink():
        shutil.rmtree(at)
    temporary = at.parent / f".link-{uuid4().hex}"
    os.symlink(target, temporary)
    os.replace(temporary, at)


def replace_dir(new: Path, at: Path) -> None:
    """Put the finished folder `new` at `at`; the old one is moved aside then deleted.

    Both are in the same asset directory (one filesystem), so each rename is atomic; a
    player sees the old folder or the new one, never a partial one.
    """
    old = at.parent / f".old-{uuid4().hex}"
    if at.is_symlink() or at.is_file():
        at.unlink()
    elif at.exists():
        os.replace(at, old)
    os.replace(new, at)
    shutil.rmtree(old, ignore_errors=True)


def write_text(path: Path, text: str) -> None:
    """Write a small text file atomically (playlists, VTT)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def tree_size(path: Path) -> int:
    """Bytes of the regular files under `path`, links not followed (a link is shared)."""
    if path.is_symlink():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            file = Path(root) / name
            if not file.is_symlink():
                try:
                    total += file.stat().st_size
                except OSError:
                    continue
    return total


def remove(path: Path) -> None:
    """Delete a file, link or folder; missing is fine."""
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.exists():
        shutil.rmtree(path, ignore_errors=True)
