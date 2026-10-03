"""Catalogue change notifications.

`catalog_changed` is sent after a change that alters what customers can browse: a title
added, edited, re-classified or changing status, files linked or removed, artwork stored,
categories created, edited, reordered or deleted. It is sent once the transaction
commits, so receivers never see rolled-back changes.

`notify_catalog_changed` also retires the Xtream catalogue JSON cached in redis-cache
(SPEC §7.5) after the commit, so IPTV apps never browse a stale catalogue.

Arguments: `kind` ("movie", "series" or "category") and `ids` (a tuple of primary keys,
possibly empty when many objects changed).
"""

from collections.abc import Iterable
from uuid import UUID

from django.db import transaction
from django.dispatch import Signal

catalog_changed = Signal()


def notify_catalog_changed(kind: str, ids: Iterable[UUID] = ()) -> None:
    """Send `catalog_changed` and invalidate the Xtream cache after the transaction commits."""
    from apps.xtream_api.cache import invalidate_on_commit  # noqa: PLC0415 (no import cycle)

    frozen = tuple(ids)
    transaction.on_commit(
        lambda: catalog_changed.send(sender=notify_catalog_changed, kind=kind, ids=frozen)
    )
    invalidate_on_commit()
