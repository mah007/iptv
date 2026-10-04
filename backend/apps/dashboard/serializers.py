"""Admin aggregates: KPIs, charts, the activity feed, system health and storage."""

from typing import Any

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.audit.models import AuditLog
from apps.catalog.serializers import ImageSerializer
from apps.dashboard.activity import Subject
from apps.dashboard.health import Status


class KpisSerializer(serializers.Serializer[Any]):
    customers_total = serializers.IntegerField(help_text="Every customer account.")
    customers_active = serializers.IntegerField(
        help_text="Active accounts whose access has not ended."
    )
    customers_expired = serializers.IntegerField(help_text="Active accounts whose access ended.")
    customers_suspended = serializers.IntegerField(help_text="Suspended accounts.")
    expiring_7d = serializers.IntegerField(help_text="Active access ending within 7 days.")
    devices_total = serializers.IntegerField(help_text="Devices that are not revoked.")
    devices_blocked = serializers.IntegerField(help_text="Blocked devices (not revoked).")
    streams_now = serializers.IntegerField(help_text="Playback sessions open now.")
    stream_users_now = serializers.IntegerField(help_text="Customers with a session open now.")
    reviews_open = serializers.IntegerField(help_text="Open items in the review queue.")
    transcode_queued = serializers.IntegerField(help_text="Transcode jobs waiting for a worker.")
    transcode_running = serializers.IntegerField(help_text="Transcode jobs running now.")
    transcode_failed_24h = serializers.IntegerField(
        help_text="Transcode jobs that failed in the last 24 hours."
    )
    as_of = serializers.DateTimeField(help_text="When the figures were computed (cached 30 s).")


# --- Charts ------------------------------------------------------------------------------


class StreamPointSerializer(serializers.Serializer[Any]):
    at = serializers.DateTimeField(help_text="Start of the bucket.")
    streams = serializers.IntegerField(help_text="Sessions that played during the bucket.")


class DayPointSerializer(serializers.Serializer[Any]):
    date = serializers.DateField(help_text="A calendar day in the admin's time zone.")
    signups = serializers.IntegerField(help_text="New customer accounts.")
    churned = serializers.IntegerField(help_text="Customer access periods that ended.")
    plays = serializers.IntegerField(help_text="Playback sessions started.")
    watch_hours = serializers.FloatField(help_text="Hours played by the sessions started.")


class CategoryPlaysSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    kind = serializers.CharField(help_text="vod, series or live.")
    name_en = serializers.CharField()
    name_ar = serializers.CharField()
    plays = serializers.IntegerField()


class TopTitleSerializer(serializers.Serializer[Any]):
    kind = serializers.ChoiceField(choices=[("movie", "Movie"), ("series", "Series")])
    id = serializers.UUIDField()
    title = serializers.CharField()
    title_ar = serializers.CharField()
    year = serializers.IntegerField(allow_null=True)
    plays = serializers.IntegerField(
        help_text="Sessions in 30 days (a series counts its episodes)."
    )
    watch_hours = serializers.FloatField()
    poster = serializers.SerializerMethodField(help_text="The poster, as the titles list has it.")

    @extend_schema_field(ImageSerializer(allow_null=True))
    def get_poster(self, title: Any) -> dict[str, Any] | None:
        # Serialized when the series were computed (they are cached as plain data).
        poster: dict[str, Any] | None = title.poster
        return poster


class TimeseriesSerializer(serializers.Serializer[Any]):
    as_of = serializers.DateTimeField()
    time_zone = serializers.CharField(help_text="The admin's time zone; days are its days.")
    bucket_minutes = serializers.IntegerField(help_text="Width of a stream bucket.")
    streams = StreamPointSerializer(many=True, help_text="The last 24 hours, oldest first.")
    streams_peak = serializers.IntegerField(help_text="The highest bucket of the 24 hours.")
    streams_peak_at = serializers.DateTimeField(allow_null=True)
    days = DayPointSerializer(many=True, help_text="The last 30 days, oldest first.")
    categories = CategoryPlaysSerializer(many=True, help_text="Top 8 categories by plays, 30 d.")
    top_titles = TopTitleSerializer(many=True, help_text="Top 10 titles by plays, 30 days.")


# --- Activity ----------------------------------------------------------------------------


class ActivityActorSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    name = serializers.CharField(help_text="The admin's name, or their username.")


class ActivityCustomerSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    name = serializers.CharField()


