"""Admin API of imports (ADR-0025): sources, their Test and browser, jobs.

RBAC: `library.view` reads, `library.manage` changes sources and starts or cancels
imports. Adding a source, or changing one's credentials or where they are sent, needs a
recent confirmation (step-up, ADR-0021); so does deleting one. Everything that changes
is audited by `apps.imports.services`.

A Test and a folder of the browser run rclone in the importer: the POST answers 202 with
a request id, and `imports/requests/{id}` answers `pending` until the importer is done.
"""

from typing import Any, ClassVar, cast
from uuid import UUID

from django.db.models import Prefetch, QuerySet
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import generics, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts import stepup
from apps.accounts.permissions import HasPermission, Requirements
from apps.core.errors import ErrorCode, ProblemError, field_error
from apps.core.http import acting_user, client_ip
from apps.core.schema import problems
from apps.imports import services
from apps.imports.models import ACTIVE_JOB_STATUSES, ImportFile, ImportJob, ImportSource
from apps.imports.serializers import (
    BrowseRequestSerializer,
    ImportJobDetailSerializer,
    ImportJobSerializer,
    ImportSourceSerializer,
    ImportSourceWriteSerializer,
    ImportStatusSerializer,
    LinkJobCreateSerializer,
    RemoteAnswerSerializer,
    RequestQueuedSerializer,
    SourceJobCreateSerializer,
)
from apps.library.models import Library

VIEW: tuple[str, ...] = ("library.view", "library.manage")
MANAGE = "library.manage"
_CONFLICTS = frozenset({"source_name_taken", "source_busy", "finished"})


def problem(exc: services.ImportRefused) -> ProblemError:
    """A refusal as RFC 9457 problem+json: the stable reason in `reason` (and on the
    field it concerns), its numbers or names as extension members."""
    field = exc.params.get("field")
    extensions = {key: value for key, value in exc.params.items() if key != "field"}
    return ProblemError(
        ErrorCode.CONFLICT if exc.code in _CONFLICTS else ErrorCode.VALIDATION_ERROR,
        exc.message,
        field_errors={str(field): [field_error(exc.message, code=exc.code)]} if field else None,
        extensions={"reason": exc.code, **extensions},
    )


class ImportView(APIView):
    permission_classes = (HasPermission,)


class ImportStatusView(ImportView):
    required_permissions: ClassVar[Requirements] = {"GET": VIEW}

    @extend_schema(
        operation_id="imports_status",
        summary="The importer's state, the import settings and each library's free space",
        responses={200: ImportStatusSerializer, **problems(401, 403)},
    )
    def get(self, request: Request) -> Response:
        libraries = []
        for library in Library.objects.order_by("name", "id"):
            space = services.free_space(library)
            libraries.append(
                {
                    "id": library.pk,
                    "name": library.name,
                    "kind": library.kind,
                    "free_bytes": space["free"] if space else None,
                    "total_bytes": space["total"] if space else None,
                }
            )
        body = {
            "importer_online": services.importer_online(),
            "max_concurrent": services.max_concurrent(),
            "reserve_bytes": services.reserve_bytes(),
            "bandwidth_mbit": services.bwlimit_bytes() // 125_000,
            "libraries": libraries,
        }
        return Response(ImportStatusSerializer(body).data)


def _write_payload(request: Request) -> dict[str, Any]:
    payload = ImportSourceWriteSerializer(data=request.data)
    payload.is_valid(raise_exception=True)
    return cast("dict[str, Any]", payload.validated_data)


@extend_schema_view(
    get=extend_schema(
        operation_id="import_sources_list",
        summary="Import sources (credentials never included)",
        responses={200: ImportSourceSerializer(many=True), **problems(401, 403)},
    ),
    post=extend_schema(
        operation_id="import_sources_create",
        summary="Add an import source (needs a recent confirmation)",
        request=ImportSourceWriteSerializer,
        responses={201: ImportSourceSerializer, **problems(400, 401, 403, 409)},
    ),
)
class ImportSourceListView(generics.ListCreateAPIView[ImportSource]):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"GET": VIEW, "POST": MANAGE}
    step_up_methods = frozenset({"POST"})
    serializer_class = ImportSourceSerializer
    pagination_class = None
    filter_backends = ()

    def get_queryset(self) -> QuerySet[ImportSource]:
        return ImportSource.objects.order_by("name", "id")

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        data = _write_payload(request)
        missing = [name for name in ("name", "kind") if not data.get(name)]
        if missing:
            raise ProblemError(
                ErrorCode.VALIDATION_ERROR,
                "Some fields are required.",
                field_errors={
                    name: [field_error("This field is required.", code="required")]
                    for name in missing
                },
            )
        try:
            source = services.create_source(
                name=data["name"].strip(),
                kind=data["kind"],
                options=data.get("options", {}),
                secrets=data.get("secrets", {}),
                actor=acting_user(request),
                ip=client_ip(request),
            )
        except services.ImportRefused as exc:
            raise problem(exc) from None
        return Response(ImportSourceSerializer(source).data, status=status.HTTP_201_CREATED)


