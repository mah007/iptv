"""Artwork an admin picks for a title (SPEC §8.3 image picker), stored on the worker.

A TMDB alternative goes through the metadata pipeline (`store_images`, which also works
in the offline fixture mode); an upload goes straight to the image converter (WebP and
AVIF at every size, blurhash) and is recorded as `source=upload`.
"""

from __future__ import annotations

from typing import Any, cast

import structlog
from django.db import transaction

from apps.catalog.models import Episode, ImageSource, MediaImage, Movie, Series
from apps.catalog.signals import notify_catalog_changed

logger = structlog.get_logger(__name__)

_MODELS: dict[str, type[Movie] | type[Series] | type[Episode]] = {
    "movie": Movie,
    "series": Series,
    "episode": Episode,
}


def store(  # noqa: PLR0913
    title_kind: str,
    title_id: str,
    *,
    kind: str,
    tmdb_path: str | None,
    upload: bytes | None,
    primary: bool,
) -> bool:
    """Store one image for the title; True when a new image was recorded."""
    from apps.metadata import images  # noqa: PLC0415
    from apps.metadata import services as metadata  # noqa: PLC0415

    model = _MODELS.get(title_kind)
    title: Any = model.objects.filter(pk=title_id).first() if model else None
    if title is None:
        return False
    owner = {title_kind: title}
    owner_key = f"{title_kind}/{title.pk}"
    if tmdb_path:
        pick = metadata.ImagePick(kind, tmdb_path, "", primary)
        added = metadata.store_images(owner, owner_key, [pick], metadata.tmdb()) > 0
        if added and primary:
            _make_primary(owner, kind, source_path=tmdb_path)
    elif upload is not None:
        try:
            stored = images.store_image(
                upload,
                owner=owner_key,
                kind=cast("images.ImageKind", kind),
                store=metadata.image_store(),
            )
        except (images.ImageError, ValueError) as exc:
            logger.warning("media.image_upload_rejected", owner=owner_key, error=str(exc)[:120])
            return False
        with transaction.atomic():
            if primary:
                MediaImage.objects.filter(**owner, kind=kind).update(is_primary=False)
            MediaImage.objects.create(
                **owner,
                kind=kind,
                sizes=stored.keys(),
                width=stored.width,
                height=stored.height,
                blurhash=stored.blurhash,
                source=ImageSource.UPLOAD,
                source_path=f"upload:{stored.source_sha256[:16]}",
                is_primary=primary,
            )
        added = True
    else:
        return False
    if added:
        series_id = title.season.series_id if title_kind == "episode" else title.pk
        notify_catalog_changed("movie" if title_kind == "movie" else "series", [series_id])
    return added


def _make_primary(owner: dict[str, Any], kind: str, *, source_path: str) -> None:
    with transaction.atomic():
        MediaImage.objects.filter(**owner, kind=kind).exclude(source_path=source_path).update(
            is_primary=False
        )
        MediaImage.objects.filter(**owner, kind=kind, source_path=source_path).update(
            is_primary=True
        )
