"""Admin API of live TV and the guide (`/api/v1/admin/live/...`, ADR-0017).

Reading needs `library.view` (or manage/review), changing needs `library.manage`:
channels are content, like titles. Source tests, guide imports, syncs and logos are
Celery tasks on the worker (the web process has no FFmpeg, internet or media volume).
"""

import base64
import json
from datetime import timedelta
from typing import Any, ClassVar
from uuid import UUID

from django.db.models import Count, Q, QuerySet
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import generics, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import HasPermission, Requirements
from apps.audit import services as audit
from apps.core.errors import ErrorCode, ProblemError, field_error
from apps.core.http import acting_user, client_ip
from apps.core.ids import uuid7
from apps.core.schema import problems
from apps.live import conf, integrations, services, sources, state, tasks
from apps.live.models import EpgChannel, EpgProgram, EpgSource, LiveChannel, LiveIntegration
from apps.live.serializers import (
    BulkEnableSerializer,
    ChannelReorderSerializer,
    EpgChannelSerializer,
    EpgSourceSerializer,
    EpgSourceWriteSerializer,
    LiveChannelSerializer,
    LiveChannelWriteSerializer,
    LiveIntegrationSerializer,
    LiveIntegrationWriteSerializer,
    LiveOverviewSerializer,
    LogoSerializer,
    ProgrammeSerializer,
    SourceTestQueuedSerializer,
    SourceTestRequestSerializer,
    SourceTestResultSerializer,
    SyncResultSerializer,
    UnmatchedSerializer,
)
from apps.live.xtream import resolve_guides

VIEW: tuple[str, ...] = ("library.view", "library.manage", "library.review")
MANAGE = "library.manage"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_LOGO_BYTES = 5 * 1024 * 1024
MAX_PREVIEW = 500


class LiveView(APIView):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {}


def _channel_context(channels: list[LiveChannel]) -> dict[str, Any]:
    keys = [channel.storage_key for channel in channels]
    return {
        "statuses": state.statuses(keys),
        "viewers": services.viewers_by_channel(channel.pk for channel in channels),
        "archives": state.archive_windows(
            channel.storage_key for channel in channels if channel.catchup_days
        ),
    }


def _channels() -> QuerySet[LiveChannel]:
    return LiveChannel.objects.select_related("group")


# --- Channels ------------------------------------------------------------------------------


@extend_schema_view(
    get=extend_schema(
        operation_id="live_channels_list",
        summary="List live channels",
        parameters=[
            OpenApiParameter("group", str, description="A live group (category) id."),
            OpenApiParameter("enabled", bool),
            OpenApiParameter("q", str, description="Search the names and the XMLTV id."),
        ],
        responses={200: LiveChannelSerializer(many=True), **problems(401, 403)},
    ),
    post=extend_schema(
        operation_id="live_channels_create",
        summary="Create a live channel",
        request=LiveChannelWriteSerializer,
        responses={201: LiveChannelSerializer, **problems(400, 401, 403)},
    ),
)
class LiveChannelListView(generics.ListCreateAPIView[LiveChannel]):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"GET": VIEW, "POST": MANAGE}
    serializer_class = LiveChannelSerializer

    def get_queryset(self) -> QuerySet[LiveChannel]:
        rows = _channels().order_by("group__sort", "group__xc_id", "sort", "xc_id")
        params = self.request.query_params
        if params.get("group"):
            try:
                rows = rows.filter(group_id=UUID(params["group"]))
            except ValueError:
                return rows.none()
        if params.get("enabled") in ("true", "false"):
            rows = rows.filter(enabled=params["enabled"] == "true")
        query = params.get("q", "").strip()
        if query:
            rows = rows.filter(
                Q(name__icontains=query)
                | Q(name_ar__icontains=query)
                | Q(epg_channel_id__icontains=query)
            )
        return rows

    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        rows = self.get_queryset()
        page = self.paginate_queryset(rows)
        items = list(page if page is not None else rows)
        data = LiveChannelSerializer(items, many=True, context=_channel_context(items)).data
        return self.get_paginated_response(data) if page is not None else Response(data)

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        payload = LiveChannelWriteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        values = dict(payload.validated_data)
        source_url = values.pop("source_url")
        channel = services.create_channel(
            values, source_url=source_url, actor=acting_user(request), ip=client_ip(request)
        )
        channel = _channels().get(pk=channel.pk)
        data = LiveChannelSerializer(channel, context=_channel_context([channel])).data
        return Response(data, status=status.HTTP_201_CREATED)


