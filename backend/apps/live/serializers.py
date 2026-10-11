"""Admin API serializers of live TV (ADR-0017).

Source and guide URLs are write-only: reads show `{scheme, host, port}` and nothing
else, because URLs carry credentials. Integration secrets are write-only too.
"""

from datetime import UTC, datetime
from typing import Any, ClassVar

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.catalog.models import Category, CategoryKind
from apps.live import egress, sources
from apps.live.models import (
    ChannelOutput,
    ChannelTranscode,
    EpgChannel,
    EpgProgram,
    EpgSource,
    IntegrationKind,
    LiveChannel,
    LiveIntegration,
)
from apps.live.xtream import logo_url


class SourceInfoSerializer(serializers.Serializer[Any]):
    scheme = serializers.CharField()
    host = serializers.CharField()
    port = serializers.IntegerField(allow_null=True)


class ChannelGroupSerializer(serializers.ModelSerializer[Category]):
    class Meta:
        model = Category
        fields = ("id", "xc_id", "name_en", "name_ar", "sort", "is_adult", "visible_in_xtream")
        read_only_fields = fields


class ChannelStatusSerializer(serializers.Serializer[Any]):
    """What the packager reports (redis-state), plus the open sessions."""

    state = serializers.ChoiceField(
        choices=("disabled", "idle", "starting", "live", "failed", "unlicensed")
    )
    since = serializers.DateTimeField(allow_null=True)
    error = serializers.CharField(allow_blank=True)
    detail = serializers.CharField(allow_blank=True)
    bitrate_kbps = serializers.IntegerField()
    viewers = serializers.IntegerField()
    recording = serializers.BooleanField()
    archive_bytes = serializers.IntegerField()
    archive_from = serializers.DateTimeField(allow_null=True)


class ProbeSerializer(serializers.Serializer[Any]):
    ok = serializers.BooleanField()
    error = serializers.CharField(allow_blank=True, required=False)
    detail = serializers.CharField(allow_blank=True, required=False)
    video = serializers.DictField(required=False)
    audio = serializers.DictField(required=False)
    height = serializers.IntegerField(required=False)
    bitrate_kbps = serializers.IntegerField(required=False)
    copy_ok = serializers.BooleanField(required=False)
    at = serializers.CharField(required=False)


def _epoch(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=UTC)
    except ValueError:
        return None


class LiveChannelSerializer(serializers.ModelSerializer[LiveChannel]):
    group = ChannelGroupSerializer(read_only=True)
    source_info = serializers.SerializerMethodField()
    logo_url = serializers.SerializerMethodField()
    license_valid = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    probe = serializers.SerializerMethodField()

    class Meta:
        model = LiveChannel
        fields = (
            "id",
            "xc_id",
            "name",
            "name_ar",
            "group",
            "sort",
            "epg_channel_id",
            "epg_source",
            "logo_url",
            "source_info",
            "output",
            "transcode",
            "catchup_days",
            "always_on",
            "enabled",
            "rights_holder",
            "license_ref",
            "license_expires_at",
            "license_valid",
            "origin",
            "origin_ref",
            "integration",
            "probe",
            "status",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    @extend_schema_field(SourceInfoSerializer(allow_null=True))
    def get_source_info(self, channel: LiveChannel) -> dict[str, object] | None:
        info = sources.describe_encrypted(channel.source_encrypted)
        return info.as_dict() if info else None

    @extend_schema_field(serializers.CharField())
    def get_logo_url(self, channel: LiveChannel) -> str:
        return logo_url(channel.logo)

    @extend_schema_field(serializers.BooleanField())
    def get_license_valid(self, channel: LiveChannel) -> bool:
        return channel.license_valid()

    @extend_schema_field(ProbeSerializer(allow_null=True))
    def get_probe(self, channel: LiveChannel) -> dict[str, Any] | None:
        return channel.probe or None

    @extend_schema_field(ChannelStatusSerializer)
    def get_status(self, channel: LiveChannel) -> dict[str, Any]:
        statuses: dict[str, dict[str, str]] = self.context.get("statuses", {})
        viewers: dict[Any, int] = self.context.get("viewers", {})
        archives: dict[str, Any] = self.context.get("archives", {})
        raw = statuses.get(channel.storage_key, {})
        if not channel.enabled:
            state = "disabled"
        elif not channel.license_valid():
            state = "unlicensed"
        else:
            state = raw.get("state", "idle")
            if state not in ("starting", "live", "failed"):
                state = "idle"
        window = archives.get(channel.storage_key)
        try:
            bitrate = int(float(raw.get("bitrate_kbps", "0") or 0))
            archive_bytes = int(raw.get("archive_bytes", "0") or 0)
        except ValueError:
            bitrate, archive_bytes = 0, 0
        return {
            "state": state,
            "since": _epoch(raw.get("since")),
            "error": raw.get("error", ""),
            "detail": raw.get("detail", ""),
            "bitrate_kbps": bitrate,
            "viewers": viewers.get(channel.pk, 0),
            "recording": bool(channel.catchup_days) and state == "live",
            "archive_bytes": archive_bytes,
            "archive_from": window.first if window else None,
        }


class LiveChannelWriteSerializer(serializers.ModelSerializer[LiveChannel]):
    group = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.filter(kind=CategoryKind.LIVE)
    )
    epg_source = serializers.PrimaryKeyRelatedField(
        queryset=EpgSource.objects.all(), allow_null=True, required=False
    )
    source_url = serializers.CharField(write_only=True, required=False, max_length=2048)
    catchup_days = serializers.IntegerField(min_value=0, max_value=365, required=False)
    output = serializers.ChoiceField(choices=ChannelOutput.choices, required=False)
    transcode = serializers.ChoiceField(choices=ChannelTranscode.choices, required=False)

    class Meta:
        model = LiveChannel
        fields = (
            "name",
            "name_ar",
            "group",
            "sort",
            "epg_channel_id",
            "epg_source",
            "source_url",
            "output",
            "transcode",
            "catchup_days",
            "always_on",
            "enabled",
            "rights_holder",
            "license_ref",
            "license_expires_at",
        )
        extra_kwargs: ClassVar[dict[str, dict[str, Any]]] = {
            "name_ar": {"required": False},
            "epg_channel_id": {"required": False},
            "rights_holder": {"required": False},
            "license_ref": {"required": False},
            "license_expires_at": {"required": False},
        }

    def validate_source_url(self, value: str) -> str:
        try:
            return sources.validate_source_url(value)
        except Exception as exc:
            raise serializers.ValidationError(
                "Enter an http(s), rtmp(s), rtsp(s) or srt URL.", code="invalid_source_url"
            ) from exc

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if self.instance is None and not attrs.get("source_url"):
            raise serializers.ValidationError(
                {"source_url": serializers.ErrorDetail("This field is required.", code="required")}
            )
        return attrs


