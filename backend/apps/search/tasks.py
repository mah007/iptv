"""Search indexing jobs (SPEC §7.8): incremental on catalogue changes, a nightly
rebuild with an atomic swap. They run on the `default` queue."""

import contextlib
from collections.abc import Sequence
from typing import Any, Final
from uuid import UUID

import redis
import structlog
from celery import shared_task

from apps.core.stores import state_redis
from apps.search import index
from apps.search.meili import MeiliError

logger = structlog.get_logger(__name__)

#: A full rebuild is scheduled at most once per this many seconds (category changes,
#: bulk edits and a missing index all ask for one).
REBUILD_DEBOUNCE_S: Final = 60
_REBUILD_FLAG: Final = "search:rebuild:scheduled"


@shared_task(
    name="apps.search.tasks.index_titles",
    autoretry_for=(MeiliError,),
    retry_backoff=10,
    retry_backoff_max=300,
    max_retries=5,
)
def index_titles(kind: str, ids: Sequence[str]) -> int:
    count = index.index_titles(kind, [UUID(value) for value in ids])
    logger.info("search.indexed", kind=kind, titles=len(ids), documents=count)
    return count


@shared_task(
    name="apps.search.tasks.rebuild_index",
    autoretry_for=(MeiliError,),
    retry_backoff=30,
    retry_backoff_max=600,
    max_retries=3,
)
def rebuild_index() -> dict[str, int]:
    with contextlib.suppress(redis.RedisError):
        state_redis().delete(_REBUILD_FLAG)
    counts = index.rebuild()
    logger.info("search.rebuilt", **counts)
    return counts


def schedule_rebuild() -> None:
    """A full rebuild in a little while, unless one is already scheduled."""
    try:
        first = state_redis().set(_REBUILD_FLAG, "1", nx=True, ex=REBUILD_DEBOUNCE_S)
    except redis.RedisError:
        first = True
    if first:
        rebuild_index.apply_async(countdown=10)


def on_catalog_changed(
    sender: Any = None, kind: str = "", ids: Sequence[UUID] = (), **_: Any
) -> None:
    """`catalog_changed` receiver (sent after commit): index what changed."""
    if kind in {"movie", "series"} and ids:
        index_titles.delay(kind, [str(pk) for pk in ids])
    elif kind in {"movie", "series", "category"}:
        schedule_rebuild()