class ActivitySerializer(serializers.Serializer[AuditLog]):
    id = serializers.UUIDField()
    at = serializers.DateTimeField()
    action = serializers.CharField(help_text="e.g. customer.create, session.kill.")
    actor = serializers.SerializerMethodField(help_text="Null for the system.")
    target_type = serializers.CharField(help_text="`app_label.model`, e.g. accounts.device.")
    target_id = serializers.CharField()
    target_label = serializers.SerializerMethodField(
        help_text="A readable name for the target; empty when it no longer exists."
    )
    customer = serializers.SerializerMethodField(
        help_text="The customer the target belongs to, if any."
    )
    before = serializers.SerializerMethodField(
        help_text="The redacted snapshot before the change, for admins with audit.view."
    )
    after = serializers.SerializerMethodField(
        help_text="The redacted snapshot after the change, for admins with audit.view."
    )

    def _subject(self, entry: AuditLog) -> Subject | None:
        subjects: dict[tuple[str, str], Subject] = self.context.get("subjects", {})
        return subjects.get((entry.target_type, entry.target_id))

    @extend_schema_field(ActivityActorSerializer(allow_null=True))
    def get_actor(self, entry: AuditLog) -> dict[str, str] | None:
        if entry.actor is None:
            return None
        return {"id": str(entry.actor.pk), "name": entry.actor.name or entry.actor.username}

    def get_target_label(self, entry: AuditLog) -> str:
        subject = self._subject(entry)
        return subject.label if subject is not None else ""

    @extend_schema_field(ActivityCustomerSerializer(allow_null=True))
    def get_customer(self, entry: AuditLog) -> dict[str, str] | None:
        subject = self._subject(entry)
        if subject is None or subject.customer_id is None:
            return None
        return {"id": subject.customer_id, "name": subject.customer_name}

    @extend_schema_field(serializers.JSONField(allow_null=True))
    def get_before(self, entry: AuditLog) -> Any:
        return entry.before if self.context.get("with_changes") else None

    @extend_schema_field(serializers.JSONField(allow_null=True))
    def get_after(self, entry: AuditLog) -> Any:
        return entry.after if self.context.get("with_changes") else None


class ActivityQuerySerializer(serializers.Serializer[Any]):
    customer = serializers.UUIDField(
        required=False, help_text="Only entries about this customer (needs customers.view)."
    )


# --- Health ------------------------------------------------------------------------------

# The labels match ENUM_NAME_OVERRIDES["HealthStatus"] (an Enum: value, name).
_STATUS_CHOICES = [(status.value, status.name) for status in Status]


class ServiceHealthSerializer(serializers.Serializer[Any]):
    name = serializers.CharField(
        help_text="postgres, redis_state, redis_cache, meilisearch, workers, watcher, "
        "transcoders, or edge:<host>."
    )
    status = serializers.ChoiceField(choices=_STATUS_CHOICES)
    latency_ms = serializers.FloatField(allow_null=True)
    age_s = serializers.IntegerField(
        allow_null=True, help_text="Seconds since the last heartbeat, for background services."
    )
    error = serializers.CharField(help_text="An exception class name or a short reason.")


class QueueDepthSerializer(serializers.Serializer[Any]):
    name = serializers.CharField()
    depth = serializers.IntegerField(allow_null=True, help_text="Null when the broker is down.")


class TranscoderSerializer(serializers.Serializer[Any]):
    host = serializers.CharField()
    best = serializers.CharField(help_text="The backend jobs prefer here: nvenc, qsv, vaapi, cpu.")
    queues = serializers.ListField(child=serializers.CharField())
    backends = serializers.DictField(
        child=serializers.ListField(child=serializers.CharField()),
        help_text="Backend -> codecs it encodes on this host.",
    )
    gpus = serializers.ListField(child=serializers.CharField())
    ffmpeg = serializers.CharField(help_text="FFmpeg version.")
    checked_at = serializers.DateTimeField(allow_null=True)


class RedisHealthSerializer(serializers.Serializer[Any]):
    name = serializers.CharField(help_text="redis_state or redis_cache.")
    status = serializers.ChoiceField(choices=_STATUS_CHOICES)
    used_memory = serializers.IntegerField(allow_null=True, help_text="Bytes.")
    max_memory = serializers.IntegerField(allow_null=True, help_text="Bytes; null: no limit.")
    policy = serializers.CharField(help_text="maxmemory-policy.")
    evicted_keys = serializers.IntegerField(
        allow_null=True, help_text="Must stay 0 on redis_state."
    )
    error = serializers.CharField()


