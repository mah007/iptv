"""Publishing renditions to the S3 origin (M15, ADR-0022).

Local disk stays the primary copy: the transcoder and the local edge use it, and the
publisher (on the worker, which has internet access) copies each asset to the bucket for
standalone edges and CDNs:

    <prefix>/<asset key>-g<generation>/compat.mp4           as on disk
    .../source.<ext>, uhd.<ext>                              the library file a link names
    .../hls/master.m3u8                                      rewritten: rungs by height
    .../hls/v<n>-h<box>/index.m3u8, init.mp4, seg_*.m4s     each rung, named by its height
    .../hls/a<n>/...                                         audio renditions
    .../hls480/master.m3u8, hls720/master.m3u8              capped masters (no links)
    .../hls2160/master.m3u8, hls2160/uhd/...                the UHD presentation
    .../subs/..., thumbs/...                                 subtitles and trickplay

Object storage has no symlinks. The capped presentations' links are not copied; their
masters name the rungs `v<n>-h<box>/`, and the edge maps `hls720/v1-h720/x` to
`hls/v1-h720/x` only when the height fits the ceiling (`streaming/nginx/njs/s3.js`), so a
720p token still can't reach a 1080p rung and nothing is stored twice.

Immutable objects (segments, MP4s, sprites) never change in place: when one would, the
asset gets a new generation (a new prefix, so a new token title, so no edge serves a
stale cached slice), unchanged objects are copied there inside the bucket, and the old
generation is deleted after `storage.old_generation_hours`. Playlists and subtitles
(`.m3u8`, `.vtt`, `.srt`) are overwritten in place; edges cache them for a minute.
Uploads go immutable objects first and playlists last, so a playlist never names an
object that isn't there yet.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, cast

import redis
from django.db import transaction
from django.utils import timezone

from apps.catalog.models import MediaFile
from apps.core.redaction import redact_text
from apps.core.services import get_secret, get_setting
from apps.core.stores import state_redis
from apps.media import layout
from apps.media.models import (
    AssetPublication,
    PublicationStatus,
    Rendition,
    RenditionKind,
    RenditionStatus,
)
from apps.media.s3 import S3Client, S3Config, S3Error

logger = logging.getLogger(__name__)

MUTABLE: Final = re.compile(r"\.(?:m3u8|vtt|srt)$")
IMMUTABLE_CACHE: Final = "public, max-age=31536000, immutable"
MUTABLE_CACHE: Final = "public, max-age=60"
PROBE_KEY: Final = "edgeprobe/probe.txt"
PROBE_BODY: Final = b"smart-iptv edge probe\n"
MAX_ATTEMPTS: Final = 5
_LOCK_TTL_S: Final = 3 * 3600
_RUNG: Final = re.compile(r"v[0-9]+")
_VARIANT_LINE: Final = re.compile(r"^(v[0-9]+)/index\.m3u8$", re.MULTILINE)
CONTENT_TYPES: Final = {
    "m3u8": "application/vnd.apple.mpegurl",
    "mp4": "video/mp4",
    "m4v": "video/mp4",
    "m4s": "video/mp4",
    "m4a": "audio/mp4",
    "ts": "video/mp2t",
    "aac": "audio/aac",
    "vtt": "text/vtt",
    "srt": "application/x-subrip",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "mkv": "video/x-matroska",
    "webm": "video/webm",
    "txt": "text/plain",
}


class NotConfiguredError(RuntimeError):
    """The bucket or the publisher's keys are missing in Settings → Storage."""


# --- Configuration ----------------------------------------------------------------------------


def config() -> S3Config:
    endpoint = str(get_setting("storage.s3_endpoint"))
    bucket = str(get_setting("storage.s3_bucket"))
    access, secret = get_secret("storage.s3_access_key"), get_secret("storage.s3_secret_key")
    if not (endpoint and bucket and access and secret):
        msg = "the bucket or the publisher's keys are not set"
        raise NotConfiguredError(msg)
    return S3Config(
        endpoint=endpoint,
        bucket=bucket,
        access_key=access,
        secret_key=secret,
        region=str(get_setting("storage.s3_region")) or "us-east-1",
        path_style=bool(get_setting("storage.s3_path_style")),
    )


def root_prefix() -> str:
    return str(get_setting("storage.s3_prefix")).strip("/")


def object_key(*parts: str) -> str:
    return "/".join(part.strip("/") for part in (root_prefix(), *parts) if part.strip("/"))


def published_name(asset: str, generation: int) -> str:
    """The token title an S3-origin edge reads: `<asset key>-g<generation>`."""
    return f"{asset}-g{generation}"


