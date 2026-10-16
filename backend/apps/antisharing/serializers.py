"""Bodies of the anti-sharing and GeoIP admin endpoints (ADR-0021)."""

from typing import Any

from rest_framework import serializers

from apps.antisharing.models import ExcessBytes, FlagStatus, RiskFlag, Sighting


class SignalSerializer(serializers.Serializer[Any]):
    name = serializers.CharField()
    value = serializers.IntegerField()
    limit = serializers.IntegerField()
    points = serializers.IntegerField()


class TravelSerializer(serializers.Serializer[Any]):
    from_country = serializers.CharField(source="from")
    to_country = serializers.CharField(source="to")
    km = serializers.IntegerField()
    hours = serializers.FloatField()
    at = serializers.DateTimeField()


class AssessmentSerializer(serializers.Serializer[Any]):
    score = serializers.IntegerField()
    flagged = serializers.BooleanField()
    signals = SignalSerializer(many=True)
    travel = TravelSerializer(many=True)


class FlagActionSerializer(serializers.Serializer[Any]):
    action = serializers.CharField()
    at = serializers.DateTimeField()
    by = serializers.CharField(allow_blank=True)


class RiskFlagSerializer(serializers.ModelSerializer[RiskFlag]):
    status = serializers.ChoiceField(choices=FlagStatus.choices)
    assessment = serializers.SerializerMethodField()
    actions = FlagActionSerializer(many=True)
    reviewed_by_name = serializers.SerializerMethodField()

    class Meta:
        model = RiskFlag
        fields = (
            "id",
            "status",
            "score",
            "assessment",
            "opened_at",
            "last_scored_at",
            "actions",
            "reviewed_at",
            "reviewed_by_name",
            "note",
        )
        read_only_fields = fields

    def get_assessment(self, flag: RiskFlag) -> dict[str, Any] | None:
        signals = flag.signals or {}
        if not signals:
            return None
        return dict(AssessmentSerializer(signals).data)

    def get_reviewed_by_name(self, flag: RiskFlag) -> str:
        return flag.reviewed_by.get_full_name() if flag.reviewed_by is not None else ""


class SightingSerializer(serializers.ModelSerializer[Sighting]):
    device_name = serializers.SerializerMethodField()
    app = serializers.SerializerMethodField()

    class Meta:
        model = Sighting
        fields = (
            "id",
            "network",
            "asn",
            "asn_org",
            "country",
            "source",
            "device",
            "device_name",
            "app",
            "first_seen",
            "last_seen",
            "hits",
        )
        read_only_fields = fields

    def get_device_name(self, sighting: Sighting) -> str:
        return sighting.device.name if sighting.device is not None else ""

    def get_app(self, sighting: Sighting) -> str:
        return sighting.device.app_hint if sighting.device is not None else ""


class ExcessBytesSerializer(serializers.ModelSerializer[ExcessBytes]):
    """A session served far more than its title explains (G-17, ADR-0024)."""

    device_name = serializers.SerializerMethodField()
    started_at = serializers.DateTimeField(source="session.started_at", read_only=True)
    ended_at = serializers.DateTimeField(source="session.ended_at", read_only=True, allow_null=True)

    class Meta:
        model = ExcessBytes
        fields = (
            "id",
            "session",
            "title_name",
            "rendition",
            "network",
            "device",
            "device_name",
            "bytes_sent",
            "expected_bytes",
            "ratio",
            "detected_at",
            "started_at",
            "ended_at",
        )
        read_only_fields = fields

    def get_device_name(self, row: ExcessBytes) -> str:
        return row.device.name if row.device is not None else ""


class RiskOverviewSerializer(serializers.Serializer[Any]):
    enabled = serializers.BooleanField()
    geoip_installed = serializers.BooleanField()
    flag = RiskFlagSerializer(allow_null=True)
    assessment = AssessmentSerializer()
    sightings = SightingSerializer(many=True)
    excess_bytes = ExcessBytesSerializer(many=True)
    attribution_url = serializers.URLField()


class ReviewSerializer(serializers.Serializer[Any]):
    note = serializers.CharField(max_length=2000, required=False, allow_blank=True, default="")


class ResetResultSerializer(serializers.Serializer[Any]):
    logins = serializers.IntegerField()


class GeoDatabaseSerializer(serializers.Serializer[Any]):
    installed = serializers.BooleanField()
    built_at = serializers.DateTimeField(allow_null=True)
    updated_at = serializers.DateTimeField(allow_null=True)
    size = serializers.IntegerField()


class GeoDatabasesSerializer(serializers.Serializer[Any]):
    country = GeoDatabaseSerializer()
    asn = GeoDatabaseSerializer()


class GeoIpStatusSerializer(serializers.Serializer[Any]):
    enabled = serializers.BooleanField()
    auto_update = serializers.BooleanField()
    databases = GeoDatabasesSerializer()
    attribution = serializers.URLField()


class GeoLookupRequestSerializer(serializers.Serializer[Any]):
    ip = serializers.IPAddressField(
        required=False, help_text="The address to look up; yours when left out."
    )


class GeoLookupSerializer(serializers.Serializer[Any]):
    ip = serializers.CharField()
    country = serializers.CharField(allow_blank=True)
    asn = serializers.IntegerField(allow_null=True)
    asn_org = serializers.CharField(allow_blank=True)
    local = serializers.BooleanField()


class GeoIpQueuedSerializer(serializers.Serializer[Any]):
    queued = serializers.BooleanField()