class BulkEnableSerializer(serializers.Serializer[Any]):
    ids = serializers.ListField(child=serializers.UUIDField(), min_length=1, max_length=500)
    enabled = serializers.BooleanField()


class ChannelReorderSerializer(serializers.Serializer[Any]):
    group = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.filter(kind=CategoryKind.LIVE)
    )
    ids = serializers.ListField(child=serializers.UUIDField(), max_length=2000)


class LogoSerializer(serializers.Serializer[Any]):
    file = serializers.ImageField(required=False)
    url = serializers.URLField(required=False, max_length=1024)


class SourceTestRequestSerializer(serializers.Serializer[Any]):
    url = serializers.CharField(max_length=2048)

    def validate_url(self, value: str) -> str:
        try:
            return sources.validate_source_url(value)
        except Exception as exc:
            raise serializers.ValidationError(
                "Enter an http(s), rtmp(s), rtsp(s) or srt URL.", code="invalid_source_url"
            ) from exc


class SourceTestQueuedSerializer(serializers.Serializer[Any]):
    request_id = serializers.CharField()


class SourceTestResultSerializer(serializers.Serializer[Any]):
    status = serializers.ChoiceField(choices=("pending", "done"))
    result = ProbeSerializer(allow_null=True)


class ProgrammeSerializer(serializers.ModelSerializer[EpgProgram]):
    class Meta:
        model = EpgProgram
        fields = (
            "id",
            "start",
            "stop",
            "title",
            "title_ar",
            "description",
            "description_ar",
            "category",
            "lang",
        )
        read_only_fields = fields


class EpgSourceSerializer(serializers.ModelSerializer[EpgSource]):
    url = serializers.SerializerMethodField()
    channel_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = EpgSource
        fields = (
            "id",
            "name",
            "kind",
            "url",
            "upload_name",
            "refresh_cron",
            "priority",
            "enabled",
            "epg_id",
            "integration",
            "last_run_at",
            "last_ok_at",
            "last_error",
            "stats",
            "channel_count",
            "created_at",
        )
        read_only_fields = fields

    @extend_schema_field(SourceInfoSerializer(allow_null=True))
    def get_url(self, source: EpgSource) -> dict[str, object] | None:
        info = sources.describe_encrypted(source.url_encrypted)
        return info.as_dict() if info else None