class ImportSourceDetailView(ImportView):
    required_permissions: ClassVar[Requirements] = {"GET": VIEW, "PATCH": MANAGE, "DELETE": MANAGE}
    step_up_methods = frozenset({"DELETE"})

    @extend_schema(
        operation_id="import_sources_retrieve",
        summary="One import source",
        responses={200: ImportSourceSerializer, **problems(401, 403, 404)},
    )
    def get(self, request: Request, pk: UUID) -> Response:
        return Response(ImportSourceSerializer(get_object_or_404(ImportSource, pk=pk)).data)

    @extend_schema(
        operation_id="import_sources_update",
        summary="Change a source; new credentials or a new address need a recent confirmation",
        request=ImportSourceWriteSerializer,
        responses={200: ImportSourceSerializer, **problems(400, 401, 403, 404, 409)},
    )
    def patch(self, request: Request, pk: UUID) -> Response:
        source = get_object_or_404(ImportSource, pk=pk)
        data = _write_payload(request)
        if data.get("kind") and data["kind"] != source.kind:
            raise ProblemError(
                ErrorCode.VALIDATION_ERROR,
                "A source's kind can't change; add a new source instead.",
                field_errors={"kind": [field_error("Can't change.", code="kind_fixed")]},
            )
        secrets = data.get("secrets", {})
        options = data.get("options")
        clear = data.get("clear_secrets", [])
        if clear or services.credentials_change(source, secrets, options or {}):
            stepup.require(request)
        try:
            services.update_source(
                source,
                name=(data.get("name") or "").strip() or None,
                options=options,
                secrets=secrets,
                clear=clear,
                actor=acting_user(request),
                ip=client_ip(request),
            )
        except services.ImportRefused as exc:
            raise problem(exc) from None
        return Response(ImportSourceSerializer(ImportSource.objects.get(pk=pk)).data)

    @extend_schema(
        operation_id="import_sources_delete",
        summary="Delete a source (the history of its imports stays)",
        responses={204: None, **problems(401, 403, 404, 409)},
    )
    def delete(self, request: Request, pk: UUID) -> Response:
        source = get_object_or_404(ImportSource, pk=pk)
        try:
            services.delete_source(source, actor=acting_user(request), ip=client_ip(request))
        except services.ImportRefused as exc:
            raise problem(exc) from None
        return Response(status=status.HTTP_204_NO_CONTENT)


class ImportSourceTestView(ImportView):
    required_permissions: ClassVar[Requirements] = {"POST": MANAGE}

    @extend_schema(
        operation_id="import_sources_test",
        summary="Test a source: the importer lists its root folder; poll the answer",
        request=None,
        responses={202: RequestQueuedSerializer, **problems(401, 403, 404)},
    )
    def post(self, request: Request, pk: UUID) -> Response:
        source = get_object_or_404(ImportSource, pk=pk)
        request_id = services.queue_request("test", source)
        return Response({"request_id": request_id}, status=status.HTTP_202_ACCEPTED)


class ImportSourceBrowseView(ImportView):
    required_permissions: ClassVar[Requirements] = {"POST": MANAGE}

    @extend_schema(
        operation_id="import_sources_browse",
        summary="List one folder of a source (sizes included); poll the answer",
        request=BrowseRequestSerializer,
        responses={202: RequestQueuedSerializer, **problems(400, 401, 403, 404)},
    )
    def post(self, request: Request, pk: UUID) -> Response:
        source = get_object_or_404(ImportSource, pk=pk)
        payload = BrowseRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        request_id = services.queue_request("browse", source, payload.validated_data["path"])
        return Response({"request_id": request_id}, status=status.HTTP_202_ACCEPTED)


