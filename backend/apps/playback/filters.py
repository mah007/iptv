"""Query filters of the admin session list (SPEC §8.3.4)."""

from django_filters import rest_framework as filters

from apps.playback.models import EndReason, PlaybackSession, TitleKind


# django-filter ships no type hints, so its base class is Any to mypy.
class SessionFilter(filters.FilterSet):  # type: ignore[misc,no-any-unimported]
    active = filters.BooleanFilter(
        field_name="ended_at",
        lookup_expr="isnull",
        help_text="true: sessions still open; false: ended ones.",
    )
    user = filters.UUIDFilter(field_name="user_id", help_text="Sessions of one customer.")
    device = filters.UUIDFilter(field_name="device_id", help_text="Sessions of one device.")
    title_kind = filters.ChoiceFilter(choices=TitleKind.choices)
    title_id = filters.UUIDFilter(help_text="Sessions of one movie or episode.")
    end_reason = filters.ChoiceFilter(choices=EndReason.choices)
    started_after = filters.IsoDateTimeFilter(
        field_name="started_at", lookup_expr="gte", help_text="Started at or after (ISO 8601)."
    )
    started_before = filters.IsoDateTimeFilter(
        field_name="started_at", lookup_expr="lt", help_text="Started before (ISO 8601)."
    )
    ordering = filters.OrderingFilter(
        fields=(
            ("started_at", "started_at"),
            ("last_heartbeat_at", "last_heartbeat_at"),
            ("ended_at", "ended_at"),
            ("bytes_sent", "bytes_sent"),
        ),
        help_text="Sort key; prefix with - for descending. Default: -started_at.",
    )

    class Meta:
        model = PlaybackSession
        fields = (
            "active",
            "user",
            "device",
            "title_kind",
            "title_id",
            "end_reason",
            "started_after",
            "started_before",
        )
