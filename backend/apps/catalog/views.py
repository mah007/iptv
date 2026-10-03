from typing import ClassVar

from django.db.models import QuerySet
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import filters, generics

from apps.accounts.permissions import HasPermission, Requirements
from apps.catalog.models import Category
from apps.catalog.serializers import CategorySerializer


@extend_schema_view(
    get=extend_schema(operation_id="categories_list", summary="List categories, by kind and order")
)
class CategoryListView(generics.ListAPIView[Category]):
    """GET /api/v1/admin/categories: for access-profile editors now, the catalogue in M6."""

    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"GET": ("customers.view", "library.review")}
    serializer_class = CategorySerializer
    filter_backends = (DjangoFilterBackend, filters.SearchFilter)
    filterset_fields = ("kind",)
    search_fields = ("name_en", "name_ar", "slug")

    def get_queryset(self) -> QuerySet[Category]:
        return Category.objects.order_by("kind", "sort", "name_en")