class EpgSourceWriteSerializer(serializers.Serializer[Any]):
    """JSON or multipart: `url` for a feed, or `file` for an uploaded XMLTV."""

    name = serializers.CharField(max_length=100, required=False)
    url = serializers.CharField(max_length=2048, required=False, write_only=True)
    file = serializers.FileField(required=False, write_only=True)
    refresh_cron = serializers.CharField(max_length=100, required=False)
    priority = serializers.IntegerField(min_value=0, max_value=10_000, required=False)
    enabled = serializers.BooleanField(required=False)


class EpgChannelSerializer(serializers.ModelSerializer[EpgChannel]):
    name = serializers.SerializerMethodField()
    source_name = serializers.CharField(source="source.name", read_only=True)

    class Meta:
        model = EpgChannel
        fields = ("id", "source", "source_name", "xmltv_id", "name", "names", "icon_url")
        read_only_fields = fields

    @extend_schema_field(serializers.CharField())
    def get_name(self, channel: EpgChannel) -> str:
        return channel.display_name()


class UnmatchedChannelSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    name = serializers.CharField()
    epg_channel_id = serializers.CharField(allow_blank=True)
    reason = serializers.ChoiceField(choices=("no_id", "not_in_guide"))
    suggestions = EpgChannelSerializer(many=True)


class UnusedGuideChannelSerializer(serializers.Serializer[Any]):
    xmltv_id = serializers.CharField()
    name = serializers.CharField()
    source_id = serializers.UUIDField()
    source_name = serializers.CharField()


class UnmatchedSerializer(serializers.Serializer[Any]):
    channels = UnmatchedChannelSerializer(many=True)
    guide_channels = UnusedGuideChannelSerializer(many=True)


class LiveIntegrationSerializer(serializers.ModelSerializer[LiveIntegration]):
    has_credentials = serializers.SerializerMethodField()
    stream_base = serializers.SerializerMethodField()
    channel_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = LiveIntegration
        fields = (
            "id",
            "kind",
            "name",
            "base_url",
            "stream_base",
            "group",
            "rights_holder",
            "license_ref",
            "has_credentials",
            "last_sync_at",
            "last_error",
            "last_result",
            "channel_count",
            "created_at",
        )
        read_only_fields = fields

    @extend_schema_field(serializers.BooleanField())
    def get_has_credentials(self, integration: LiveIntegration) -> bool:
        return bool(integration.credentials_encrypted)

    @extend_schema_field(SourceInfoSerializer(allow_null=True))
    def get_stream_base(self, integration: LiveIntegration) -> dict[str, object] | None:
        if not integration.stream_base_url:
            return None
        return sources.describe(integration.stream_base_url).as_dict()


class LiveIntegrationWriteSerializer(serializers.Serializer[Any]):
    kind = serializers.ChoiceField(choices=IntegrationKind.choices, required=False)
    name = serializers.CharField(max_length=100, required=False)
    base_url = serializers.CharField(max_length=500, required=False)
    stream_base_url = serializers.CharField(max_length=500, required=False, allow_blank=True)
    group = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.filter(kind=CategoryKind.LIVE), allow_null=True, required=False
    )
    rights_holder = serializers.CharField(max_length=255, required=False)
    license_ref = serializers.CharField(max_length=255, required=False, allow_blank=True)
    username = serializers.CharField(
        max_length=200, required=False, allow_blank=True, write_only=True
    )
    password = serializers.CharField(
        max_length=500, required=False, allow_blank=True, write_only=True
    )
    access_token = serializers.CharField(
        max_length=2000, required=False, allow_blank=True, write_only=True
    )

    def _url(self, value: str, *, http: bool) -> str:
        try:
            url = sources.validate_http_url(value) if http else sources.validate_source_url(value)
        except Exception as exc:
            raise serializers.ValidationError("Enter a valid URL.", code="invalid_url") from exc
        if "@" in url.split("//", 1)[-1].split("/", 1)[0]:
            raise serializers.ValidationError(
                "Put credentials in their own fields, not in the URL.", code="credentials_in_url"
            )
        try:
            egress.check_url_static(url)
        except egress.UnsafeDestination as refused:
            raise serializers.ValidationError(
                "This address is never fetched from.", code="unsafe_destination"
            ) from refused
        return url

    def validate_base_url(self, value: str) -> str:
        return self._url(value, http=True)

    def validate_stream_base_url(self, value: str) -> str:
        return self._url(value, http=False) if value else ""


class SyncResultSerializer(serializers.Serializer[Any]):
    queued = serializers.BooleanField()


class LiveOverviewSerializer(serializers.Serializer[Any]):
    packager_running = serializers.BooleanField()
    running_channels = serializers.IntegerField()
    channels = serializers.IntegerField()
    enabled_channels = serializers.IntegerField()
    catchup_channels = serializers.IntegerField()
    archive_bytes = serializers.IntegerField()
    archive_budget_bytes = serializers.IntegerField()
    viewers = serializers.IntegerField()
