"""Publish a file's HLS presentations and scope links (ADR-0014; layout in `layout`).

`publish(file)` (re)writes, from the file's ready renditions and tracks:

- `hls/master.m3u8`: every SDR rung, the audio renditions and the text subtitles;
- `hls480/`, `hls720/`: the same, capped at the ceiling, made of links to the rungs that
  fit (only when the ladder has a taller rung; otherwise the full ladder already fits);
- `hls2160/master.m3u8`: the ladder plus the UHD rung (`hls2160/uhd/`), when both exist;
- the `subs` and `thumbs` links in every presentation and progressive scope.

It is idempotent and cheap (no media is read but init segments), so every job that
changes an input (ladder, UHD rung, subtitles, thumbnails, an admin's track edit) calls
it. Masters are written atomically, so players never see a partial one.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import structlog
from django.db import transaction
from django.utils import timezone

from apps.catalog.models import MediaFile
from apps.media import layout
from apps.media.hls import (
    AudioEntry,
    SubtitleEntry,
    VariantEntry,
    language_name,
    master_playlist,
)
from apps.media.models import (
    AudioTrack,
    Rendition,
    RenditionKind,
    RenditionStatus,
    SubtitleStatus,
    SubtitleTrack,
)

logger = structlog.get_logger(__name__)

#: Progressive renditions whose token scope gets `subs` and `thumbs` links.
_PROGRESSIVE = {
    RenditionKind.COMPAT_MP4: layout.COMPAT,
    RenditionKind.SOURCE: layout.SOURCE,
    RenditionKind.UHD: layout.UHD,
}


def publish(file: MediaFile) -> list[str]:
    """Write every presentation and link of `file`; returns the presentation names."""
    root = layout.asset_dir(layout.asset_key(file))
    rows = list(Rendition.objects.filter(media_file=file))
    _link_scopes(root, rows)
    return _publish_hls(file, root, rows)


def _shared_links(root: Path, scope: Path) -> None:
    for name in layout.SHARED:
        if (root / name).is_dir():
            layout.link(f"../{name}", scope / name)
        elif (scope / name).is_symlink():
            (scope / name).unlink()


def _link_scopes(root: Path, rows: Sequence[Rendition]) -> None:
    for row in rows:
        stem = _PROGRESSIVE.get(RenditionKind(row.kind))
        if stem is None or row.status != RenditionStatus.READY:
            continue
        _shared_links(root, root / stem)


def _ready(rows: Iterable[Rendition], kind: RenditionKind) -> list[Rendition]:
    return [r for r in rows if r.kind == kind and r.status == RenditionStatus.READY]


def _position(row: Rendition) -> int:
    try:
        return int(row.name[1:])
    except ValueError:
        return 1_000


def _variant(row: Rendition, prefix: str = "") -> VariantEntry:
    details: dict[str, Any] = row.details or {}
    return VariantEntry(
        uri=f"{prefix}{row.name}/{layout.PLAYLIST}",
        width=row.width or 0,
        height=row.height or 0,
        frame_rate=details.get("frame_rate"),
        codecs=str(details.get("codecs") or ""),
        peak_bps=int(details.get("peak_bps") or 0),
        average_bps=int(details.get("average_bps") or 0),
        video_range=str(details.get("video_range") or "SDR"),
        default=bool(details.get("default")),
    )


def _box(row: Rendition) -> int:
    return int((row.details or {}).get("box_height") or row.height or 0)


def _audio(file: MediaFile, master: Rendition) -> list[AudioEntry]:
    stored = [a for a in (master.details or {}).get("audio", []) if isinstance(a, dict)]
    chosen = (
        AudioTrack.objects.filter(media_file=file, default=True)
        .values_list("stream_index", flat=True)
        .first()
    )
    entries = []
    for item in stored:
        default = (
            item.get("source_index") == chosen if chosen is not None else bool(item.get("default"))
        )
        entries.append(
            AudioEntry(
                uri=f"{item['dir']}/{layout.PLAYLIST}",
                group=str(item.get("group") or "aac"),
                language=str(item.get("language") or "und"),
                name=_audio_name(item),
                default=default,
                channels=int(item.get("channels") or 2),
                codec=str(item.get("codec") or "aac"),
                peak_bps=int(item.get("peak_bps") or 0),
                average_bps=int(item.get("average_bps") or 0),
            )
        )
    # Every group needs a default: the first track when the chosen one is not in it.
    by_group: dict[str, list[int]] = {}
    for i, entry in enumerate(entries):
        by_group.setdefault(entry.group, []).append(i)
    for members in by_group.values():
        if not any(entries[i].default for i in members):
            first = entries[members[0]]
            entries[members[0]] = replace(first, default=True)
    return entries


def _audio_name(item: dict[str, Any]) -> str:
    """The track's title, else its language's name (the planner falls back to the code).
    A downmix drops the source title, which names the source's layout ("English 5.1")."""
    language = str(item.get("language") or "und")
    name = str(item.get("name") or "")
    downmixed = int(item.get("channels") or 2) < int(item.get("source_channels") or 0)
    if not name or downmixed or name == language or name.startswith(f"{language} "):
        suffix = name.removeprefix(language) if name.startswith(language) else ""
        return language_name(language) + suffix
    return name


