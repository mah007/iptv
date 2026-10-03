import django_filters

from apps.audit.models import AuditLog


class AuditLogFilter(django_filters.FilterSet):
    """Filters for the audit list: actor, action, target and an `at` range."""

    actor = django_filters.UUIDFilter(field_name="actor_id")
    action = django_filters.CharFilter(field_name="action")
    target_type = django_filters.CharFilter(field_name="target_type")
    target_id = django_filters.CharFilter(field_name="target_id")
    # ?at_after=...&at_before=... (ISO 8601, inclusive)
    at = django_filters.IsoDateTimeFromToRangeFilter(field_name="at")

    class Meta:
        model = AuditLog
        fields = ("actor", "action", "target_type", "target_id", "at")