def client(cfg: S3Config | None = None) -> S3Client:
    rate = int(cast("int", get_setting("storage.publish_mbps"))) * 1_000_000
    return S3Client(cfg or config(), rate_bps=rate)


# --- What to publish --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Planned:
    """One object of the asset: its path in the asset, and a file or generated text."""

    path: str
    size: int
    fingerprint: str
    file: Path | None = None
    text: bytes | None = None

    @property
    def mutable(self) -> bool:
        return bool(MUTABLE.search(self.path))

    @property
    def content_type(self) -> str:
        ext = self.path.rsplit(".", 1)[-1].lower()
        return CONTENT_TYPES.get(ext, "application/octet-stream")

    @property
    def cache_control(self) -> str:
        return MUTABLE_CACHE if self.mutable else IMMUTABLE_CACHE


@dataclass(frozen=True, slots=True)
class Plan:
    objects: list[Planned]
    renditions: list[str]


def _boxes(rows: Iterable[Rendition]) -> dict[str, int]:
    """Rung folder -> its box height (the name the bucket uses: v1-h720)."""
    boxes = {}
    for row in rows:
        if row.kind == RenditionKind.HLS_VARIANT and _RUNG.fullmatch(row.name):
            boxes[row.name] = int((row.details or {}).get("box_height") or row.height or 0)
    return boxes


def rewrite_master(text: str, boxes: dict[str, int]) -> str:
    """A master as the bucket holds it: `v1/index.m3u8` becomes `v1-h720/index.m3u8`."""

    def rung(match: re.Match[str]) -> str:
        name = match.group(1)
        return f"{name}-h{boxes[name]}/index.m3u8" if name in boxes else match.group(0)

    return _VARIANT_LINE.sub(rung, text)


def _file(path: str, file: Path) -> Planned:
    stat = file.stat()
    return Planned(path, stat.st_size, f"{stat.st_mtime_ns}", file=file)


def _text(path: str, data: bytes) -> Planned:
    return Planned(path, len(data), hashlib.sha256(data).hexdigest()[:16], text=data)


def _walk(directory: Path, prefix: str) -> list[Planned]:
    """Regular files under `directory` (links not followed, work folders skipped)."""
    found: list[Planned] = []
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if not layout.is_hidden(d) and not Path(root, d).is_symlink()]
        for name in files:
            entry = Path(root) / name
            if layout.is_hidden(name) or entry.is_symlink():
                continue
            relative = entry.relative_to(directory).as_posix()
            found.append(_file(f"{prefix}/{relative}", entry))
    return found


_STEMS: Final = {
    RenditionKind.COMPAT_MP4: layout.COMPAT,
    RenditionKind.SOURCE: layout.SOURCE,
    RenditionKind.UHD: layout.UHD,
}


def _progressive(root: Path, row: Rendition, *, sources: bool) -> Planned | None:
    entry = root / f"{_STEMS[RenditionKind(row.kind)]}.{row.container}"
    if entry.is_symlink() and not sources:
        return None
    return _file(entry.name, entry.resolve()) if entry.exists() else None


def _ladder(root: Path, boxes: dict[str, int]) -> list[Planned]:
    """The rungs (renamed by height), audio renditions and the UHD rung."""
    objects: list[Planned] = []
    hls = root / layout.HLS
    if hls.is_dir():
        for entry in sorted(hls.iterdir()):
            if layout.is_hidden(entry.name) or entry.is_symlink() or not entry.is_dir():
                continue
            if _RUNG.fullmatch(entry.name):
                if entry.name in boxes:
                    objects += _walk(entry, f"{layout.HLS}/{entry.name}-h{boxes[entry.name]}")
            elif entry.name not in layout.SHARED:
                objects += _walk(entry, f"{layout.HLS}/{entry.name}")
    uhd = layout.presentation(layout.UHD_CEILING)
    uhd_rung = root / uhd / layout.UHD_RUNG
    if uhd_rung.is_dir() and not uhd_rung.is_symlink():
        objects += _walk(uhd_rung, f"{uhd}/{layout.UHD_RUNG}")
    return objects