class ImportRequestView(ImportView):
    required_permissions: ClassVar[Requirements] = {"GET": MANAGE}

    @extend_schema(
        operation_id="import_requests_retrieve",
        summary="The answer to a Test or a browse (pending until the importer is done)",
        responses={200: RemoteAnswerSerializer, **problems(401, 403, 404)},
    )
    def get(self, request: Request, request_id: str) -> Response:
        stored = services.read_result(request_id) if request_id.isalnum() else None
        if stored is None:
            raise ProblemError(ErrorCode.NOT_FOUND, "No such request (they last ten minutes).")
        if stored.get("status") != "done":
            return Response({"status": "pending", "result": None})
        return Response({"status": "done", "result": stored.get("result")})


def _created_job(job: ImportJob) -> Response:
    return Response(ImportJobSerializer(job).data, status=status.HTTP_201_CREATED)


@extend_schema_view(
    get=extend_schema(
        operation_id="import_jobs_list",
        summary="Imports, newest first: the queue (state=active) or the history",
        parameters=[
            OpenApiParameter(
                "state", str, enum=["active", "finished"], description="Only these imports."
            )
        ],
        responses={200: ImportJobSerializer(many=True), **problems(400, 401, 403)},
    ),
    post=extend_schema(
        operation_id="import_jobs_create",
        summary="Import files or folders of a source into a library, under the stated rights",
        request=SourceJobCreateSerializer,
        responses={201: ImportJobSerializer, **problems(400, 401, 403)},
    ),
)
class ImportJobListView(generics.ListCreateAPIView[ImportJob]):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"GET": VIEW, "POST": MANAGE}
    serializer_class = ImportJobSerializer
    filter_backends = ()

    def get_queryset(self) -> QuerySet[ImportJob]:
        jobs = ImportJob.objects.order_by("-created_at", "-id")
        state = self.request.query_params.get("state", "")
        if state == "active":
            return jobs.filter(status__in=ACTIVE_JOB_STATUSES).order_by("created_at", "id")
        if state == "finished":
            return jobs.exclude(status__in=ACTIVE_JOB_STATUSES)
        return jobs

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        payload = SourceJobCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        try:
            job = services.create_source_job(
                source=data["source"],
                items=data["items"],
                library=data["library"],
                subfolder=data["subfolder"],
                rights_holder=data["rights_holder"],
                license_ref=data["license_ref"],
                license_expires_at=data["license_expires_at"],
                actor=acting_user(request),
                ip=client_ip(request),
            )
        except services.ImportRefused as exc:
            raise problem(exc) from None
        return _created_job(job)


class ImportLinkView(ImportView):
    required_permissions: ClassVar[Requirements] = {"POST": MANAGE}

    @extend_schema(
        operation_id="import_links_create",
        summary="Import one file from a direct HTTPS link into a library, under the stated rights",
        request=LinkJobCreateSerializer,
        responses={201: ImportJobSerializer, **problems(400, 401, 403)},
    )
    def post(self, request: Request) -> Response:
        payload = LinkJobCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        try:
            job = services.create_link_job(
                url=data["url"],
                file_name=data["file_name"],
                checksum=data["checksum"],
                library=data["library"],
                subfolder=data["subfolder"],
                rights_holder=data["rights_holder"],
                license_ref=data["license_ref"],
                license_expires_at=data["license_expires_at"],
                actor=acting_user(request),
                ip=client_ip(request),
            )
        except services.ImportRefused as exc:
            raise problem(exc) from None
        return _created_job(job)


class ImportJobDetailView(ImportView):
    required_permissions: ClassVar[Requirements] = {"GET": VIEW}

    @extend_schema(
        operation_id="import_jobs_retrieve",
        summary="One import with the files it placed (library-relative paths)",
        responses={200: ImportJobDetailSerializer, **problems(401, 403, 404)},
    )
    def get(self, request: Request, pk: UUID) -> Response:
        jobs = ImportJob.objects.prefetch_related(
            Prefetch("files", queryset=ImportFile.objects.order_by("storage_key"))
        )
        return Response(ImportJobDetailSerializer(get_object_or_404(jobs, pk=pk)).data)


class ImportJobCancelView(ImportView):
    required_permissions: ClassVar[Requirements] = {"POST": MANAGE}

    @extend_schema(
        operation_id="import_jobs_cancel",
        summary="Cancel an import: a queued one ends now, a running one within seconds",
        request=None,
        responses={200: ImportJobSerializer, **problems(401, 403, 404, 409)},
    )
    def post(self, request: Request, pk: UUID) -> Response:
        job = get_object_or_404(ImportJob, pk=pk)
        try:
            job = services.cancel_job(job, actor=acting_user(request), ip=client_ip(request))
        except services.ImportRefused as exc:
            raise problem(exc) from None
        return Response(ImportJobSerializer(job).data)
