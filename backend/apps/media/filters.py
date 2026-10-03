"""Query filters of the admin transcode job list (SPEC §8.3.10)."""

from django_filters import rest_framework as filters

from apps.media.models import JobStatus, TranscodeBackend, TranscodeJob


# django-filter ships no type hints, so its base class is Any to mypy.
class TranscodeJobFilter(filters.FilterSet):  # type: ignore[misc,no-any-unimported]
    status = filters.MultipleChoiceFilter(choices=JobStatus.choices)
    backend = filters.ChoiceFilter(choices=TranscodeBackend.choices)
    file = filters.UUIDFilter(field_name="media_file_id", help_text="Jobs of one media file.")
    ordering = filters.OrderingFilter(
        fields=(
            ("created_at", "created_at"),
            ("priority", "priority"),
            ("progress", "progress"),
            ("started_at", "started_at"),
            ("finished_at", "finished_at"),
        ),
        help_text="Sort key; prefix with - for descending. Default: -created_at.",
    )

    class Meta:
        model = TranscodeJob
        fields = ("status", "backend", "file")
