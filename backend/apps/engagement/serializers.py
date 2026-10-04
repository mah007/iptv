"""Customer API representations of playback, progress and engagement (SPEC §3 web
playback, §10 Playback and Engagement)."""

from typing import Any

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.catalog.serializers_customer import (
    PLAYING_TYPE_CHOICES,
    THUMB_CHOICES,
    TITLE_TYPE_CHOICES,
    CategorySerializer,
    EpisodeSerializer,
    HeroSerializer,
    TitleCardSerializer,
)
from apps.engagement.services import WatchItem


class TitleRefSerializer(serializers.Serializer[Any]):
    title_type = serializers.ChoiceField(choices=TITLE_TYPE_CHOICES)
    title_id = serializers.UUIDField()


# --- Playback ---------------------------------------------------------------------------------


class PlaybackStartSerializer(TitleRefSerializer):
    episode_id = serializers.UUIDField(
        required=False,
        allow_null=True,
        help_text="For a series: the episode. Without it, the episode in progress or the "
        "next one (series_retrieve's next_episode).",
    )
    prefer = serializers.ChoiceField(
        choices=(("hls", "HLS"), ("mp4", "Progressive MP4")),
        required=False,
        allow_null=True,
        help_text="Segmented or progressive delivery when both exist; the best quality "
        "within the plan otherwise.",
    )


class AudioTrackSerializer(serializers.Serializer[Any]):
    language = serializers.CharField(help_text="ISO 639-2, or und.")
    codec = serializers.CharField(allow_blank=True)
    channels = serializers.IntegerField()
    default = serializers.BooleanField()


class SubtitleTrackSerializer(serializers.Serializer[Any]):
    language = serializers.CharField(help_text="ISO 639-2, or und.")
    name = serializers.CharField(allow_blank=True, help_text="The track title, if any.")
    forced = serializers.BooleanField()
    url = serializers.URLField(allow_null=True, help_text="WebVTT, signed like the stream.")


class PlaybackEpisodeSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    season_number = serializers.IntegerField()
    number = serializers.IntegerField()


class PlaybackGrantSerializer(serializers.Serializer[Any]):
    """A started stream: open `url` in the player and report progress to `session_id`."""

    session_id = serializers.UUIDField(help_text="For playback/{id}/progress and /stop.")
    title_type = serializers.ChoiceField(choices=PLAYING_TYPE_CHOICES)
    title_id = serializers.UUIDField(help_text="The movie or the episode playing.")
    episode = PlaybackEpisodeSerializer(allow_null=True)
    delivery = serializers.ChoiceField(
        choices=(("progressive", "Progressive"), ("segmented", "Segmented (HLS)"))
    )
    url = serializers.URLField(help_text="The signed edge URL of the stream.")
    hls_master_url = serializers.URLField(allow_null=True)
    compat_url = serializers.URLField(allow_null=True)
    height = serializers.IntegerField()
    expires_at = serializers.DateTimeField(help_text="When the signed URL stops working.")
    duration_ms = serializers.IntegerField()
    resume_ms = serializers.IntegerField(help_text="Where to resume; 0 starts at the top.")
    audio = AudioTrackSerializer(many=True)
    subtitles = SubtitleTrackSerializer(many=True)
    thumbnails_url = serializers.URLField(
        allow_null=True, help_text="Seek-bar previews (WebVTT sprite map), when available."
    )


class ProgressReportSerializer(serializers.Serializer[Any]):
    position_ms = serializers.IntegerField(min_value=0, max_value=100 * 3600 * 1000)
    duration_ms = serializers.IntegerField(
        required=False, min_value=0, max_value=100 * 3600 * 1000, allow_null=True
    )


class StopReportSerializer(serializers.Serializer[Any]):
    position_ms = serializers.IntegerField(
        required=False, min_value=0, max_value=100 * 3600 * 1000, allow_null=True
    )
    duration_ms = serializers.IntegerField(
        required=False, min_value=0, max_value=100 * 3600 * 1000, allow_null=True
    )