def _subtitles(file: MediaFile, root: Path) -> list[SubtitleEntry]:
    tracks = SubtitleTrack.objects.filter(media_file=file, status=SubtitleStatus.READY).exclude(
        storage_key=""
    )
    entries = []
    for track in tracks.order_by("-default", "external", "stream_index", "created_at"):
        if not (root / layout.subtitle_file(track.storage_key, "m3u8")).is_file():
            continue
        entries.append(
            SubtitleEntry(
                uri=layout.subtitle_file(track.storage_key, "m3u8"),
                language=track.language,
                name=language_name(track.language, track.title),
                default=track.default,
                forced=track.forced,
            )
        )
    return entries


def _audio_dirs(master: Rendition) -> list[str]:
    return [str(a["dir"]) for a in (master.details or {}).get("audio", []) if "dir" in a]


def _publish_hls(file: MediaFile, root: Path, rows: Sequence[Rendition]) -> list[str]:
    masters = [r for r in rows if r.kind == RenditionKind.HLS_MASTER]
    full = next(
        (r for r in _ready(masters, RenditionKind.HLS_MASTER) if r.name == layout.HLS), None
    )
    variants = _ready(rows, RenditionKind.HLS_VARIANT)
    ladder = sorted((r for r in variants if r.name.startswith("v")), key=_position)
    uhd_rung = next((r for r in variants if r.name == layout.UHD_RUNG), None)
    if full is None or not ladder or not (root / layout.HLS).is_dir():
        _retire(file, root, masters, keep=set())
        return []
    audio = _audio(file, full)
    subtitles = _subtitles(file, root)
    audio_dirs = _audio_dirs(full)
    published = [layout.HLS]

    hls_dir = root / layout.HLS
    _shared_links(root, hls_dir)
    entries = [_variant(row) for row in ladder]
    layout.write_text(hls_dir / layout.MASTER, master_playlist(entries, audio, subtitles))

    for ceiling in layout.CAPPED_CEILINGS:
        members = [row for row in ladder if _box(row) <= ceiling]
        if not members or len(members) == len(ladder):
            continue
        name = layout.presentation(ceiling)
        entries = _with_default([_variant(row) for row in members])
        _write_linked(
            root,
            name,
            targets=[m.name for m in members] + audio_dirs,
            variants=entries,
            audio=audio,
            subtitles=subtitles,
        )
        _upsert_master(file, name, ceiling, root)
        published.append(name)

    if uhd_rung is not None and (root / layout.presentation(layout.UHD_CEILING)).is_dir():
        name = layout.presentation(layout.UHD_CEILING)
        entries = [_variant(row) for row in ladder] + [_variant(uhd_rung)]
        _write_linked(
            root,
            name,
            targets=[m.name for m in ladder] + audio_dirs,
            variants=entries,
            audio=audio,
            subtitles=subtitles,
        )
        _upsert_master(file, name, layout.UHD_CEILING, root)
        published.append(name)

    _retire(file, root, masters, keep=set(published))
    return published


def _with_default(entries: list[VariantEntry]) -> list[VariantEntry]:
    """A capped ladder starts on its default rung, else on its tallest."""
    if any(e.default for e in entries):
        return entries
    tallest = max(entries, key=lambda e: e.height)
    return [replace(e, default=e is tallest) for e in entries]


def _write_linked(  # noqa: PLR0913
    root: Path,
    name: str,
    *,
    targets: Sequence[str],
    variants: Sequence[VariantEntry],
    audio: Sequence[AudioEntry],
    subtitles: Sequence[SubtitleEntry],
) -> None:
    """A presentation folder of links into `hls/` plus its own master."""
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    wanted = set(targets) | set(layout.SHARED) | {layout.MASTER, layout.UHD_RUNG}
    for target in targets:
        layout.link(f"../{layout.HLS}/{target}", directory / target)
    _shared_links(root, directory)
    for entry in directory.iterdir():
        if entry.name not in wanted and not layout.is_hidden(entry.name):
            layout.remove(entry)
    layout.write_text(directory / layout.MASTER, master_playlist(variants, audio, subtitles))


def _upsert_master(file: MediaFile, name: str, ceiling: int, root: Path) -> None:
    size = (root / name / layout.MASTER).stat().st_size
    Rendition.objects.update_or_create(
        media_file=file,
        kind=RenditionKind.HLS_MASTER,
        name=name,
        defaults={
            "storage_key": layout.asset_key(file),
            "container": "m3u8",
            "height": ceiling,
            "codec": "hls",
            "size": size,
            "status": RenditionStatus.READY,
            "error": "",
            "ready_at": timezone.now(),
            "source_hash": file.xxhash64,
            "details": {"ceiling": ceiling},
        },
    )


def _retire(file: MediaFile, root: Path, masters: Sequence[Rendition], *, keep: set[str]) -> None:
    """Presentations that no longer apply lose their master and row (and, for capped
    ones, their folder of links; the UHD folder keeps its rung for the UHD job)."""
    stale = [row for row in masters if row.name not in keep and row.name != layout.HLS]
    for row in stale:
        directory = root / row.name
        if row.name == layout.presentation(layout.UHD_CEILING):
            (directory / layout.MASTER).unlink(missing_ok=True)
        else:
            layout.remove(directory)
    if stale:
        with transaction.atomic():
            Rendition.objects.filter(pk__in=[row.pk for row in stale]).delete()
        logger.info("media.presentations_retired", file=str(file.pk), names=[r.name for r in stale])
