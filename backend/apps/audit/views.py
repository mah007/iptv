from django.db.models import QuerySet
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, generics
from rest_framework.permissions import IsAdminUser

from apps.audit.filters import AuditLogFilter
from apps.audit.models import AuditLog
from apps.audit.serializers import AuditLogSerializer


class AuditLogListView(generics.ListAPIView[AuditLog]):
    """GET /api/v1/admin/audit: newest first, filterable, paginated.

    Staff only until RBAC lands in M3, which switches it to `audit.view`.
    """

    permission_classes = (IsAdminUser,)
    serializer_class = AuditLogSerializer
    filter_backends = (DjangoFilterBackend, filters.OrderingFilter)
    filterset_class = AuditLogFilter
    ordering_fields = ("at",)
    ordering = ("-at", "-id")

    def get_queryset(self) -> QuerySet[AuditLog]:
        return AuditLog.objects.select_related("actor")
