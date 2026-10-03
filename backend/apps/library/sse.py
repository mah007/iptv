"""Live scan progress for the admin: a server-sent event stream (SPEC §7.1, §8.3).

GET /api/v1/admin/libraries/<id>/scan/stream answers `text/event-stream`:

- first the library's latest full scan, if any, as an event;
- then every update published on the Redis channel `admin.scan.<library_id>`;
- a comment line (`: ping`) after 15 s without one, so proxies keep the connection.

Each event is `event: scan` with the JSON of `apps.library.services.job_event` as data.
The view is async (ASGI): a stream holds no worker thread while it waits. Sign-in and
the `library.view` (or `library.manage`) permission are checked as for the REST API.
"""

import json
from collections.abc import AsyncIterator
from typing import Any, Final
from uuid import UUID

import redis.asyncio as aioredis
from asgiref.sync import sync_to_async
from django.conf import settings
from django.http import HttpRequest, HttpResponseBase, StreamingHttpResponse

from apps.accounts.models import User
from apps.accounts.rbac import permission_codes
from apps.core.errors import ErrorCode, problem_response
from apps.library.models import Library, ScanJob
from apps.library.services import job_event, scan_channel

HEARTBEAT_S: Final = 15.0
REQUIRED: Final = frozenset({"library.view", "library.manage"})


def format_event(data: dict[str, Any], event: str = "scan") -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def _snapshot(library_id: UUID) -> tuple[bool, dict[str, Any] | None]:
    """(library exists, its latest full scan as an event)."""
    if not Library.objects.filter(pk=library_id).exists():
        return False, None
    job = ScanJob.objects.filter(library_id=library_id, path="").order_by("-created_at").first()
    return True, (job_event(job) if job is not None else None)


def _allowed(user: Any) -> bool:
    return isinstance(user, User) and bool(permission_codes(user) & REQUIRED)


async def _events(library_id: UUID, initial: dict[str, Any] | None) -> AsyncIterator[str]:
    client = aioredis.Redis.from_url(settings.REDIS_STATE_URL)
    pubsub = client.pubsub()
    try:
        await pubsub.subscribe(scan_channel(library_id))
        yield "retry: 5000\n\n"
        if initial is not None:
            yield format_event(initial)
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=HEARTBEAT_S)
            if message is None:
                yield ": ping\n\n"
                continue
            try:
                data = json.loads(message["data"])
            except (TypeError, ValueError):
                continue
            yield format_event(data)
    finally:
        await pubsub.aclose()
        await client.aclose()


async def scan_stream(request: HttpRequest, pk: UUID) -> HttpResponseBase:
    user = await request.auser()
    if not user.is_authenticated:
        return problem_response(ErrorCode.NOT_AUTHENTICATED)
    if not await sync_to_async(_allowed)(user):
        return problem_response(ErrorCode.PERMISSION_DENIED)
    exists, initial = await sync_to_async(_snapshot)(pk)
    if not exists:
        return problem_response(ErrorCode.NOT_FOUND)
    response = StreamingHttpResponse(_events(pk, initial), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache, no-store"
    response["X-Accel-Buffering"] = "no"
    return response