class LiveChannelDetailView(LiveView):
    required_permissions: ClassVar[Requirements] = {"GET": VIEW, "PATCH": MANAGE, "DELETE": MANAGE}

    def _get(self, pk: UUID) -> LiveChannel:
        return get_object_or_404(_channels(), pk=pk)

    @extend_schema(
        operation_id="live_channels_retrieve",
        summary="One live channel",
        responses={200: LiveChannelSerializer, **problems(401, 403, 404)},
    )
    def get(self, request: Request, pk: UUID) -> Response:
        channel = self._get(pk)
        return Response(LiveChannelSerializer(channel, context=_channel_context([channel])).data)

    @extend_schema(
        operation_id="live_channels_update",
        summary="Change a live channel (the source URL only when given)",
        request=LiveChannelWriteSerializer,
        responses={200: LiveChannelSerializer, **problems(400, 401, 403, 404)},
    )
    def patch(self, request: Request, pk: UUID) -> Response:
        channel = self._get(pk)
        payload = LiveChannelWriteSerializer(channel, data=request.data, partial=True)
        payload.is_valid(raise_exception=True)
        values = dict(payload.validated_data)
        source_url = values.pop("source_url", None)
        services.update_channel(
            channel,
            values,
            source_url=source_url,
            actor=acting_user(request),
            ip=client_ip(request),
        )
        channel = self._get(pk)
        return Response(LiveChannelSerializer(channel, context=_channel_context([channel])).data)

    @extend_schema(
        operation_id="live_channels_delete",
        summary="Delete a live channel (its sessions stop)",
        responses={204: None, **problems(401, 403, 404)},
    )
    def delete(self, request: Request, pk: UUID) -> Response:
        services.delete_channel(self._get(pk), actor=acting_user(request), ip=client_ip(request))
        return Response(status=status.HTTP_204_NO_CONTENT)


class LiveChannelBulkView(LiveView):
    required_permissions: ClassVar[Requirements] = {"POST": MANAGE}

    @extend_schema(
        operation_id="live_channels_bulk_enable",
        summary="Enable or disable channels (enabling needs a rights holder)",
        request=BulkEnableSerializer,
        responses={200: LiveChannelSerializer(many=True), **problems(400, 401, 403, 404)},
    )
    def post(self, request: Request) -> Response:
        payload = BulkEnableSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        ids = payload.validated_data["ids"]
        services.set_enabled(
            ids,
            payload.validated_data["enabled"],
            actor=acting_user(request),
            ip=client_ip(request),
        )
        channels = list(_channels().filter(pk__in=ids).order_by("sort", "xc_id"))
        return Response(
            LiveChannelSerializer(channels, many=True, context=_channel_context(channels)).data
        )


class LiveChannelReorderView(LiveView):
    required_permissions: ClassVar[Requirements] = {"POST": MANAGE}

    @extend_schema(
        operation_id="live_channels_reorder",
        summary="Order a group's channels",
        request=ChannelReorderSerializer,
        responses={200: LiveChannelSerializer(many=True), **problems(400, 401, 403)},
    )
    def post(self, request: Request) -> Response:
        payload = ChannelReorderSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        ordered = services.reorder_channels(
            payload.validated_data["group"],
            payload.validated_data["ids"],
            actor=acting_user(request),
            ip=client_ip(request),
        )
        channels = list(_channels().filter(pk__in=[c.pk for c in ordered]).order_by("sort"))
        return Response(
            LiveChannelSerializer(channels, many=True, context=_channel_context(channels)).data
        )


