"""Query filters of the notification log (SPEC §8.3 Notifications)."""

from django_filters import rest_framework as filters

from apps.notifications.models import Channel, NotificationOutbox, OutboxStatus


class OutboxFilter(filters.FilterSet):
    status = filters.MultipleChoiceFilter(choices=OutboxStatus.choices)
    channel = filters.ChoiceFilter(choices=Channel.choices)
    template_key = filters.CharFilter(help_text="The event, e.g. payment_succeeded.")
    user = filters.UUIDFilter(field_name="user_id", help_text="Recipient id.")
    created_after = filters.IsoDateTimeFilter(field_name="created_at", lookup_expr="gte")
    created_before = filters.IsoDateTimeFilter(field_name="created_at", lookup_expr="lt")

    class Meta:
        model = NotificationOutbox
        fields = ("status", "channel", "template_key", "user", "created_after", "created_before")
