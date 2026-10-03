"""Catalogue change notifications.

`catalog_changed` is sent after a change that alters what customers can browse: a title
added, edited, re-classified or changing status, files linked or removed, categories
created, edited, reordered or deleted. Derived caches (the Xtream catalogue JSON in
redis-cache, SPEC §7.5) invalidate themselves from it. It is sent once the transaction
commits, so receivers never see rolled-back changes.

Arguments: `kind` ("movie", "series" or "category") and `ids` (a tuple of primary keys,
possibly empty when many objects changed).
"""

from collections.abc import Iterable
from uuid import UUID

from django.db import transaction
from django.dispatch import Signal

catalog_changed = Signal()


def notify_catalog_changed(kind: str, ids: Iterable[UUID] = ()) -> None:
    """Send `catalog_changed` after the current transaction commits."""
    frozen = tuple(ids)
    transaction.on_commit(
        lambda: catalog_changed.send(sender=notify_catalog_changed, kind=kind, ids=frozen)
    )
