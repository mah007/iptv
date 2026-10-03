"""Admin API of the playback app (SPEC §8.3.4 Live Sessions, §10 admin/sessions).

- `GET sessions`: session history and open sessions (filters, search, ordering).
- `POST sessions/{id}/kill`: stop one session (RBAC `sessions.kill`, audited). The
  edge refuses its next request once its 60 s stream-auth cache entry expires.
- `GET sessions/stream`: the live feed as server-sent events (`apps.playback.feed`).
"""

import json
from collections.abc import Mapping
from typing import Any, ClassVar
from uuid import UUID

from django.db.models import QuerySet
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import filters, generics
from rest_framework.renderers import BaseRenderer, JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import HasPermission, Requirements
from apps.core.http import acting_user, client_ip
from apps.core.schema import problems
from apps.playback import conf, feed, services
from apps.playback.filters import SessionFilter
from apps.playback.models import PlaybackSession
from apps.playback.serializers import SessionSerializer

EVENT_STREAM = "text/event-stream"


def sessions() -> QuerySet[PlaybackSession]:
    return PlaybackSession.objects.select_related("user", "device")


@extend_schema_view(
    get=extend_schema(
        operation_id="sessions_list",
        summary="List playback sessions, newest first",
        description="`active=true` lists the sessions still open.",
        responses={200: SessionSerializer(many=True), **problems(400, 401, 403)},
    ),
)
class SessionListView(generics.ListAPIView[PlaybackSession]):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"GET": "customers.view"}
    serializer_class = SessionSerializer
    filter_backends = (DjangoFilterBackend, filters.SearchFilter)
    filterset_class = SessionFilter
    search_fields = ("user__username", "user__name", "user__email", "device__name", "title_name")

    def get_queryset(self) -> QuerySet[PlaybackSession]:
        return sessions().order_by("-started_at", "-id")


class SessionKillView(APIView):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"POST": "sessions.kill"}

    @extend_schema(
        operation_id="sessions_kill",
        summary="Stop a playback session",
        description=(
            "Playback stops on the player's next request, within the edge's 60 s "
            "stream-auth cache. 409 when the session has already ended."
        ),
        request=None,
        responses={200: SessionSerializer, **problems(401, 403, 404, 409)},
    )
    def post(self, request: Request, pk: UUID) -> Response:
        session = get_object_or_404(PlaybackSession, pk=pk)
        services.kill_session(session, actor=acting_user(request), ip=client_ip(request))
        return Response(SessionSerializer(sessions().get(pk=pk)).data)


class EventStreamRenderer(BaseRenderer):
    """Lets `Accept: text/event-stream` through content negotiation; the stream itself
    is a StreamingHttpResponse. Errors on this view render as JSON text."""

    media_type = EVENT_STREAM
    format = "sse"

    def render(
        self,
        data: Any,
        accepted_media_type: str | None = None,
        renderer_context: Mapping[str, Any] | None = None,
    ) -> bytes:
        return b"" if data is None else json.dumps(data).encode()


class SessionStreamView(APIView):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"GET": "customers.view"}
    renderer_classes = (JSONRenderer, EventStreamRenderer)
    # Bounds the stream in tests; None streams until the client disconnects.
    polls: ClassVar[int | None] = None

    @extend_schema(
        operation_id="sessions_stream",
        summary="Live sessions as server-sent events",
        description=(
            "Server-sent events, polled every 2 s from redis-state: first `snapshot` "
            '`{"sessions": [Session]}`, then `diff` `{"added": [Session], "updated": '
            '[Session], "removed": [id]}` whenever something changed, and a keep-alive '
            "comment every 15 s otherwise. A Session is `{id, user{id,name}, "
            "device{id,name}, title{kind,id,name}, rendition, ip, country, edge, "
            "started_at, last_seen_at, bytes_sent}`; `id` is what sessions/{id}/kill takes."
        ),
        responses={
            (200, EVENT_STREAM): OpenApiResponse(
                response=OpenApiTypes.STR, description="An endless text/event-stream."
            ),
            **problems(401, 403),
        },
    )
    def get(self, request: Request) -> StreamingHttpResponse:
        response = StreamingHttpResponse(
            feed.session_events(interval=conf.feed_interval_s(), polls=self.polls),
            content_type=EVENT_STREAM,
        )
        response["Cache-Control"] = "no-store"
        response["X-Accel-Buffering"] = "no"
        return response
