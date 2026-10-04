"""Admin API representations of transcode jobs (SPEC §8.3.10 Transcode Jobs)."""

from typing import Any

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.media.models import PRIORITY_MAX, PRIORITY_MIN, TranscodeJob


class JobTitleSerializer(serializers.Serializer[Any]):
    kind = serializers.ChoiceField(choices=("movie", "episode"))
    id = serializers.UUIDField()
    name = serializers.CharField()


class JobFileSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    library = serializers.CharField(help_text="The library's name.")
    relative_path = serializers.CharField(help_text="The path inside its library.")
    duration_ms = serializers.IntegerField(allow_null=True)


class TranscodeJobSerializer(serializers.ModelSerializer[TranscodeJob]):
    file = serializers.SerializerMethodField()
    title = serializers.SerializerMethodField(help_text="The movie or episode, if linked.")

    class Meta:
        model = TranscodeJob
        fields = (
            "id",
            "file",
            "title",
            "profile",
            "remux",
            "backend",
            "encoder",
            "priority",
            "status",
            "progress",
            "fps",
            "speed",
            "eta_s",
            "worker_host",
            "attempts",
            "error",
            "error_tail",
            "created_at",
            "started_at",
            "finished_at",
        )
        read_only_fields = fields

    @extend_schema_field(JobFileSerializer)
    def get_file(self, job: TranscodeJob) -> dict[str, Any]:
        file = job.media_file
        return {
            "id": file.pk,
            "library": file.library.name,
            "relative_path": file.relative_path,
            "duration_ms": file.duration_ms,
        }

    @extend_schema_field(JobTitleSerializer(allow_null=True))
    def get_title(self, job: TranscodeJob) -> dict[str, Any] | None:
        file = job.media_file
        if file.movie is not None:
            return {"kind": "movie", "id": file.movie.pk, "name": file.movie.title}
        episodes = list(file.episodes.all())  # prefetched with season__series
        if not episodes:
            return None
        first = episodes[0]
        series = first.season.series
        name = f"{series.title} S{first.season.number:02d}E{first.number:02d}"
        return {"kind": "episode", "id": first.pk, "name": name}


class PrioritySerializer(serializers.Serializer[Any]):
    priority = serializers.IntegerField(
        min_value=PRIORITY_MIN, max_value=PRIORITY_MAX, help_text="0-9, higher runs first."
    )