class LiveChannelLogoView(LiveView):
    required_permissions: ClassVar[Requirements] = {"POST": MANAGE}
    parser_classes = (MultiPartParser, FormParser, JSONParser)

    @extend_schema(
        operation_id="live_channels_logo",
        summary="Upload a logo, or give an http(s) URL to fetch it from (stored on the worker)",
        request={"multipart/form-data": LogoSerializer, "application/json": LogoSerializer},
        responses={202: None, **problems(400, 401, 403, 404)},
    )
    def post(self, request: Request, pk: UUID) -> Response:
        channel = get_object_or_404(LiveChannel, pk=pk)
        payload = LogoSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        upload = payload.validated_data.get("file")
        url = payload.validated_data.get("url", "")
        if upload is None and not url:
            raise ProblemError(
                ErrorCode.VALIDATION_ERROR,
                "Upload a file or give a URL.",
                field_errors={
                    "file": [field_error("A file or a URL is required.", code="required")]
                },
            )
        upload_b64 = ""
        if upload is not None:
            if upload.size > MAX_LOGO_BYTES:
                raise ProblemError(
                    ErrorCode.VALIDATION_ERROR,
                    "The logo is too large.",
                    field_errors={"file": [field_error("At most 5 MB.", code="max_size")]},
                )
            upload_b64 = base64.b64encode(upload.read()).decode("ascii")
        audit.record(
            "live.channel.logo", actor=acting_user(request), target=channel, ip=client_ip(request)
        )
        tasks.store_channel_logo.delay(str(channel.pk), upload_b64=upload_b64, url=url)
        return Response(status=status.HTTP_202_ACCEPTED)


class LiveChannelTestView(LiveView):
    required_permissions: ClassVar[Requirements] = {"POST": MANAGE}

    @extend_schema(
        operation_id="live_channels_test",
        summary="Test a channel's source (ffprobe on the worker); poll the result",
        request=None,
        responses={202: SourceTestQueuedSerializer, **problems(401, 403, 404)},
    )
    def post(self, request: Request, pk: UUID) -> Response:
        channel = get_object_or_404(LiveChannel, pk=pk)
        request_id = uuid7().hex
        tasks.probe_channel.delay(str(channel.pk), request_id)
        return Response({"request_id": request_id}, status=status.HTTP_202_ACCEPTED)


class SourceTestView(LiveView):
    required_permissions: ClassVar[Requirements] = {"POST": MANAGE}

    @extend_schema(
        operation_id="live_source_test",
        summary="Test a source URL before saving it; poll the result",
        request=SourceTestRequestSerializer,
        responses={202: SourceTestQueuedSerializer, **problems(400, 401, 403)},
    )
    def post(self, request: Request) -> Response:
        payload = SourceTestRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        request_id = uuid7().hex
        tasks.probe_url.delay(sources.encrypt(payload.validated_data["url"]), request_id)
        return Response({"request_id": request_id}, status=status.HTTP_202_ACCEPTED)


class SourceTestResultView(LiveView):
    required_permissions: ClassVar[Requirements] = {"GET": MANAGE}

    @extend_schema(
        operation_id="live_source_test_result",
        summary="The result of a source test (pending until the worker answers)",
        responses={200: SourceTestResultSerializer, **problems(401, 403)},
    )
    def get(self, request: Request, request_id: str) -> Response:
        result = tasks.read_probe(request_id) if request_id.isalnum() else None
        return Response({"status": "done" if result else "pending", "result": result})


