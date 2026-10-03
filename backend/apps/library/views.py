"""Admin API of libraries and scans (SPEC §8.3, docs/plans/poc.md slice 2).

RBAC: `library.view` reads, `library.manage` creates, changes, deletes and scans.
Changes are audited by `apps.library.services`. The live progress stream of a scan is
a server-sent event stream (`apps.library.sse`), outside the OpenAPI schema.
"""

from typing import Any, ClassVar
from uuid import UUID

from django.db.models import OuterRef, QuerySet, Subquery
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import generics, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import HasPermission, Requirements
from apps.core.http import acting_user, client_ip
from apps.core.schema import problems
from apps.library import services
from apps.library.models import Library, ScanJob
from apps.library.serializers import (
    LibrarySerializer,
    LibraryWriteSerializer,
    ScanJobSerializer,
    ScanStartedSerializer,
)

VIEW: tuple[str, ...] = ("library.view", "library.manage")


def library_queryset() -> QuerySet[Library]:
    latest = ScanJob.objects.filter(library=OuterRef("pk"), path="").order_by("-created_at")
    return (
        Library.objects.prefetch_related("default_categories")
        .annotate(last_scan_id=Subquery(latest.values("pk")[:1]))
        .order_by("name", "id")
    )


def attach_last_scans(libraries: list[Library]) -> list[Library]:
    """Give each library its latest full scan (`last_scan`) with one query."""
    ids = [getattr(lib, "last_scan_id", None) for lib in libraries]
    jobs = ScanJob.objects.select_related("library").in_bulk([pk for pk in ids if pk])
    for library in libraries:
        library.last_scan = jobs.get(getattr(library, "last_scan_id", None))  # type: ignore[attr-defined]
    return libraries


def library_body(pk: UUID) -> dict[str, Any]:
    library = attach_last_scans([get_object_or_404(library_queryset(), pk=pk)])[0]
    return dict(LibrarySerializer(library).data)


@extend_schema_view(
    get=extend_schema(
        operation_id="libraries_list",
        summary="List libraries with their totals and latest scan",
        responses={200: LibrarySerializer(many=True), **problems(400, 401, 403)},
    ),
    post=extend_schema(
        operation_id="libraries_create",
        summary="Add a library: a readable folder under the media root",
        request=LibraryWriteSerializer,
        responses={201: LibrarySerializer, **problems(400, 401, 403, 409)},
    ),
)
class LibraryListView(generics.ListCreateAPIView[Library]):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"GET": VIEW, "POST": "library.manage"}
    serializer_class = LibrarySerializer
    filter_backends = ()

    def get_queryset(self) -> QuerySet[Library]:
        return library_queryset()

    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        page = self.paginate_queryset(self.get_queryset())
        libraries = attach_last_scans(list(page if page is not None else self.get_queryset()))
        data = LibrarySerializer(libraries, many=True).data
        return self.get_paginated_response(data) if page is not None else Response(data)

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        payload = LibraryWriteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        library = services.create_library(
            payload.validated_data, actor=acting_user(request), ip=client_ip(request)
        )
        return Response(library_body(library.pk), status=status.HTTP_201_CREATED)


class LibraryDetailView(APIView):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {
        "GET": VIEW,
        "PATCH": "library.manage",
        "DELETE": "library.manage",
    }

    @extend_schema(
        operation_id="libraries_retrieve",
        summary="One library with its totals and latest scan",
        responses={200: LibrarySerializer, **problems(401, 403, 404)},
    )
    def get(self, request: Request, pk: UUID) -> Response:
        return Response(library_body(pk))

    @extend_schema(
        operation_id="libraries_update",
        summary="Change a library",
        request=LibraryWriteSerializer,
        responses={200: LibrarySerializer, **problems(400, 401, 403, 404, 409)},
    )
    def patch(self, request: Request, pk: UUID) -> Response:
        library = get_object_or_404(Library, pk=pk)
        payload = LibraryWriteSerializer(library, data=request.data, partial=True)
        payload.is_valid(raise_exception=True)
        services.update_library(
            library, payload.validated_data, actor=acting_user(request), ip=client_ip(request)
        )
        return Response(library_body(pk))

    @extend_schema(
        operation_id="libraries_delete",
        summary="Delete a library and its file records (titles stay, hidden if left empty)",
        responses={204: None, **problems(401, 403, 404)},
    )
    def delete(self, request: Request, pk: UUID) -> Response:
        library = get_object_or_404(Library, pk=pk)
        services.delete_library(library, actor=acting_user(request), ip=client_ip(request))
        return Response(status=status.HTTP_204_NO_CONTENT)


class LibraryScanView(APIView):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"POST": "library.manage"}

    @extend_schema(
        operation_id="libraries_scan",
        summary="Scan the library now (returns the running scan if there is one)",
        request=None,
        responses={202: ScanStartedSerializer, **problems(401, 403, 404)},
    )
    def post(self, request: Request, pk: UUID) -> Response:
        library = get_object_or_404(Library, pk=pk)
        job, created = services.request_scan(
            library, actor=acting_user(request), ip=client_ip(request)
        )
        job = ScanJob.objects.select_related("library").get(pk=job.pk)
        body = {"job": ScanJobSerializer(job).data, "created": created}
        return Response(body, status=status.HTTP_202_ACCEPTED)


@extend_schema_view(
    get=extend_schema(
        operation_id="scans_list",
        summary="Scan history, newest first",
        responses={200: ScanJobSerializer(many=True), **problems(400, 401, 403)},
    )
)
class ScanListView(generics.ListAPIView[ScanJob]):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"GET": VIEW}
    serializer_class = ScanJobSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_fields = ("library", "status", "trigger")

    def get_queryset(self) -> QuerySet[ScanJob]:
        return ScanJob.objects.select_related("library").order_by("-created_at", "-id")


class ScanDetailView(APIView):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"GET": VIEW}

    @extend_schema(
        operation_id="scans_retrieve",
        summary="One scan with its counts and log",
        responses={200: ScanJobSerializer, **problems(401, 403, 404)},
    )
    def get(self, request: Request, pk: UUID) -> Response:
        job = get_object_or_404(ScanJob.objects.select_related("library"), pk=pk)
        return Response(ScanJobSerializer(job).data)
