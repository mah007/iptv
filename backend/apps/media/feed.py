"""The admin transcode feed: `GET /api/v1/admin/transcode-jobs/stream`, SSE.

First a `snapshot` event, `{"jobs": [Job, ...]}` with every queued or running job,
then a `job` event for every update the transcoders publish on the redis-state
channel `admin.transcode` (progress at most once a second per job), and a
`: keep-alive` comment after 15 s without one. A Job here is
`apps.media.services.event`: `{id, status, progress, fps, speed, eta_s, backend,
encoder, priority, attempts, worker_host, error}`.
"""

import json
from collections.abc import AsyncIterator
from typing import Any, Final

import redis.asyncio as aioredis
from django.conf import settings
from redis.exceptions import RedisError

from apps.media.services import CHANNEL

KEEPALIVE_S: Final = 15.0
RETRY_MS: Final = 5000


def event(name: str, data: object) -> bytes:
    payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False, default=str)
    return f"event: {name}\ndata: {payload}\n\n".encode()


async def job_events(
    snapshot: list[dict[str, Any]], *, limit: int | None = None
) -> AsyncIterator[bytes]:
    """The SSE byte stream; `limit` bounds the number of `job` events (tests)."""
    client = aioredis.Redis.from_url(settings.REDIS_STATE_URL)
    pubsub = client.pubsub()
    try:
        await pubsub.subscribe(CHANNEL)
        yield f"retry: {RETRY_MS}\n".encode() + event("snapshot", {"jobs": snapshot})
        sent = 0
        while limit is None or sent < limit:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=KEEPALIVE_S)
            if message is None:
                yield b": keep-alive\n\n"
                continue
            try:
                data = json.loads(message["data"])
            except (TypeError, ValueError):
                continue
            sent += 1
            yield event("job", data)
    except RedisError:
        return
    finally:
        await pubsub.aclose()
        await client.aclose()