class LiveChannelProgrammesView(LiveView):
    required_permissions: ClassVar[Requirements] = {"GET": VIEW}

    @extend_schema(
        operation_id="live_channels_programmes",
        summary="A channel's programmes between two instants (default: today, a day ahead)",
        parameters=[
            OpenApiParameter("from", str, description="ISO 8601 start (default now - 12 h)."),
            OpenApiParameter("to", str, description="ISO 8601 end (default from + 36 h)."),
        ],
        responses={200: ProgrammeSerializer(many=True), **problems(400, 401, 403, 404)},
    )
    def get(self, request: Request, pk: UUID) -> Response:
        channel = get_object_or_404(LiveChannel, pk=pk)
        now = timezone.now()
        start = parse_datetime(request.query_params.get("from", "")) or now - timedelta(hours=12)
        end = parse_datetime(request.query_params.get("to", "")) or start + timedelta(hours=36)
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            raise ProblemError(ErrorCode.VALIDATION_ERROR, "Give an aware from before to.")
        guide = resolve_guides([channel]).get(channel.storage_key)
        if guide is None:
            return Response([])
        rows = EpgProgram.objects.filter(
            channel=guide, start__gte=start - timedelta(days=1), start__lt=end, stop__gt=start
        ).order_by("start")[:MAX_PREVIEW]
        return Response(ProgrammeSerializer(rows, many=True).data)


# --- Guide sources ---------------------------------------------------------------------------


def _sources() -> QuerySet[EpgSource]:
    return EpgSource.objects.annotate(channel_count=Count("channels"))


def _upload(payload: EpgSourceWriteSerializer) -> tuple[bytes | None, str]:
    upload = payload.validated_data.get("file")
    if upload is None:
        return None, ""
    if upload.size > MAX_UPLOAD_BYTES:
        raise ProblemError(
            ErrorCode.VALIDATION_ERROR,
            "The file is too large.",
            field_errors={"file": [field_error("At most 50 MB.", code="max_size")]},
        )
    return upload.read(), str(upload.name or "")


@extend_schema_view(
    get=extend_schema(
        operation_id="live_epg_sources_list",
        summary="List guide sources",
        responses={200: EpgSourceSerializer(many=True), **problems(401, 403)},
    ),
    post=extend_schema(
        operation_id="live_epg_sources_create",
        summary="Add a guide source: an XMLTV URL or an uploaded file (imported at once)",
        request={
            "application/json": EpgSourceWriteSerializer,
            "multipart/form-data": EpgSourceWriteSerializer,
        },
        responses={201: EpgSourceSerializer, **problems(400, 401, 403)},
    ),
)
class EpgSourceListView(generics.ListCreateAPIView[EpgSource]):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"GET": VIEW, "POST": MANAGE}
    serializer_class = EpgSourceSerializer
    pagination_class = None
    parser_classes = (JSONParser, MultiPartParser, FormParser)

    def get_queryset(self) -> QuerySet[EpgSource]:
        return _sources().order_by("priority", "name", "id")

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        payload = EpgSourceWriteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        if not payload.validated_data.get("name"):
            raise ProblemError(
                ErrorCode.VALIDATION_ERROR,
                "Name the source.",
                field_errors={"name": [field_error("This field is required.", code="required")]},
            )
        upload, upload_name = _upload(payload)
        source = services.create_epg_source(
            payload.validated_data,
            url=payload.validated_data.get("url"),
            upload=upload,
            upload_name=upload_name,
            actor=acting_user(request),
            ip=client_ip(request),
        )
        return Response(
            EpgSourceSerializer(_sources().get(pk=source.pk)).data, status=status.HTTP_201_CREATED
        )


