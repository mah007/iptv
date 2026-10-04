"""Admin API of the dashboard and system health (SPEC §8.3, §10 admin/dashboard/*, admin/health)."""

from typing import Any, ClassVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db.models import QuerySet
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.accounts.permissions import HasPermission, Requirements, request_permission_codes
from apps.audit.models import AuditLog
from apps.core.errors import ErrorCode, ProblemError
from apps.core.schema import problems
from apps.dashboard import activity, charts, health, services, storage
from apps.dashboard.serializers import (
    ActivityQuerySerializer,
    ActivitySerializer,
    HealthSerializer,
    KpisSerializer,
    StorageUsageSerializer,
    TimeseriesSerializer,
    WatchHistorySerializer,
)
from apps.engagement.models import WatchProgress

DEFAULT_TIME_ZONE = "Asia/Riyadh"


class KpisView(APIView):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"GET": "dashboard.view"}

    @extend_schema(
        operation_id="dashboard_kpis",
        summary="Customer, device, stream and queue KPIs (cached for 30 s)",
        responses={200: KpisSerializer, **problems(401, 403)},
    )
    def get(self, request: Request) -> Response:
        return Response(KpisSerializer(services.kpis()).data)


def _time_zone(request: Request) -> ZoneInfo:
    try:
        return ZoneInfo(getattr(request.user, "timezone", "") or DEFAULT_TIME_ZONE)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TIME_ZONE)


class TimeseriesView(APIView):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"GET": "dashboard.view"}

    @extend_schema(
        operation_id="dashboard_timeseries",
        summary="Dashboard charts: streams over 24 h, days over 30 d, top categories and "
        "titles (the admin's time zone; cached for 30 s)",
        responses={200: TimeseriesSerializer, **problems(401, 403)},
    )
    def get(self, request: Request) -> Response:
        return Response(TimeseriesSerializer(charts.timeseries(_time_zone(request))).data)


class StorageView(APIView):
    """Storage usage (SPEC §8.3 Storage); the cleanup itself is `admin/renditions/cleanup`."""

    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"GET": ("library.view", "library.manage")}

    @extend_schema(
        operation_id="storage_usage",
        summary="Storage usage: by library, sources against renditions, growth over 90 days "
        "and the largest titles (the admin's time zone; cached for 30 s)",
        responses={200: StorageUsageSerializer, **problems(401, 403)},
    )
    def get(self, request: Request) -> Response:
        return Response(StorageUsageSerializer(storage.storage(_time_zone(request))).data)


class ActivityView(generics.ListAPIView[AuditLog]):
    """Recent activity from the audit log, newest first; one customer's with `?customer=`."""

    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {
        "GET": ("dashboard.view", "customers.view", "audit.view")
    }
    serializer_class = ActivitySerializer
    filter_backends = ()

    def _customer(self) -> Any:
        query = ActivityQuerySerializer(data=self.request.query_params)
        query.is_valid(raise_exception=True)
        return query.validated_data.get("customer")

    def get_queryset(self) -> Any:
        customer = self._customer()
        codes = request_permission_codes(self.request)
        if customer is None and "dashboard.view" not in codes:
            raise ProblemError(ErrorCode.PERMISSION_DENIED, "The feed needs dashboard.view.")
        if customer is not None and "customers.view" not in codes:
            raise ProblemError(
                ErrorCode.PERMISSION_DENIED, "A customer's feed needs customers.view."
            )
        return activity.feed(customer=customer)

    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        page = self.paginate_queryset(self.get_queryset())
        entries = list(page or [])
        context = {
            **self.get_serializer_context(),
            "subjects": activity.subjects(entries),
            "with_changes": "audit.view" in request_permission_codes(request),
        }
        return self.get_paginated_response(
            ActivitySerializer(entries, many=True, context=context).data  # type: ignore[arg-type]
        )

    @extend_schema(
        operation_id="dashboard_activity",
        summary="Recent activity (audit entries with readable targets), newest first",
        parameters=[
            OpenApiParameter(
                "customer",
                str,
                required=False,
                description="Only what was done to this customer, their devices, access "
                "rules and sessions (needs customers.view). Without it: the dashboard feed "
                "(needs dashboard.view), sign-ins left out.",
            )
        ],
        responses={200: ActivitySerializer(many=True), **problems(400, 401, 403)},
    )
    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return super().get(request, *args, **kwargs)


class HealthView(APIView):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"GET": "settings.view"}

    @extend_schema(
        operation_id="system_health",
        summary="Service status, queue depths, transcoders, Redis and Postgres (live)",
        responses={200: HealthSerializer, **problems(401, 403)},
    )
    def get(self, request: Request) -> Response:
        return Response(HealthSerializer(health.collect()).data)


class CustomerHistoryView(generics.ListAPIView[WatchProgress]):
    """GET /api/v1/admin/customers/{id}/history: what the customer watched, newest first."""

    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"GET": "customers.view"}
    serializer_class = WatchHistorySerializer
    filter_backends = ()

    def get_queryset(self) -> QuerySet[WatchProgress]:
        customer = get_object_or_404(User.objects.filter(is_staff=False), pk=self.kwargs["pk"])
        return (
            WatchProgress.objects.filter(user=customer)
            .select_related("movie", "episode__season__series")
            .order_by("-updated_at", "-id")
        )

    @extend_schema(
        operation_id="customers_history",
        summary="A customer's watch history (progress per title), newest first",
        responses={200: WatchHistorySerializer(many=True), **problems(401, 403, 404)},
    )
    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return super().get(request, *args, **kwargs)
