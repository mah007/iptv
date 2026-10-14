"""Admin API shapes of the Edges page (ADR-0022). Secrets are never part of an edge's
representation: a standalone edge's secret appears once, in the answer that created or
rotated it."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from django.conf import settings
from rest_framework import serializers

from apps.accounts.models import User
from apps.edges import services
from apps.edges.models import EDGE_ID_PATTERN, Audience, Edge, EdgeKind, EdgeStorage
from apps.edges.selection import EdgeView

_EDGE_ID = re.compile(EDGE_ID_PATTERN)


def _base_url(value: str) -> str:
    value = value.strip().rstrip("/")
    parts = urlsplit(value)
    allowed = ("https", "http") if settings.DEBUG else ("https",)
    if parts.scheme not in allowed or not parts.hostname:
        msg = "Enter the edge's https address, e.g. https://media2.example.com."
        raise serializers.ValidationError(msg)
    if parts.path or parts.query or parts.fragment or parts.username or parts.password:
        msg = "Enter only the address: scheme, host and optional port."
        raise serializers.ValidationError(msg)
    return value


class EdgeSerializer(serializers.ModelSerializer[Edge]):
    status = serializers.SerializerMethodField()
    effective_url = serializers.SerializerMethodField()
    canary_user_ids = serializers.SerializerMethodField()
    origin_edge_id = serializers.SerializerMethodField()
    connections = serializers.SerializerMethodField()
    egress_mbps = serializers.SerializerMethodField()
    kids = serializers.SerializerMethodField()
    version = serializers.SerializerMethodField()
    has_secret = serializers.SerializerMethodField()
    #: The change added this edge's origin to the apps' CSP (it applies after a restart).
    csp_changed = serializers.SerializerMethodField()

    class Meta:
        model = Edge
        fields = (
            "id",
            "edge_id",
            "name",
            "kind",
            "storage",
            "base_url",
            "effective_url",
            "enabled",
            "draining",
            "weight",
            "capacity_mbps",
            "audience",
            "canary_user_ids",
            "origin_edge_id",
            "status",
            "last_seen_at",
            "connections",
            "egress_mbps",
            "kids",
            "version",
            "has_secret",
            "secret_set_at",
            "created_at",
            "csp_changed",
        )
        read_only_fields = fields

    def get_status(self, edge: Edge) -> str:
        return services.status_of(edge, signing_kid=self.context.get("signing_kid"))

    def get_effective_url(self, edge: Edge) -> str:
        return services.base_url_of(EdgeView(edge.edge_id, edge.kind, edge.storage, edge.base_url))

    def get_canary_user_ids(self, edge: Edge) -> list[str]:
        return [str(user.pk) for user in edge.canary_users.all()]

    def get_origin_edge_id(self, edge: Edge) -> str | None:
        return str(edge.origin_edge_id) if edge.origin_edge_id else None

    def get_connections(self, edge: Edge) -> int | None:
        active = ((edge.report or {}).get("connections") or {}).get("active")
        return active if isinstance(active, int) else None

    def get_egress_mbps(self, edge: Edge) -> float | None:
        egress = (edge.report or {}).get("egress_bps")
        return round(float(egress) / 1e6, 2) if isinstance(egress, int | float) else None

    def get_kids(self, edge: Edge) -> list[str]:
        return [str(kid) for kid in (edge.report or {}).get("kids") or []]

    def get_version(self, edge: Edge) -> str:
        return str((edge.report or {}).get("version") or "")

    def get_has_secret(self, edge: Edge) -> bool:
        return bool(edge.secret_hash)

    def get_csp_changed(self, edge: Edge) -> bool:
        return bool(self.context.get("csp_changed", False))


class EdgeWriteSerializer(serializers.Serializer[dict[str, Any]]):
    """Create (every field) or change (any subset) an edge."""

    edge_id = serializers.CharField(max_length=64, required=False)
    name = serializers.CharField(max_length=100, required=False)
    kind = serializers.ChoiceField(
        choices=[EdgeKind.STANDALONE.value, EdgeKind.CDN.value], required=False
    )
    storage = serializers.ChoiceField(choices=EdgeStorage.choices, required=False)
    base_url = serializers.CharField(max_length=255, required=False)
    enabled = serializers.BooleanField(required=False)
    draining = serializers.BooleanField(required=False)
    weight = serializers.IntegerField(min_value=0, max_value=1000, required=False)
    capacity_mbps = serializers.IntegerField(min_value=1, max_value=1_000_000, required=False)
    audience = serializers.ChoiceField(choices=Audience.choices, required=False)
    canary_user_ids = serializers.ListField(
        child=serializers.UUIDField(), max_length=500, required=False
    )
    origin_edge_id = serializers.UUIDField(required=False, allow_null=True)

    def validate_edge_id(self, value: str) -> str:
        if not _EDGE_ID.fullmatch(value):
            msg = "1-64 letters, digits, dots, dashes or underscores, e.g. media2."
            raise serializers.ValidationError(msg)
        return value

    def validate_base_url(self, value: str) -> str:
        return _base_url(value)

    def validate_canary_user_ids(self, value: list[Any]) -> list[Any]:
        found = User.objects.filter(pk__in=value).count()
        if found != len(set(value)):
            msg = "Some of these customers don't exist."
            raise serializers.ValidationError(msg)
        return value


class EdgeCreatedSerializer(EdgeSerializer):
    """A new standalone edge, with its secret (shown once)."""

    secret = serializers.SerializerMethodField()

    class Meta(EdgeSerializer.Meta):
        fields = (*EdgeSerializer.Meta.fields, "secret")  # type: ignore[assignment]
        read_only_fields = fields  # type: ignore[assignment]

    def get_secret(self, edge: Edge) -> str | None:
        return self.context.get("secret")


class EdgeSecretSerializer(serializers.Serializer[dict[str, Any]]):
    secret = serializers.CharField()


class ProbeStepSerializer(serializers.Serializer[dict[str, Any]]):
    name = serializers.CharField()
    ok = serializers.BooleanField()
    code = serializers.CharField()
    detail = serializers.CharField(allow_blank=True)


class ProbeResultSerializer(serializers.Serializer[dict[str, Any]]):
    ok = serializers.BooleanField()
    steps = ProbeStepSerializer(many=True)


class EdgeListSerializer(serializers.Serializer[dict[str, Any]]):
    """The list, plus what the page shows beside it."""

    edges = EdgeSerializer(many=True)
    signing_kid = serializers.CharField(allow_blank=True)
    s3_enabled = serializers.BooleanField()
    cdn_ready = serializers.BooleanField()
    edges_host = serializers.CharField()
    control_url = serializers.CharField()