class ProgressSavedSerializer(serializers.Serializer[Any]):
    saved = serializers.BooleanField(help_text="False when throttled (too soon after the last).")
    position_ms = serializers.IntegerField(allow_null=True)
    completed = serializers.BooleanField(allow_null=True)


# --- Watching ---------------------------------------------------------------------------------


class WatchItemSerializer(serializers.Serializer[Any]):
    """A movie or an episode you started (or the next episode of a series you watch)."""

    id = serializers.SerializerMethodField(help_text="The history entry; null for an up-next.")
    type = serializers.SerializerMethodField()
    title = serializers.SerializerMethodField()
    episode = serializers.SerializerMethodField()
    position_ms = serializers.IntegerField()
    duration_ms = serializers.IntegerField()
    completed = serializers.BooleanField()
    updated_at = serializers.DateTimeField()

    @extend_schema_field(serializers.UUIDField(allow_null=True))
    def get_id(self, item: WatchItem) -> str | None:
        return str(item.progress.pk) if item.progress is not None else None

    @extend_schema_field(serializers.ChoiceField(choices=PLAYING_TYPE_CHOICES))
    def get_type(self, item: WatchItem) -> str:
        return "episode" if item.episode is not None else "movie"

    @extend_schema_field(TitleCardSerializer)
    def get_title(self, item: WatchItem) -> dict[str, Any]:
        return dict(TitleCardSerializer(item.title, context=self.context).data)

    @extend_schema_field(EpisodeSerializer(allow_null=True))
    def get_episode(self, item: WatchItem) -> dict[str, Any] | None:
        if item.episode is None:
            return None
        return dict(EpisodeSerializer(item.episode, context=self.context).data)


class RatingSerializer(TitleRefSerializer):
    value = serializers.ChoiceField(
        choices=THUMB_CHOICES,
        allow_null=True,
        help_text="null clears the rating.",
    )


class FavoriteAddedSerializer(TitleRefSerializer):
    added = serializers.BooleanField(help_text="False when it was already in the list.")


# --- Recommendations and home -----------------------------------------------------------------


class BecauseRowSerializer(serializers.Serializer[Any]):
    because_of = serializers.SerializerMethodField(help_text="The title you watched.")
    items = serializers.SerializerMethodField()

    @extend_schema_field(TitleCardSerializer)
    def get_because_of(self, row: Any) -> dict[str, Any]:
        return dict(TitleCardSerializer(row.source, context=self.context).data)

    @extend_schema_field(TitleCardSerializer(many=True))
    def get_items(self, row: Any) -> list[dict[str, Any]]:
        return list(TitleCardSerializer(row.items, many=True, context=self.context).data)


class RecommendationsSerializer(serializers.Serializer[Any]):
    because_you_watched = BecauseRowSerializer(many=True)
    top_picks = TitleCardSerializer(many=True)
    popular = TitleCardSerializer(many=True)


class HomeRowSerializer(serializers.Serializer[Any]):
    kind = serializers.ChoiceField(
        choices=(
            ("recently_added", "Recently added"),
            ("popular", "Popular this week"),
            ("because_you_watched", "Because you watched"),
            ("top_picks", "Top picks"),
            ("category", "Category"),
            ("collection", "Collection"),
        )
    )
    key = serializers.CharField(help_text="Stable per row, e.g. category:<slug>.")
    title = serializers.CharField(help_text="Display name in the request's language.")
    items = TitleCardSerializer(many=True)
    because_of = TitleCardSerializer(required=False, help_text="because_you_watched rows.")
    category = CategorySerializer(required=False, help_text="category rows: the category.")
    collection_slug = serializers.SlugField(required=False, help_text="collection rows.")


class HomeSerializer(serializers.Serializer[Any]):
    hero = HeroSerializer(many=True)
    continue_watching = WatchItemSerializer(many=True)
    rows = HomeRowSerializer(many=True)
