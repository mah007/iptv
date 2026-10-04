"""Serializers of the notifications admin API (SPEC §8.3 Notifications & Templates)."""

from typing import Any

from rest_framework import serializers

from apps.accounts.models import Locale, User
from apps.notifications.models import Channel, NotificationOutbox
from apps.notifications.rendering import MAX_SUBJECT_LENGTH, MAX_TEMPLATE_LENGTH


class RecipientSerializer(serializers.ModelSerializer[User]):
    class Meta:
        model = User
        fields = ("id", "username", "name", "email", "is_staff")
        read_only_fields = fields


class OutboxSerializer(serializers.ModelSerializer[NotificationOutbox]):
    user = RecipientSerializer(read_only=True, allow_null=True)

    class Meta:
        model = NotificationOutbox
        fields: tuple[str, ...] = (
            "id",
            "user",
            "channel",
            "template_key",
            "locale",
            "status",
            "attempts",
            "next_attempt_at",
            "to_address",
            "subject",
            "sent_at",
            "error",
            "created_at",
        )
        read_only_fields = fields


class OutboxDetailSerializer(OutboxSerializer):
    payload = serializers.JSONField(read_only=True)

    class Meta(OutboxSerializer.Meta):
        fields = (*OutboxSerializer.Meta.fields, "payload")
        read_only_fields = fields


class TemplateSerializer(serializers.Serializer[Any]):
    """The template in effect for one event, channel and language."""

    key = serializers.CharField()
    channel = serializers.ChoiceField(choices=Channel.choices)
    locale = serializers.ChoiceField(choices=Locale.choices)
    description = serializers.CharField()
    variables = serializers.ListField(child=serializers.CharField())
    subject = serializers.CharField()
    body_text = serializers.CharField()
    body_html = serializers.CharField(allow_blank=True)
    enabled = serializers.BooleanField()
    is_default = serializers.BooleanField()
    secret = serializers.BooleanField(
        help_text="Carries a password link: sent at once, never stored or resent."
    )
    updated_at = serializers.DateTimeField(allow_null=True)


class TemplateWriteSerializer(serializers.Serializer[Any]):
    subject = serializers.CharField(max_length=MAX_SUBJECT_LENGTH, required=False)
    body_text = serializers.CharField(max_length=MAX_TEMPLATE_LENGTH, required=False)
    body_html = serializers.CharField(
        max_length=MAX_TEMPLATE_LENGTH, required=False, allow_blank=True
    )
    enabled = serializers.BooleanField(required=False)


class PreviewRequestSerializer(serializers.Serializer[Any]):
    key = serializers.CharField(max_length=64)
    locale = serializers.ChoiceField(choices=Locale.choices)
    subject = serializers.CharField(max_length=MAX_SUBJECT_LENGTH)
    body_text = serializers.CharField(max_length=MAX_TEMPLATE_LENGTH)
    body_html = serializers.CharField(
        max_length=MAX_TEMPLATE_LENGTH, required=False, allow_blank=True, default=""
    )


class PreviewSerializer(serializers.Serializer[Any]):
    subject = serializers.CharField()
    text = serializers.CharField()
    html = serializers.CharField()
