"""Admin API of transcode jobs (SPEC §8.3.10 Transcode Jobs, §10 admin).

- `GET transcode-jobs`: newest first, filtered by status, backend or file.
- `POST transcode-jobs/{id}/retry`: queue a failed or cancelled job again.
- `POST transcode-jobs/{id}/cancel`: stop a queued or running job (ffmpeg is killed
  within a second).
- `POST transcode-jobs/{id}/priority`: 0-9, higher runs first; a queued job is
  re-sent with the new priority.
- `GET transcode-jobs/stream`: live progress as server-sent events (`apps.media.feed`).

RBAC: `library.view` (or `library.manage`) reads, `library.manage` acts. Actions are
audited by `apps.media.services`.
"""

from typing import Any, ClassVar
from uuid import UUID

from django.db.models import QuerySet
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import generics
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import HasPermission, Requirements
from apps.core.http import acting_user, client_ip
from apps.core.schema import problems
from apps.media import feed, services
from apps.media.filters import TranscodeJobFilter
from apps.media.models import ACTIVE_JOB_STATUSES, TranscodeJob
from apps.media.serializers import PrioritySerializer, TranscodeJobSerializer
from apps.playback.api import EVENT_STREAM, EventStreamRenderer

VIEW: tuple[str, ...] = ("library.view", "library.manage")


def jobs() -> QuerySet[TranscodeJob]:
    return TranscodeJob.objects.select_related(
        "media_file__library", "media_file__movie"
    ).prefetch_related("media_file__episodes__season__series")


def _body(pk: UUID) -> dict[str, Any]:
    return dict(TranscodeJobSerializer(jobs().get(pk=pk)).data)


@extend_schema_view(
    get=extend_schema(
        operation_id="transcode_jobs_list",
        summary="List transcode jobs, newest first",
        responses={200: TranscodeJobSerializer(many=True), **problems(400, 401, 403)},
    ),
)
class JobListView(generics.ListAPIView[TranscodeJob]):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"GET": VIEW}
    serializer_class = TranscodeJobSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = TranscodeJobFilter

    def get_queryset(self) -> QuerySet[TranscodeJob]:
        return jobs().order_by("-created_at", "-id")


class _JobAction(APIView):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"POST": "library.manage"}


class JobRetryView(_JobAction):
    @extend_schema(
        operation_id="transcode_jobs_retry",
        summary="Queue a failed or cancelled job again",
        description="Attempts start from zero. 409 unless the job failed or was cancelled.",
        request=None,
        responses={200: TranscodeJobSerializer, **problems(401, 403, 404, 409)},
    )
    def post(self, request: Request, pk: UUID) -> Response:
        job = get_object_or_404(TranscodeJob, pk=pk)
        services.retry_job(job, actor=acting_user(request), ip=client_ip(request))
        return Response(_body(pk))


class JobCancelView(_JobAction):
    @extend_schema(
        operation_id="transcode_jobs_cancel",
        summary="Cancel a queued or running job",
        description="A running ffmpeg is stopped within a second. 409 for a finished job.",
        request=None,
        responses={200: TranscodeJobSerializer, **problems(401, 403, 404, 409)},
    )
    def post(self, request: Request, pk: UUID) -> Response:
        job = get_object_or_404(TranscodeJob, pk=pk)
        services.cancel_job(job, actor=acting_user(request), ip=client_ip(request))
        return Response(_body(pk))


class JobPriorityView(_JobAction):
    @extend_schema(
        operation_id="transcode_jobs_priority",
        summary="Change a queued or running job's priority",
        description="0-9, higher runs first. 409 for a finished job.",
        request=PrioritySerializer,
        responses={200: TranscodeJobSerializer, **problems(400, 401, 403, 404, 409)},
    )
    def post(self, request: Request, pk: UUID) -> Response:
        job = get_object_or_404(TranscodeJob, pk=pk)
        body = PrioritySerializer(data=request.data)
        body.is_valid(raise_exception=True)
        services.set_priority(
            job,
            body.validated_data["priority"],
            actor=acting_user(request),
            ip=client_ip(request),
        )
        return Response(_body(pk))


class JobStreamView(APIView):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"GET": VIEW}
    renderer_classes = (JSONRenderer, EventStreamRenderer)
    # Bounds the stream in tests; None streams until the client disconnects.
    limit: ClassVar[int | None] = None

    @extend_schema(
        operation_id="transcode_jobs_stream",
        summary="Live transcode progress as server-sent events",
        description=(
            'Server-sent events: first `snapshot` `{"jobs": [TranscodeJob]}` with the '
            "queued and running jobs, then a `job` event `{id, status, progress, fps, "
            "speed, eta_s, backend, encoder, priority, attempts, worker_host, error}` "
            "for every update (progress at most once a second per job), and a keep-alive "
            "comment after 15 s without one."
        ),
        responses={
            (200, EVENT_STREAM): OpenApiResponse(
                response=OpenApiTypes.STR, description="An endless text/event-stream."
            ),
            **problems(401, 403),
        },
    )
    def get(self, request: Request) -> StreamingHttpResponse:
        active = jobs().filter(status__in=ACTIVE_JOB_STATUSES).order_by("-priority", "created_at")
        snapshot = [dict(row) for row in TranscodeJobSerializer(active, many=True).data]
        response = StreamingHttpResponse(
            feed.job_events(snapshot, limit=self.limit), content_type=EVENT_STREAM
        )
        response["Cache-Control"] = "no-store"
        response["X-Accel-Buffering"] = "no"
        return response