class DatabaseHealthSerializer(serializers.Serializer[Any]):
    status = serializers.ChoiceField(choices=_STATUS_CHOICES)
    connections = serializers.IntegerField(allow_null=True)
    max_connections = serializers.IntegerField(allow_null=True)
    error = serializers.CharField()


class HealthSerializer(serializers.Serializer[Any]):
    as_of = serializers.DateTimeField()
    status = serializers.ChoiceField(choices=_STATUS_CHOICES, help_text="The worst of the parts.")
    services = ServiceHealthSerializer(many=True)
    queues = QueueDepthSerializer(many=True)
    transcoders = TranscoderSerializer(many=True)
    redis = RedisHealthSerializer(many=True)
    database = DatabaseHealthSerializer()
    grafana_url = serializers.CharField(help_text="Base URL for Grafana links; empty: none.")


# --- Storage -----------------------------------------------------------------------------


class LibraryUsageSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    name = serializers.CharField()
    kind = serializers.CharField(help_text="movies, series or mixed.")
    files = serializers.IntegerField(help_text="Source files present in the library.")
    sources = serializers.IntegerField(help_text="Bytes of those files.")
    renditions = serializers.IntegerField(
        help_text="Bytes of their renditions on disk (links to a source count nothing)."
    )


class GrowthPointSerializer(serializers.Serializer[Any]):
    date = serializers.DateField(help_text="A calendar day in the admin's time zone.")
    sources = serializers.IntegerField(help_text="Source bytes at the end of the day.")
    renditions = serializers.IntegerField(help_text="Rendition bytes at the end of the day.")


class TitleUsageSerializer(serializers.Serializer[Any]):
    kind = serializers.ChoiceField(choices=[("movie", "Movie"), ("series", "Series")])
    id = serializers.UUIDField()
    title = serializers.CharField()
    title_ar = serializers.CharField()
    year = serializers.IntegerField(allow_null=True)
    files = serializers.IntegerField(help_text="Source files present (a series: every episode).")
    sources = serializers.IntegerField()
    renditions = serializers.IntegerField()


class StorageUsageSerializer(serializers.Serializer[Any]):
    as_of = serializers.DateTimeField(help_text="When the figures were computed (cached 30 s).")
    time_zone = serializers.CharField(help_text="The admin's time zone; days are its days.")
    files = serializers.IntegerField(help_text="Source files present in every library.")
    sources = serializers.IntegerField(help_text="Bytes of the source files.")
    renditions = serializers.IntegerField(help_text="Bytes of the renditions on disk.")
    libraries = LibraryUsageSerializer(many=True, help_text="Largest first.")
    growth = GrowthPointSerializer(many=True, help_text="The last 90 days, oldest first.")
    largest = TitleUsageSerializer(many=True, help_text="The 10 titles that take the most space.")


# --- Watch history -----------------------------------------------------------------------


class WatchedTitleSerializer(serializers.Serializer[Any]):
    kind = serializers.ChoiceField(choices=[("movie", "Movie"), ("series", "Series")])
    id = serializers.UUIDField(help_text="The movie, or the series of the episode.")
    title = serializers.CharField()
    title_ar = serializers.CharField()
    season = serializers.IntegerField(allow_null=True, help_text="Episodes only.")
    episode = serializers.IntegerField(allow_null=True, help_text="Episodes only.")
    episode_title = serializers.CharField(help_text="Episodes only; may be empty.")


class WatchHistorySerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    title = serializers.SerializerMethodField()
    position_ms = serializers.IntegerField()
    duration_ms = serializers.IntegerField()
    completed = serializers.BooleanField()
    updated_at = serializers.DateTimeField(help_text="When the customer last watched it.")

    @extend_schema_field(WatchedTitleSerializer)
    def get_title(self, row: Any) -> dict[str, Any]:
        if row.episode is not None:
            show = row.episode.season.series
            return {
                "kind": "series",
                "id": str(show.pk),
                "title": show.title,
                "title_ar": show.title_ar,
                "season": row.episode.season.number,
                "episode": row.episode.number,
                "episode_title": row.episode.title,
            }
        return {
            "kind": "movie",
            "id": str(row.movie.pk),
            "title": row.movie.title,
            "title_ar": row.movie.title_ar,
            "season": None,
            "episode": None,
            "episode_title": "",
        }
