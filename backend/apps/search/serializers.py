"""Customer search representations (SPEC §9 Search, §10 search)."""

from typing import Any

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.catalog.serializers_customer import (
    EpisodeSerializer,
    PersonBriefSerializer,
    TitleCardSerializer,
)
from apps.search.services import Hit

RESULT_TYPE_CHOICES = (("movie", "Movie"), ("series", "Series"), ("episode", "Episode"))


class SearchQuerySerializer(serializers.Serializer[Any]):
    q = serializers.CharField(max_length=200, help_text="Arabic or English; typos are fine.")
    type = serializers.ChoiceField(choices=RESULT_TYPE_CHOICES, required=False)
    genre = serializers.UUIDField(required=False)
    year = serializers.IntegerField(required=False, min_value=1870, max_value=2200)
    page = serializers.IntegerField(required=False, min_value=1, max_value=50, default=1)
    page_size = serializers.IntegerField(required=False, min_value=1, max_value=50, default=20)


class SearchHitSerializer(serializers.Serializer[Any]):
    type = serializers.ChoiceField(choices=RESULT_TYPE_CHOICES)
    title = serializers.SerializerMethodField(help_text="The movie or series (an episode's).")
    episode = serializers.SerializerMethodField()

    @extend_schema_field(TitleCardSerializer)
    def get_title(self, hit: Hit) -> dict[str, Any]:
        return dict(TitleCardSerializer(hit.title, context=self.context).data)

    @extend_schema_field(EpisodeSerializer(allow_null=True))
    def get_episode(self, hit: Hit) -> dict[str, Any] | None:
        if hit.episode is None:
            return None
        return dict(EpisodeSerializer(hit.episode, context=self.context).data)


class SearchResultSerializer(serializers.Serializer[Any]):
    query = serializers.CharField()
    engine = serializers.ChoiceField(
        choices=(("meilisearch", "Meilisearch"), ("database", "PostgreSQL fallback")),
        help_text="database while Meilisearch is unavailable (no episodes, fewer typos).",
    )
    page = serializers.IntegerField()
    page_size = serializers.IntegerField()
    total = serializers.IntegerField()
    results = SearchHitSerializer(many=True, source="hits")
    people = PersonBriefSerializer(many=True, help_text="Matching people (first page only).")
