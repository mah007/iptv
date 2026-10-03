"""Filters of the admin title lists and the review queue (SPEC §8.3)."""

from typing import Any

import django_filters
from django.db.models import Q, QuerySet

from apps.catalog.models import (
    MatchReview,
    MetadataSource,
    Movie,
    ReviewKind,
    ReviewStatus,
    Series,
    TitleStatus,
)


class TitleFilter(django_filters.FilterSet):
    status = django_filters.MultipleChoiceFilter(choices=TitleStatus.choices)
    category = django_filters.UUIDFilter(field_name="categories__id", distinct=True)
    year = django_filters.NumberFilter()
    year_min = django_filters.NumberFilter(field_name="year", lookup_expr="gte")
    year_max = django_filters.NumberFilter(field_name="year", lookup_expr="lte")
    metadata_source = django_filters.ChoiceFilter(choices=MetadataSource.choices)
    missing_arabic = django_filters.BooleanFilter(
        method="filter_missing_arabic", label="Arabic overview missing"
    )
    library = django_filters.UUIDFilter(method="filter_library", label="Library")

    def filter_missing_arabic(
        self, queryset: QuerySet[Any], name: str, value: bool
    ) -> QuerySet[Any]:
        missing = Q(overview_ar="")
        return queryset.filter(missing) if value else queryset.exclude(missing)

    def filter_library(self, queryset: QuerySet[Any], name: str, value: Any) -> QuerySet[Any]:
        model = queryset.model
        if model is Movie:
            return queryset.filter(files__library_id=value).distinct()
        return queryset.filter(seasons__episodes__files__library_id=value).distinct()


class MovieFilter(TitleFilter):
    class Meta:
        model = Movie
        fields = ("status", "category", "year", "metadata_source", "featured")


class SeriesFilter(TitleFilter):
    class Meta:
        model = Series
        fields = ("status", "category", "year", "metadata_source", "featured")


class ReviewFilter(django_filters.FilterSet):
    status = django_filters.ChoiceFilter(choices=ReviewStatus.choices)
    kind = django_filters.ChoiceFilter(choices=ReviewKind.choices)
    library = django_filters.UUIDFilter(field_name="media_file__library_id")
    reason = django_filters.CharFilter()

    class Meta:
        model = MatchReview
        fields = ("status", "kind", "library", "reason")