def plan(file: MediaFile) -> Plan:
    """Every object of the file's asset as the bucket holds it, and its renditions."""
    root = layout.asset_dir(layout.asset_key(file))
    rows = list(
        Rendition.objects.filter(media_file=file, status=RenditionStatus.READY).only(
            "kind", "name", "container", "height", "details"
        )
    )
    boxes = _boxes(rows)
    objects: list[Planned] = []
    renditions: list[str] = []
    sources = bool(get_setting("storage.publish_sources"))
    for row in rows:
        kind = RenditionKind(row.kind)
        if kind in _STEMS:
            item = _progressive(root, row, sources=sources)
            if item is not None:
                objects.append(item)
                renditions.append(_STEMS[kind])
        elif kind == RenditionKind.HLS_MASTER:
            master = root / row.name / layout.MASTER
            if master.is_file():
                text = rewrite_master(master.read_text(encoding="utf-8"), boxes)
                objects.append(_text(f"{row.name}/{layout.MASTER}", text.encode()))
                renditions.append(row.name)
    objects += _ladder(root, boxes)
    for shared in layout.SHARED:
        directory = root / shared
        if directory.is_dir() and not directory.is_symlink():
            objects += _walk(directory, shared)
    unique = {item.path: item for item in objects}
    return Plan(sorted(unique.values(), key=lambda item: item.path), sorted(set(renditions)))


# --- Publishing -----------------------------------------------------------------------------------


def _manifest(objects: Iterable[Planned]) -> dict[str, list[Any]]:
    return {item.path: [item.size, item.fingerprint] for item in objects}


def needs_new_generation(old: dict[str, Any], objects: Iterable[Planned]) -> bool:
    """An immutable object that would change in place forces a new generation."""
    for item in objects:
        previous = old.get(item.path)
        if (
            previous is not None
            and not item.mutable
            and list(previous)
            != [
                item.size,
                item.fingerprint,
            ]
        ):
            return True
    return False


def _upload(s3: S3Client, key: str, item: Planned) -> None:
    if item.text is not None:
        s3.put_bytes(
            key, item.text, content_type=item.content_type, cache_control=item.cache_control
        )
    elif item.file is not None:
        s3.put_file(
            key, item.file, content_type=item.content_type, cache_control=item.cache_control
        )


def publish(file: MediaFile, s3: S3Client | None = None) -> str:
    """Publish the file's asset; returns "published", "unchanged", "empty" or "failed"."""
    publication, _ = AssetPublication.objects.get_or_create(media_file=file)
    asset = layout.asset_key(file)
    wanted = plan(file)
    if not wanted.renditions:
        return "empty"
    old = dict(publication.manifest or {})
    current = publication.generation
    fresh = current == 0 or needs_new_generation(old, wanted.objects)
    target = current + 1 if fresh else current
    manifest = _manifest(wanted.objects)
    if not fresh and manifest == old and sorted(publication.renditions or []) == wanted.renditions:
        if publication.status != PublicationStatus.PUBLISHED:
            AssetPublication.objects.filter(pk=publication.pk).update(
                status=PublicationStatus.PUBLISHED, error=""
            )
        return "unchanged"
    AssetPublication.objects.filter(pk=publication.pk).update(status=PublicationStatus.PUBLISHING)
    own = s3 is None
    s3 = s3 or client()
    try:
        prefix = published_name(asset, target)
        old_prefix = published_name(asset, current)
        # Immutable objects first (copied inside the bucket when unchanged), then
        # playlists and subtitles, so nothing names an object that isn't there yet.
        ordered = sorted(wanted.objects, key=lambda item: (item.mutable, item.path))
        for item in ordered:
            previous = old.get(item.path)
            same = previous is not None and list(previous) == [item.size, item.fingerprint]
            if same and not fresh:
                continue
            if same and fresh and current > 0 and not item.mutable:
                s3.copy(object_key(old_prefix, item.path), object_key(prefix, item.path))
                continue
            _upload(s3, object_key(prefix, item.path), item)
        if not fresh:
            gone = [path for path in old if path not in manifest]
            if gone:
                s3.delete_many([object_key(prefix, path) for path in gone])
    except (S3Error, OSError) as exc:
        message = redact_text(str(exc))[:300]
        AssetPublication.objects.filter(pk=publication.pk).update(
            status=PublicationStatus.FAILED,
            error=message,
            attempts=publication.attempts + 1,
        )
        logger.warning("media.publish_failed", extra={"file": str(file.pk), "error": message})
        return "failed"
    finally:
        if own:
            s3.close()
    retired = list(publication.retired or [])
    if fresh and current > 0:
        retired.append({"generation": current, "at": time.time()})
    with transaction.atomic():
        AssetPublication.objects.filter(pk=publication.pk).update(
            generation=target,
            status=PublicationStatus.PUBLISHED,
            renditions=wanted.renditions,
            manifest=manifest,
            retired=retired,
            published_at=timezone.now(),
            size=sum(item.size for item in wanted.objects),
            object_count=len(wanted.objects),
            attempts=0,
            error="",
        )
    logger.info(
        "media.published",
        extra={"file": str(file.pk), "generation": target, "objects": len(wanted.objects)},
    )
    return "published"


