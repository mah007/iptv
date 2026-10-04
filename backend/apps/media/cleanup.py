"""Retention and cleanup of renditions (SPEC §7.3, §8.3 Storage; ADR-0014).

Renditions are re-creatable, so anything on the media volume that no database row
explains is removed:

- **orphaned** asset folders: no media file has that key any more;
- **removed** files' folders, once the file has been gone for
  `library.rendition_retention_days` (a file that comes back sooner keeps them);
- **superseded** outputs inside a live asset: a previous ladder's rungs, a UHD version
  the source no longer gets, subtitles of tracks that are gone, a `source` link after
  a compat MP4 replaced it, presentations that no longer apply;
- **leftovers** of interrupted work (`.tmp-<job>` of a job that is not running,
  `.old-*`, `.link-*`).

Nothing younger than `GRACE` is touched, so a job that has just moved its output into
place but not yet recorded it never loses it. `cleanup(dry_run=True)` reports what
would go (the admin's Storage page); beat runs it for real every six hours.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final, cast
from uuid import UUID

import structlog
from django.db import transaction
from django.utils import timezone

from apps.catalog.models import MediaFile
from apps.catalog.services import refresh_status_of_files
from apps.core.services import get_setting
from apps.media import conf, layout
from apps.media.models import (
    ACTIVE_JOB_STATUSES,
    Rendition,
    RenditionKind,
    RenditionStatus,
    SubtitleStatus,
    SubtitleTrack,
    TranscodeJob,
)

logger = structlog.get_logger(__name__)

#: Entries modified more recently than this are never removed.
GRACE: Final = timedelta(hours=1)
_LIVE: Final = (RenditionStatus.READY, RenditionStatus.PENDING, RenditionStatus.RUNNING)


@dataclass(frozen=True, slots=True)
class Removal:
    path: str  # `<asset>/<entry>`: an asset key and a name inside it, never a library path
    reason: str  # orphaned | removed_file | superseded | leftover
    bytes: int


@dataclass(slots=True)
class Report:
    dry_run: bool
    removals: list[Removal] = field(default_factory=list)

    @property
    def bytes(self) -> int:
        return sum(r.bytes for r in self.removals)


def cleanup(*, dry_run: bool, now: datetime | None = None) -> Report:
    """Find (and unless `dry_run`, delete) what no row explains; see the module docstring."""
    now = now or timezone.now()
    report = Report(dry_run=dry_run)
    root = conf.renditions_root()
    if not root.is_dir():
        return report
    assets = [p for p in root.iterdir() if p.is_dir() and not p.is_symlink()]
    files = _files_by_key(p.name for p in assets)
    retention = timedelta(days=int(cast(int, get_setting("library.rendition_retention_days"))))
    cutoff = (now - GRACE).timestamp()
    expired: list[MediaFile] = []
    for asset in sorted(assets):
        file = files.get(asset.name)
        if file is None:
            _drop(report, asset, asset.name, "orphaned", cutoff)
        elif file.removed_at is not None and file.removed_at <= now - retention:
            if _drop(report, asset, asset.name, "removed_file", cutoff):
                expired.append(file)
        else:
            _sweep_asset(report, file, asset, cutoff)
    if not dry_run and expired:
        _forget(expired)
    if report.removals:
        logger.info(
            "media.cleanup",
            dry_run=dry_run,
            entries=len(report.removals),
            bytes=report.bytes,
        )
    return report


def _files_by_key(keys: Iterable[str]) -> dict[str, MediaFile]:
    ids = []
    for key in keys:
        try:
            ids.append(UUID(hex=key))
        except ValueError:
            continue
    return {f.pk.hex: f for f in MediaFile.objects.filter(pk__in=ids)}


def _drop(report: Report, path: Path, label: str, reason: str, cutoff: float) -> bool:
    """Record (and unless dry-run, delete) `path` when it is older than the grace."""
    if _newest_mtime(path) > cutoff:
        return False
    report.removals.append(Removal(path=label, reason=reason, bytes=layout.tree_size(path)))
    if not report.dry_run:
        layout.remove(path)
    return True


def _newest_mtime(path: Path) -> float:
    try:
        newest = path.lstat().st_mtime
    except OSError:
        return time.time()
    if path.is_dir() and not path.is_symlink():
        for child in path.rglob("*"):
            try:
                newest = max(newest, child.lstat().st_mtime)
            except OSError:
                continue
    return newest


def _sweep_asset(report: Report, file: MediaFile, asset: Path, cutoff: float) -> None:
    rows = list(Rendition.objects.filter(media_file=file, status__in=_LIVE))
    active = set(
        TranscodeJob.objects.filter(media_file=file, status__in=ACTIVE_JOB_STATUSES)
        .values_list("pk", flat=True)
    )  # fmt: skip
    expected = _expected_entries(rows)
    for entry in sorted(asset.iterdir()):
        label = f"{asset.name}/{entry.name}"
        if layout.is_hidden(entry.name):
            job = entry.name.removeprefix(".tmp-")
            if entry.name.startswith(".tmp-") and any(pk.hex == job for pk in active):
                continue
            _drop(report, entry, label, "leftover", cutoff)
        elif entry.name not in expected:
            _drop(report, entry, label, "superseded", cutoff)
        elif entry.name == layout.HLS and entry.is_dir():
            _sweep_folder(report, entry, label, _hls_entries(rows), cutoff)
        elif entry.name == layout.SUBS and entry.is_dir():
            keys = set(
                SubtitleTrack.objects.filter(media_file=file).exclude(storage_key="")
                .values_list("storage_key", flat=True)
            )  # fmt: skip
            for sub in sorted(entry.iterdir()):
                stem = sub.name.rsplit(".", 1)[0]
                if stem not in keys:
                    _drop(report, sub, f"{label}/{sub.name}", "superseded", cutoff)


def _sweep_folder(
    report: Report, folder: Path, label: str, expected: set[str], cutoff: float
) -> None:
    for entry in sorted(folder.iterdir()):
        if entry.name not in expected:
            reason = "leftover" if layout.is_hidden(entry.name) else "superseded"
            _drop(report, entry, f"{label}/{entry.name}", reason, cutoff)


def _expected_entries(rows: list[Rendition]) -> set[str]:
    """Top-level names a live asset may hold, from its renditions."""
    names: set[str] = {layout.SUBS}
    for row in rows:
        kind = RenditionKind(row.kind)
        if kind is RenditionKind.COMPAT_MP4:
            names |= {f"{layout.COMPAT}.mp4", layout.COMPAT}
        elif kind is RenditionKind.SOURCE:
            names |= {f"{layout.SOURCE}.{row.container}", layout.SOURCE}
        elif kind is RenditionKind.UHD:
            names |= {f"{layout.UHD}.{row.container}", layout.UHD}
            names.add(layout.presentation(layout.UHD_CEILING))
        elif kind is RenditionKind.HLS_MASTER:
            names.add(row.name)
        elif kind is RenditionKind.HLS_VARIANT and row.name == layout.UHD_RUNG:
            names.add(layout.presentation(layout.UHD_CEILING))
        elif kind is RenditionKind.THUMBNAILS:
            names.add(layout.THUMBS)
    return names


def _hls_entries(rows: list[Rendition]) -> set[str]:
    names = {layout.MASTER, *layout.SHARED}
    for row in rows:
        if row.kind == RenditionKind.HLS_VARIANT and row.name.startswith("v"):
            names.add(row.name)
        elif row.kind == RenditionKind.HLS_MASTER and row.name == layout.HLS:
            names |= {str(a["dir"]) for a in (row.details or {}).get("audio", []) if "dir" in a}
    return names


def _forget(files: list[MediaFile]) -> None:
    """Rows of renditions whose folder was deleted: a file that comes back is planned
    again from scratch."""
    with transaction.atomic():
        Rendition.objects.filter(media_file__in=files).delete()
        SubtitleTrack.objects.filter(media_file__in=files).update(
            storage_key="", status=SubtitleStatus.PENDING, cues=0
        )
    refresh_status_of_files([f.pk for f in files])
