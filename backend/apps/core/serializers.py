from typing import Any

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.core.registry import SettingKind, SettingValue
from apps.core.services import SettingState

# A setting value is a JSON scalar; `null` when the setting is sensitive (never sent).
_SETTING_VALUE_SCHEMA = {"type": ["boolean", "number", "string", "null"]}


class SettingEntrySerializer(serializers.Serializer[SettingState]):
    """A registered setting with its effective value. Sensitive values are never sent."""

    key = serializers.CharField(source="definition.key", read_only=True)
    group = serializers.CharField(source="definition.group", read_only=True)
    # (value, name) pairs, as ENUM_NAME_OVERRIDES derives them, keep the enum named SettingKind.
    kind = serializers.ChoiceField(
        source="definition.kind",
        choices=[(kind.value, kind.name) for kind in SettingKind],
        read_only=True,
    )
    description = serializers.CharField(source="definition.description", read_only=True)
    # Explicit method names: get_default/get_value would shadow DRF's own Field methods.
    default = serializers.SerializerMethodField(method_name="default_of")
    value = serializers.SerializerMethodField(method_name="value_of")
    is_default = serializers.BooleanField(read_only=True)
    sensitive = serializers.BooleanField(source="definition.sensitive", read_only=True)
    min_value = serializers.FloatField(
        source="definition.min_value", read_only=True, allow_null=True
    )
    max_value = serializers.FloatField(
        source="definition.max_value", read_only=True, allow_null=True
    )
    choices = serializers.ListField(
        source="definition.choices", child=serializers.CharField(), read_only=True, allow_null=True
    )
    updated_at = serializers.DateTimeField(read_only=True, allow_null=True)
    updated_by = serializers.CharField(read_only=True, allow_null=True)

    @extend_schema_field(_SETTING_VALUE_SCHEMA)
    def default_of(self, state: SettingState) -> SettingValue | None:
        return None if state.definition.sensitive else state.definition.default

    @extend_schema_field(_SETTING_VALUE_SCHEMA)
    def value_of(self, state: SettingState) -> SettingValue | None:
        return None if state.definition.sensitive else state.value


@extend_schema_field({"type": ["boolean", "number", "string"]})
class SettingValueField(serializers.JSONField):
    """Any JSON scalar; the registry checks the type and constraints of the key."""


class SettingSerializer(serializers.Serializer[Any]):
    """PATCH body. Partial like any PATCH: without `value` nothing changes."""

    value = SettingValueField()
