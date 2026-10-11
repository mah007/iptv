from typing import Any

from django.conf import settings
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.core.registry import SettingKind, SettingValue
from apps.core.services import SettingSource, SettingState

# A setting value is a JSON scalar; `null` when the setting is sensitive (never sent).
_SETTING_VALUE_SCHEMA = {"type": ["boolean", "number", "string", "null"]}


def media_url(key: str) -> str:
    """Public URL of a file under the media volume (`images/...`), served by the edge."""
    return f"{str(settings.MEDIA_BASE_URL).rstrip('/')}/{key.lstrip('/')}"


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
    is_default = serializers.BooleanField(
        read_only=True, help_text="Nothing is stored: the server config or the default applies."
    )
    # Declared fields leave the class namespace, so this doesn't shadow Field.source.
    source = serializers.ChoiceField(  # type: ignore[assignment]
        choices=[(source.value, source.name) for source in SettingSource],
        read_only=True,
        help_text="database: stored by an admin; environment: from the server config (.env); "
        "default: the built-in default.",
    )
    is_set = serializers.BooleanField(
        read_only=True, help_text="The effective value is not empty (secrets included)."
    )
    undecryptable = serializers.BooleanField(
        read_only=True,
        help_text="A stored secret FIELD_ENCRYPTION_KEY can't decrypt; it reads as empty.",
    )
    sensitive = serializers.BooleanField(source="definition.sensitive", read_only=True)
    env = serializers.ListField(
        source="definition.env",
        child=serializers.CharField(),
        read_only=True,
        help_text="The server-config variables used when nothing is stored (names only).",
    )
    widget = serializers.CharField(source="definition.widget", read_only=True, allow_null=True)
    image_url = serializers.SerializerMethodField(method_name="image_url_of")
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

    @extend_schema_field({"type": ["string", "null"]})
    def image_url_of(self, state: SettingState) -> str | None:
        if state.definition.widget != "image" or not state.value:
            return None
        return media_url(str(state.value))


@extend_schema_field({"type": ["boolean", "number", "string"]})
class SettingValueField(serializers.JSONField):
    """Any JSON scalar; the registry checks the type and constraints of the key."""


@extend_schema_field({"type": ["boolean", "number", "string", "null"]})
class NullableSettingValueField(serializers.JSONField):
    """A JSON scalar, or null to reset the setting."""


class SettingSerializer(serializers.Serializer[Any]):
    """PATCH body. Partial like any PATCH: without `value` nothing changes."""

    value = SettingValueField()


class SettingsBulkSerializer(serializers.Serializer[Any]):
    """Several settings at once, all or nothing: a value stores it, null resets it."""

    values = serializers.DictField(
        child=NullableSettingValueField(allow_null=True),
        allow_empty=False,
        help_text="Setting key to its new value; null forgets the stored value.",
    )


class SettingImageSerializer(serializers.Serializer[Any]):
    file = serializers.ImageField(help_text="PNG, JPEG or WebP, at most 2 MB.")
