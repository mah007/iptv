"""Admin API representations of libraries and scans (SPEC §8.3 Libraries & scans)."""

from typing import Any, ClassVar

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.catalog.models import Category
from apps.catalog.serializers import CategoryBriefSerializer, LibraryBriefSerializer
from apps.library.models import SCAN_INTERVAL_MAX_MIN, Library, ScanJob


class ScanJobSerializer(serializers.ModelSerializer[ScanJob]):
    library = LibraryBriefSerializer(read_only=True)

    class Meta:
        model = ScanJob
        fields = (
            "id",
            "library",
            "trigger",
            "status",
            "path",
            "found",
            "new",
            "changed",
            "moved",
            "removed",
            "errors",
            "started_at",
            "finished_at",
            "log",
            "created_at",
        )
        read_only_fields = fields


STAT_NAMES = ("files", "bytes", "movies", "series", "episodes", "review", "errors", "pending")


class LibraryStatsSerializer(serializers.Serializer[Any]):
    """Totals after the last scan (zeros before the first)."""

    files = serializers.IntegerField(default=0)
    bytes = serializers.IntegerField(default=0)
    movies = serializers.IntegerField(default=0)
    series = serializers.IntegerField(default=0)
    episodes = serializers.IntegerField(default=0)
    review = serializers.IntegerField(default=0)
    errors = serializers.IntegerField(default=0)  # type: ignore[assignment]
    pending = serializers.IntegerField(default=0)


class LibrarySerializer(serializers.ModelSerializer[Library]):
    default_categories = CategoryBriefSerializer(many=True, read_only=True)
    stats = serializers.SerializerMethodField()
    last_scan = serializers.SerializerMethodField()

    class Meta:
        model = Library
        fields = (
            "id",
            "name",
            "kind",
            "path",
            "processing_policy",
            "default_categories",
            "scan_interval_min",
            "enabled",
            "last_scan_at",
            "stats",
            "last_scan",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    @extend_schema_field(LibraryStatsSerializer)
    def get_stats(self, library: Library) -> dict[str, int]:
        stats = library.stats if isinstance(library.stats, dict) else {}
        return {name: int(stats.get(name) or 0) for name in STAT_NAMES}

    @extend_schema_field(ScanJobSerializer(allow_null=True))
    def get_last_scan(self, library: Library) -> dict[str, Any] | None:
        job = getattr(library, "last_scan", None)
        return ScanJobSerializer(job).data if job is not None else None


class LibraryWriteSerializer(serializers.ModelSerializer[Library]):
    """Create or change a library. `path` is a folder under the media root, given as an
    absolute container path (/media/movies) or relative to the root (movies)."""

    default_categories = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.all(), many=True, required=False
    )
    scan_interval_min = serializers.IntegerField(
        min_value=1, max_value=SCAN_INTERVAL_MAX_MIN, required=False
    )

    class Meta:
        model = Library
        fields = (
            "name",
            "kind",
            "path",
            "processing_policy",
            "default_categories",
            "scan_interval_min",
            "enabled",
        )
        # Uniqueness and path checks are the service's (problem codes, overlap rules).
        extra_kwargs: ClassVar[dict[str, dict[str, Any]]] = {
            "name": {"validators": []},
            "path": {"validators": []},
        }


class ScanStartedSerializer(serializers.Serializer[Any]):
    job = ScanJobSerializer()
    created = serializers.BooleanField(
        help_text="False when a scan of the library was already queued or running."
    )
