"""Plan a media file's outputs once it is matched to a title (ADR-0010).

Every path that links a file (automatic matching, review resolution, a rescan of a
linked file) saves it as `matched`; the receiver queues `prepare_media_file` after the
commit. The task is idempotent, so repeated saves cost one cheap check each; beat's
`reconcile_media` catches anything this misses (a broker outage, for example).
"""

from typing import Any

import structlog
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.catalog.models import FileState, MediaFile

logger = structlog.get_logger(__name__)


def _enqueue(file_id: str) -> None:
    from apps.media import tasks  # noqa: PLC0415 (tasks import the services)

    try:
        tasks.prepare_media_file.delay(file_id)
    except Exception:
        logger.warning("media.prepare_enqueue_failed", file=file_id)


@receiver(post_save, sender=MediaFile, dispatch_uid="media.prepare_matched_file")
def prepare_matched_file(sender: type[MediaFile], instance: MediaFile, **kwargs: Any) -> None:
    if instance.state != FileState.MATCHED or instance.removed_at is not None:
        return
    file_id = str(instance.pk)
    transaction.on_commit(lambda: _enqueue(file_id))
