"""Admin API representations of transcode jobs (SPEC §8.3.10 Transcode Jobs) and of a
title's media: renditions, tracks, images (SPEC §8.3 Title detail; ADR-0014)."""

from typing import Any

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.catalog.serializers import ImageSerializer
from apps.media.models import (
    PRIORITY_MAX,
    PRIORITY_MIN,
    AudioTrack,
    Rendition,
    SubtitleTrack,
    TranscodeJob,
    TranscodeProfile,
)


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


# --- Title media (SPEC §8.3 Title detail; ADR-0014) --------------------------------------


class TitleRefSerializer(serializers.Serializer[Any]):
    kind = serializers.ChoiceField(choices=("movie", "series", "episode"))
    id = serializers.UUIDField()
    name = serializers.CharField()


class RenditionSerializer(serializers.ModelSerializer[Rendition]):
    disk_bytes = serializers.SerializerMethodField(
        help_text="Bytes on the media volume (0 for a link to the source file)."
    )

    class Meta:
        model = Rendition
        fields = (
            "id",
            "kind",
            "name",
            "status",
            "width",
            "height",
            "bitrate",
            "codec",
            "container",
            "size",
            "disk_bytes",
            "encoder_used",
            "duration_s",
            "error",
            "details",
            "ready_at",
            "created_at",
        )
        read_only_fields = fields

    @extend_schema_field(serializers.IntegerField())
    def get_disk_bytes(self, rendition: Rendition) -> int:
        return 0 if (rendition.details or {}).get("linked") else rendition.size


class ActiveJobSerializer(serializers.ModelSerializer[TranscodeJob]):
    class Meta:
        model = TranscodeJob
        fields = ("id", "profile", "status", "progress", "backend", "priority", "eta_s")
        read_only_fields = fields


class MediaAudioTrackSerializer(serializers.ModelSerializer[AudioTrack]):
    class Meta:
        model = AudioTrack
        fields = (
            "id",
            "stream_index",
            "language",
            "codec",
            "channels",
            "title",
            "default",
            "forced",
            "commentary",
        )
        read_only_fields = fields


class MediaSubtitleTrackSerializer(serializers.ModelSerializer[SubtitleTrack]):
    origin = serializers.SerializerMethodField(help_text="embedded, sidecar or upload.")
    relative_path = serializers.CharField(
        source="sidecar_path", help_text="A sidecar's path inside its library."
    )
    text = serializers.BooleanField(source="is_text", help_text="False for image subtitles.")

    class Meta:
        model = SubtitleTrack
        fields = (
            "id",
            "origin",
            "stream_index",
            "relative_path",
            "upload_name",
            "codec",
            "format",
            "language",
            "title",
            "default",
            "forced",
            "hearing_impaired",
            "encoding",
            "status",
            "error",
            "cues",
            "text",
        )
        read_only_fields = fields

    @extend_schema_field(serializers.ChoiceField(choices=("embedded", "sidecar", "upload")))
    def get_origin(self, track: SubtitleTrack) -> str:
        if not track.external:
            return "embedded"
        return "sidecar" if track.sidecar_path else "upload"


class TitleFileSerializer(serializers.Serializer[Any]):
    """A file of the title with its outputs, tracks and running jobs."""

    id = serializers.UUIDField()
    relative_path = serializers.CharField(help_text="The path inside its library.")
    library = serializers.CharField(source="library.name")
    size = serializers.IntegerField()
    duration_ms = serializers.IntegerField(allow_null=True)
    video_codec = serializers.CharField()
    width = serializers.IntegerField(allow_null=True)
    height = serializers.IntegerField(allow_null=True)
    hdr = serializers.CharField()
    is_primary = serializers.BooleanField()
    disk_bytes = serializers.SerializerMethodField(help_text="Bytes its renditions occupy.")
    renditions = RenditionSerializer(many=True)
    audio = MediaAudioTrackSerializer(many=True, source="audio_tracks")
    subtitles = MediaSubtitleTrackSerializer(many=True, source="subtitle_tracks")
    jobs = ActiveJobSerializer(many=True, source="active_jobs")

    @extend_schema_field(serializers.IntegerField())
    def get_disk_bytes(self, file: Any) -> int:
        return sum(0 if (r.details or {}).get("linked") else r.size for r in file.renditions.all())