class EpgSourceDetailView(LiveView):
    required_permissions: ClassVar[Requirements] = {"GET": VIEW, "PATCH": MANAGE, "DELETE": MANAGE}
    parser_classes = (JSONParser, MultiPartParser, FormParser)

    @extend_schema(
        operation_id="live_epg_sources_retrieve",
        summary="One guide source",
        responses={200: EpgSourceSerializer, **problems(401, 403, 404)},
    )
    def get(self, request: Request, pk: UUID) -> Response:
        return Response(EpgSourceSerializer(get_object_or_404(_sources(), pk=pk)).data)

    @extend_schema(
        operation_id="live_epg_sources_update",
        summary="Change a guide source (a new URL or file is imported at once)",
        request={
            "application/json": EpgSourceWriteSerializer,
            "multipart/form-data": EpgSourceWriteSerializer,
        },
        responses={200: EpgSourceSerializer, **problems(400, 401, 403, 404)},
    )
    def patch(self, request: Request, pk: UUID) -> Response:
        source = get_object_or_404(EpgSource, pk=pk)
        payload = EpgSourceWriteSerializer(data=request.data, partial=True)
        payload.is_valid(raise_exception=True)
        upload, upload_name = _upload(payload)
        services.update_epg_source(
            source,
            payload.validated_data,
            url=payload.validated_data.get("url"),
            upload=upload,
            upload_name=upload_name,
            actor=acting_user(request),
            ip=client_ip(request),
        )
        return Response(EpgSourceSerializer(_sources().get(pk=pk)).data)

    @extend_schema(
        operation_id="live_epg_sources_delete",
        summary="Delete a guide source and its programmes",
        responses={204: None, **problems(401, 403, 404)},
    )
    def delete(self, request: Request, pk: UUID) -> Response:
        services.delete_epg_source(
            get_object_or_404(EpgSource, pk=pk), actor=acting_user(request), ip=client_ip(request)
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class EpgSourceRefreshView(LiveView):
    required_permissions: ClassVar[Requirements] = {"POST": MANAGE}

    @extend_schema(
        operation_id="live_epg_sources_refresh",
        summary="Import a guide source now",
        request=None,
        responses={202: None, **problems(401, 403, 404)},
    )
    def post(self, request: Request, pk: UUID) -> Response:
        source = get_object_or_404(EpgSource, pk=pk)
        audit.record(
            "live.epg_source.refresh",
            actor=acting_user(request),
            target=source,
            ip=client_ip(request),
        )
        tasks.import_epg_source.delay(str(source.pk))
        return Response(status=status.HTTP_202_ACCEPTED)


class EpgChannelSearchView(LiveView):
    required_permissions: ClassVar[Requirements] = {"GET": VIEW}

    @extend_schema(
        operation_id="live_epg_channels",
        summary="Search the guide's channels (for mapping a live channel)",
        parameters=[OpenApiParameter("q", str), OpenApiParameter("source", str)],
        responses={200: EpgChannelSerializer(many=True), **problems(401, 403)},
    )
    def get(self, request: Request) -> Response:
        rows = EpgChannel.objects.select_related("source").order_by("xmltv_id", "source__priority")
        query = request.query_params.get("q", "").strip()
        if query:
            rows = rows.filter(Q(xmltv_id__icontains=query) | Q(names__icontains=query))
        source = request.query_params.get("source", "")
        if source:
            try:
                rows = rows.filter(source_id=UUID(source))
            except ValueError:
                rows = rows.none()
        return Response(EpgChannelSerializer(rows[:50], many=True).data)


def _suggestions(channel: LiveChannel, guide: list[EpgChannel]) -> list[EpgChannel]:
    from rapidfuzz import fuzz, process  # noqa: PLC0415

    names = {index: item.display_name() for index, item in enumerate(guide)}
    query = channel.name_ar if not channel.name else channel.name
    matches = process.extract(query, names, scorer=fuzz.token_set_ratio, limit=3, score_cutoff=60)
    return [guide[index] for _, _, index in matches]


class EpgUnmatchedView(LiveView):
    required_permissions: ClassVar[Requirements] = {"GET": VIEW}

    @extend_schema(
        operation_id="live_epg_unmatched",
        summary="Channels without a guide, and guide channels no channel uses",
        responses={200: UnmatchedSerializer, **problems(401, 403)},
    )
    def get(self, request: Request) -> Response:
        channels = list(LiveChannel.objects.order_by("name"))
        resolved = resolve_guides(channels)
        guide = list(EpgChannel.objects.select_related("source").order_by("xmltv_id")[:5000])
        used = {channel.epg_channel_id for channel in channels if channel.epg_channel_id}
        missing = []
        for channel in channels:
            if channel.storage_key in resolved:
                continue
            missing.append(
                {
                    "id": channel.pk,
                    "name": channel.name,
                    "epg_channel_id": channel.epg_channel_id,
                    "reason": "not_in_guide" if channel.epg_channel_id else "no_id",
                    "suggestions": EpgChannelSerializer(
                        _suggestions(channel, guide), many=True
                    ).data,
                }
            )
        unused = [
            {
                "xmltv_id": item.xmltv_id,
                "name": item.display_name(),
                "source": item.source_id,
                "source_name": item.source.name,
            }
            for item in guide
            if item.xmltv_id not in used
        ][:200]
        return Response({"channels": missing, "guide_channels": unused})


# --- Integrations ------------------------------------------------------------------------------


def _integrations() -> QuerySet[LiveIntegration]:
    return LiveIntegration.objects.annotate(channel_count=Count("channels"))


def _secrets(payload: LiveIntegrationWriteSerializer, current: dict[str, str]) -> dict[str, str]:
    values = dict(current)
    for name in ("username", "password", "access_token"):
        if name in payload.validated_data:
            values[name] = payload.validated_data[name]
    return values


def _integration_state(integration: LiveIntegration) -> dict[str, Any]:
    return {
        "kind": integration.kind,
        "name": integration.name,
        "base_url": integration.base_url,
        "rights_holder": integration.rights_holder,
        "license_ref": integration.license_ref,
        "group": str(integration.group_id) if integration.group_id else None,
    }


_INTEGRATION_FIELDS = (
    "kind",
    "name",
    "base_url",
    "stream_base_url",
    "group",
    "rights_holder",
    "license_ref",
)


@extend_schema_view(
    get=extend_schema(
        operation_id="live_integrations_list",
        summary="List ErsatzTV and MediaMTX instances channels are synced from",
        responses={200: LiveIntegrationSerializer(many=True), **problems(401, 403)},
    ),
    post=extend_schema(
        operation_id="live_integrations_create",
        summary="Add an ErsatzTV or MediaMTX instance",
        request=LiveIntegrationWriteSerializer,
        responses={201: LiveIntegrationSerializer, **problems(400, 401, 403)},
    ),
)
class LiveIntegrationListView(generics.ListCreateAPIView[LiveIntegration]):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"GET": VIEW, "POST": MANAGE}
    serializer_class = LiveIntegrationSerializer
    pagination_class = None

    def get_queryset(self) -> QuerySet[LiveIntegration]:
        return _integrations().order_by("name", "id")

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        payload = LiveIntegrationWriteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        required = ("kind", "name", "base_url", "rights_holder")
        missing = [name for name in required if not data.get(name)]
        if data.get("kind") == "mediamtx" and not data.get("stream_base_url"):
            missing.append("stream_base_url")
        if missing:
            raise ProblemError(
                ErrorCode.VALIDATION_ERROR,
                "Some fields are required.",
                field_errors={
                    name: [field_error("This field is required.", code="required")]
                    for name in missing
                },
            )
        integration = LiveIntegration(
            **{name: data[name] for name in _INTEGRATION_FIELDS if name in data}
        )
        integration.credentials_encrypted = integrations.encrypt_credentials(_secrets(payload, {}))
        integration.save()
        audit.record(
            "live.integration.create",
            actor=acting_user(request),
            target=integration,
            after=_integration_state(integration),
            ip=client_ip(request),
        )
        data_out = LiveIntegrationSerializer(_integrations().get(pk=integration.pk)).data
        return Response(data_out, status=status.HTTP_201_CREATED)


