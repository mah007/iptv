from rest_framework import serializers

from apps.catalog.models import Category


class CategoryBriefSerializer(serializers.ModelSerializer[Category]):
    class Meta:
        model = Category
        fields = ("id", "kind", "name_en", "name_ar")
        read_only_fields = fields


class CategorySerializer(serializers.ModelSerializer[Category]):
    class Meta:
        model = Category
        fields = (
            "id",
            "xc_id",
            "kind",
            "name_en",
            "name_ar",
            "slug",
            "sort",
            "is_adult",
            "visible_in_xtream",
            "icon",
            "parent",
        )
        read_only_fields = fields