class TitleMediaSerializer(serializers.Serializer[Any]):
    title = TitleRefSerializer()
    files = TitleFileSerializer(many=True)


class ReprocessSerializer(serializers.Serializer[Any]):
    outputs = serializers.ListField(
        child=serializers.ChoiceField(choices=TranscodeProfile.choices),
        required=False,
        help_text="Outputs to make again; all of them when omitted.",
    )
    media_file = serializers.UUIDField(
        required=False, help_text="One file of the title; all of them when omitted."
    )


class ReprocessResultSerializer(serializers.Serializer[Any]):
    files = serializers.ListField(child=serializers.UUIDField())
    outputs = serializers.ListField(child=serializers.CharField())


class TrackUpdateSerializer(serializers.Serializer[Any]):
    language = serializers.CharField(required=False, max_length=16)
    title = serializers.CharField(required=False, allow_blank=True, max_length=200)
    default = serializers.BooleanField(required=False)
    forced = serializers.BooleanField(required=False)


class TrackSerializer(serializers.Serializer[Any]):
    """An audio or subtitle track after an edit."""

    kind = serializers.ChoiceField(choices=("audio", "subtitle"))
    audio = MediaAudioTrackSerializer(allow_null=True)
    subtitle = MediaSubtitleTrackSerializer(allow_null=True)


class SubtitleUploadSerializer(serializers.Serializer[Any]):
    file = serializers.FileField(help_text="An .srt, .ass, .ssa or .vtt file, any encoding.")
    language = serializers.CharField(max_length=16, help_text="ISO 639: ar, ara, en, eng...")
    title = serializers.CharField(required=False, allow_blank=True, max_length=200, default="")
    default = serializers.BooleanField(required=False, default=False)
    forced = serializers.BooleanField(required=False, default=False)
    media_file = serializers.UUIDField(
        required=False, help_text="Required when the title has several files."
    )


class ImageAlternativeSerializer(serializers.Serializer[Any]):
    kind = serializers.ChoiceField(choices=("poster", "backdrop", "logo"))
    path = serializers.CharField(help_text="The TMDB image path to send back to pick it.")
    language = serializers.CharField()
    width = serializers.IntegerField()
    height = serializers.IntegerField()
    preview_url = serializers.URLField(help_text="A small preview on TMDB's image CDN.")


class TitleImagesSerializer(serializers.Serializer[Any]):
    title = TitleRefSerializer()
    images = ImageSerializer(many=True)
    alternatives = ImageAlternativeSerializer(many=True)
    alternatives_error = serializers.CharField(
        allow_null=True, help_text="provider_unavailable when TMDB could not be asked."
    )


class ImageAddSerializer(serializers.Serializer[Any]):
    kind = serializers.ChoiceField(choices=("poster", "backdrop", "logo", "still"))
    tmdb_path = serializers.CharField(required=False, max_length=200)
    file = serializers.ImageField(required=False, help_text="An uploaded image (15 MB at most).")
    primary = serializers.BooleanField(required=False, default=True)


class MediaQueuedSerializer(serializers.Serializer[Any]):
    queued = serializers.BooleanField()


class RematchSerializer(serializers.Serializer[Any]):
    tmdb_id = serializers.IntegerField(
        required=False, min_value=1, help_text="Omit to match the files again automatically."
    )
    kind = serializers.ChoiceField(choices=("movie", "tv"), required=False)


class RematchResultSerializer(serializers.Serializer[Any]):
    files = serializers.IntegerField()
    automatic = serializers.BooleanField()
    title_ids = serializers.ListField(child=serializers.UUIDField())


class CleanupRequestSerializer(serializers.Serializer[Any]):
    dry_run = serializers.BooleanField(default=True)


class CleanupRemovalSerializer(serializers.Serializer[Any]):
    path = serializers.CharField(help_text="`<asset key>/<entry>`, never a library path.")
    reason = serializers.ChoiceField(choices=("orphaned", "removed_file", "superseded", "leftover"))
    bytes = serializers.IntegerField()


class CleanupReportSerializer(serializers.Serializer[Any]):
    dry_run = serializers.BooleanField()
    finished_at = serializers.DateTimeField()
    bytes = serializers.IntegerField()
    entries = serializers.IntegerField()
    removals = CleanupRemovalSerializer(many=True)