def publish_file_id(file_id: str) -> str:
    file = MediaFile.objects.filter(pk=file_id, removed_at__isnull=True).first()
    if file is None:
        return "gone"
    lock = f"publish:lock:{file_id}"
    try:
        if not state_redis().set(lock, "1", nx=True, ex=_LOCK_TTL_S):
            return "busy"
    except redis.RedisError:
        return "busy"
    try:
        return publish(file)
    except NotConfiguredError:
        return "not_configured"
    finally:
        state_redis().delete(lock)


def mark_stale(file: MediaFile) -> None:
    """A file's outputs changed: queue its publication (after this transaction)."""
    if not get_setting("storage.s3_enabled"):
        return
    from apps.media import publish_tasks  # noqa: PLC0415 (Celery tasks import this module)

    AssetPublication.objects.update_or_create(
        media_file=file, defaults={"status": PublicationStatus.PENDING}
    )
    file_id = str(file.pk)
    transaction.on_commit(lambda: publish_tasks.publish_asset.delay(file_id))


# --- Housekeeping ----------------------------------------------------------------------


def ensure_probe(s3: S3Client) -> None:
    """The edges' Test object (apps.edges.probe) in the bucket."""
    if s3.head(object_key(PROBE_KEY)) is None:
        s3.put_bytes(object_key(PROBE_KEY), PROBE_BODY, content_type="text/plain",
                     cache_control=MUTABLE_CACHE)  # fmt: skip


def reconcile(*, now: float | None = None) -> dict[str, int]:
    """Queue what waits or failed, delete replaced generations past their grace and the
    copies of files that are gone; make sure the edges' probe object exists."""
    moment = time.time() if now is None else now
    counts = {"queued": 0, "retired": 0, "removed": 0}
    try:
        cfg = config()
    except NotConfiguredError:
        return counts
    from apps.media import publish_tasks  # noqa: PLC0415

    grace_s = int(cast("int", get_setting("storage.old_generation_hours"))) * 3600
    with client(cfg) as s3:
        try:
            ensure_probe(s3)
        except S3Error as exc:
            logger.warning("media.publish_probe_failed", extra={"error": str(exc)})
            return counts
        waiting = (
            AssetPublication.objects.filter(media_file__removed_at__isnull=True)
            .filter(status__in=(PublicationStatus.PENDING, PublicationStatus.FAILED))
            .filter(attempts__lt=MAX_ATTEMPTS)
            .values_list("media_file_id", flat=True)[:500]
        )
        for file_id in waiting:
            publish_tasks.publish_asset.delay(str(file_id))
            counts["queued"] += 1
        unpublished = (
            MediaFile.objects.filter(removed_at__isnull=True, publication__isnull=True)
            .filter(renditions__status=RenditionStatus.READY)
            .values_list("pk", flat=True)
            .distinct()[:200]
        )
        for file_id in unpublished:
            AssetPublication.objects.get_or_create(media_file_id=file_id)
            publish_tasks.publish_asset.delay(str(file_id))
            counts["queued"] += 1
        for publication in AssetPublication.objects.exclude(retired=[]).select_related(
            "media_file"
        ):
            asset = layout.asset_key(publication.media_file)
            keep = []
            for item in publication.retired:
                if moment - float(item.get("at", 0)) >= grace_s:
                    s3.delete_prefix(
                        object_key(published_name(asset, int(item["generation"]))) + "/"
                    )
                    counts["retired"] += 1
                else:
                    keep.append(item)
            AssetPublication.objects.filter(pk=publication.pk).update(retired=keep)
        for publication in AssetPublication.objects.filter(
            media_file__removed_at__isnull=False
        ).select_related("media_file"):
            asset = layout.asset_key(publication.media_file)
            for generation in {publication.generation} | {
                int(item["generation"]) for item in publication.retired
            }:
                if generation > 0:
                    s3.delete_prefix(object_key(published_name(asset, generation)) + "/")
            publication.delete()
            counts["removed"] += 1
    return counts


def summary() -> dict[str, Any]:
    """For the Storage page: how many files wait, are published or failed, and bytes."""
    rows = AssetPublication.objects.values_list("status", "size")
    by_status: dict[str, int] = {}
    total = 0
    for status, size in rows:
        by_status[status] = by_status.get(status, 0) + 1
        if status == PublicationStatus.PUBLISHED:
            total += size
    return {"by_status": by_status, "published_bytes": total}
