from typing import ClassVar

from django.db.models import QuerySet
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, generics

from apps.accounts.permissions import HasPermission, Requirements
from apps.audit.filters import AuditLogFilter
from apps.audit.models import AuditLog
from apps.audit.serializers import AuditLogSerializer


class AuditLogListView(generics.ListAPIView[AuditLog]):
    """GET /api/v1/admin/audit: newest first, filterable, paginated."""

    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"GET": "audit.view"}
    serializer_class = AuditLogSerializer
    filter_backends = (DjangoFilterBackend, filters.OrderingFilter)
    filterset_class = AuditLogFilter
    ordering_fields = ("at",)
    ordering = ("-at", "-id")

    def get_queryset(self) -> QuerySet[AuditLog]:
        return AuditLog.objects.select_related("actor")
