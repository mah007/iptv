"""Admin API representations of playback sessions (SPEC §8.3.4 Live Sessions)."""

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.accounts.models import Device, User
from apps.playback.models import EndReason, PlaybackSession
from apps.playback.tokens import SESSION_LOG_PREFIX


class SessionUserSerializer(serializers.ModelSerializer[User]):
    class Meta:
        model = User
        fields = ("id", "username", "name")
        read_only_fields = fields


class SessionDeviceSerializer(serializers.ModelSerializer[Device]):
    class Meta:
        model = Device
        fields = ("id", "name", "kind", "app_hint")
        read_only_fields = fields


class SessionSerializer(serializers.ModelSerializer[PlaybackSession]):
    user = SessionUserSerializer(read_only=True)
    device = SessionDeviceSerializer(read_only=True, allow_null=True)
    end_reason = serializers.SerializerMethodField()
    log_ref = serializers.SerializerMethodField(
        help_text="The session id prefix the media edge's access log shows."
    )
    is_active = serializers.BooleanField(read_only=True, help_text="Not ended yet.")

    class Meta:
        model = PlaybackSession
        fields = (
            "id",
            "user",
            "device",
            "title_kind",
            "title_id",
            "title_name",
            "rendition",
            "ip",
            "country",
            "user_agent",
            "player",
            "started_at",
            "last_heartbeat_at",
            "ended_at",
            "bytes_sent",
            "end_reason",
            "is_active",
            "log_ref",
        )
        read_only_fields = fields

    @extend_schema_field(serializers.ChoiceField(choices=EndReason.choices, allow_null=True))
    def get_end_reason(self, obj: PlaybackSession) -> str | None:
        return obj.end_reason or None

    def get_log_ref(self, obj: PlaybackSession) -> str:
        return obj.session_key[:SESSION_LOG_PREFIX]