class LiveIntegrationDetailView(LiveView):
    required_permissions: ClassVar[Requirements] = {"GET": VIEW, "PATCH": MANAGE, "DELETE": MANAGE}

    @extend_schema(
        operation_id="live_integrations_retrieve",
        summary="One integration",
        responses={200: LiveIntegrationSerializer, **problems(401, 403, 404)},
    )
    def get(self, request: Request, pk: UUID) -> Response:
        return Response(LiveIntegrationSerializer(get_object_or_404(_integrations(), pk=pk)).data)

    @extend_schema(
        operation_id="live_integrations_update",
        summary="Change an integration (secrets only when given)",
        request=LiveIntegrationWriteSerializer,
        responses={200: LiveIntegrationSerializer, **problems(400, 401, 403, 404)},
    )
    def patch(self, request: Request, pk: UUID) -> Response:
        integration = get_object_or_404(LiveIntegration, pk=pk)
        before = _integration_state(integration)
        payload = LiveIntegrationWriteSerializer(data=request.data, partial=True)
        payload.is_valid(raise_exception=True)
        for name in _INTEGRATION_FIELDS:
            if name in payload.validated_data and name != "kind":
                setattr(integration, name, payload.validated_data[name])
        integration.credentials_encrypted = integrations.encrypt_credentials(
            _secrets(payload, integrations.credentials(integration))
        )
        integration.save()
        audit.record(
            "live.integration.update",
            actor=acting_user(request),
            target=integration,
            before=before,
            after=_integration_state(integration),
            ip=client_ip(request),
        )
        return Response(LiveIntegrationSerializer(_integrations().get(pk=pk)).data)

    @extend_schema(
        operation_id="live_integrations_delete",
        summary="Delete an integration (its channels stay, unlinked)",
        responses={204: None, **problems(401, 403, 404)},
    )
    def delete(self, request: Request, pk: UUID) -> Response:
        integration = get_object_or_404(LiveIntegration, pk=pk)
        audit.record(
            "live.integration.delete",
            actor=acting_user(request),
            target=integration,
            before=_integration_state(integration),
            ip=client_ip(request),
        )
        integration.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class LiveIntegrationSyncView(LiveView):
    required_permissions: ClassVar[Requirements] = {"POST": MANAGE}

    @extend_schema(
        operation_id="live_integrations_sync",
        summary="Sync channels from the instance now (new ones arrive disabled)",
        request=None,
        responses={202: SyncResultSerializer, **problems(401, 403, 404)},
    )
    def post(self, request: Request, pk: UUID) -> Response:
        integration = get_object_or_404(LiveIntegration, pk=pk)
        audit.record(
            "live.integration.sync",
            actor=acting_user(request),
            target=integration,
            ip=client_ip(request),
        )
        tasks.sync_integration.delay(str(integration.pk))
        return Response({"queued": True}, status=status.HTTP_202_ACCEPTED)


class LiveOverviewView(LiveView):
    required_permissions: ClassVar[Requirements] = {"GET": VIEW}

    @extend_schema(
        operation_id="live_overview",
        summary="Live TV at a glance: packager, channels, viewers and the archive's disk use",
        responses={200: LiveOverviewSerializer, **problems(401, 403)},
    )
    def get(self, request: Request) -> Response:
        heartbeat = state.packager()
        report: dict[str, Any] = {}
        if heartbeat:
            try:
                report = json.loads(heartbeat)
            except ValueError:
                report = {}
        counts = LiveChannel.objects.aggregate(
            channels=Count("id"),
            enabled_channels=Count("id", filter=Q(enabled=True)),
            catchup_channels=Count("id", filter=Q(catchup_days__gt=0, enabled=True)),
        )
        viewers = sum(
            services.viewers_by_channel(LiveChannel.objects.values_list("pk", flat=True)).values()
        )
        return Response(
            {
                "packager_running": bool(heartbeat),
                "running_channels": int(report.get("running", 0)),
                "archive_bytes": int(report.get("archive_bytes", 0)),
                "archive_budget_bytes": int(
                    report.get("archive_budget_bytes", conf.archive_max_bytes())
                ),
                "viewers": viewers,
                **counts,
            }
        )
