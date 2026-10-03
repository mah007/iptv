"""Admin API of the dashboard (SPEC §8.3 Dashboard, §10 admin/dashboard/kpis)."""

from typing import ClassVar

from drf_spectacular.utils import extend_schema
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import HasPermission, Requirements
from apps.core.schema import problems
from apps.dashboard import services
from apps.dashboard.serializers import KpisSerializer


class KpisView(APIView):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"GET": "dashboard.view"}

    @extend_schema(
        operation_id="dashboard_kpis",
        summary="Customer and device KPIs (cached for 30 s)",
        responses={200: KpisSerializer, **problems(401, 403)},
    )
    def get(self, request: Request) -> Response:
        return Response(KpisSerializer(services.kpis()).data)
