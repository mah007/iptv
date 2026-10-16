"""Admin API representations of import sources and jobs (ADR-0025).

Secrets are write-only: a source reports which ones are set (`secrets_set`), never
their values. Imported files show library-relative paths only.
"""

from typing import Any

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.imports import remotes, services
from apps.imports.models import (
    FileKind,
    ImportFile,
    ImportJob,
    ImportSource,
    JobOrigin,
    JobPhase,
    JobStatus,
    SourceKind,
    TestStatus,
)
from apps.library.models import Library, LibraryKind


class SourceOptionsSerializer(serializers.Serializer[Any]):
    """The non-secret options; each kind uses some (`remotes.OPTIONS`)."""

    provider = serializers.ChoiceField(choices=list(remotes.S3_PROVIDERS), required=False)
    endpoint = serializers.CharField(required=False, allow_blank=True, max_length=500)
    region = serializers.CharField(required=False, allow_blank=True, max_length=40)
    path_style = serializers.BooleanField(required=False)
    # DRF pops declared fields off the class, so the name may shadow Field.root.
    root = serializers.CharField(  # type: ignore[assignment]
        required=False, allow_blank=True, max_length=1000
    )
    host = serializers.CharField(required=False, allow_blank=True, max_length=253)
    port = serializers.IntegerField(required=False, min_value=1, max_value=65535)
    username = serializers.CharField(required=False, allow_blank=True, max_length=128)
    host_keys = serializers.CharField(required=False, allow_blank=True, max_length=8000)
    url = serializers.CharField(required=False, allow_blank=True, max_length=500)
    vendor = serializers.ChoiceField(choices=list(remotes.WEBDAV_VENDORS), required=False)
    root_folder_id = serializers.CharField(required=False, allow_blank=True, max_length=200)
    client_id = serializers.CharField(required=False, allow_blank=True, max_length=200)
    drive_id = serializers.CharField(required=False, allow_blank=True, max_length=200)
    drive_type = serializers.ChoiceField(
        choices=["", *remotes.ONEDRIVE_TYPES], required=False, allow_blank=True
    )


class SourceSecretsSerializer(serializers.Serializer[Any]):
    """Secrets, write-only; only the ones given change."""

    access_key_id = serializers.CharField(required=False, allow_blank=True, max_length=200)
    secret_access_key = serializers.CharField(required=False, allow_blank=True, max_length=500)
    password = serializers.CharField(required=False, allow_blank=True, max_length=500)
    private_key = serializers.CharField(required=False, allow_blank=True, max_length=20000)
    key_passphrase = serializers.CharField(required=False, allow_blank=True, max_length=500)
    token = serializers.CharField(required=False, allow_blank=True, max_length=20000)
    client_secret = serializers.CharField(required=False, allow_blank=True, max_length=500)


class ImportSourceSerializer(serializers.ModelSerializer[ImportSource]):
    options = serializers.SerializerMethodField()
    summary = serializers.SerializerMethodField()
    secrets_set = serializers.SerializerMethodField()
    last_test_status = serializers.ChoiceField(
        choices=["", *TestStatus.values], read_only=True, allow_blank=True
    )

    class Meta:
        model = ImportSource
        fields = (
            "id",
            "name",
            "kind",
            "options",
            "summary",
            "secrets_set",
            "last_test_at",
            "last_test_status",
            "last_test_message",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    @extend_schema_field(SourceOptionsSerializer)
    def get_options(self, source: ImportSource) -> dict[str, Any]:
        return {
            key: value
            for key, value in (source.options or {}).items()
            if key in remotes.ALL_OPTIONS
        }

    def get_summary(self, source: ImportSource) -> str:
        return remotes.summary(source.kind, source.options or {})

    @extend_schema_field(
        serializers.ListField(child=serializers.ChoiceField(choices=list(remotes.ALL_SECRETS)))
    )
    def get_secrets_set(self, source: ImportSource) -> list[str]:
        return sorted(services.source_secrets(source))


class ImportSourceWriteSerializer(serializers.Serializer[Any]):
    """Create a source (name, kind, options, secrets) or change one (kind stays)."""

    name = serializers.CharField(max_length=100, required=False)
    kind = serializers.ChoiceField(choices=SourceKind.choices, required=False)
    options = SourceOptionsSerializer(required=False)
    secrets = SourceSecretsSerializer(required=False)
    clear_secrets = serializers.ListField(
        child=serializers.ChoiceField(choices=list(remotes.ALL_SECRETS)),
        required=False,
        help_text="Secrets to remove (for example the password when switching to a key).",
    )


class RequestQueuedSerializer(serializers.Serializer[Any]):
    request_id = serializers.CharField()


class BrowseRequestSerializer(serializers.Serializer[Any]):
    path = serializers.CharField(required=False, allow_blank=True, max_length=1000, default="")


class RemoteEntrySerializer(serializers.Serializer[Any]):
    name = serializers.CharField()
    path = serializers.CharField(help_text="Relative to the source's root folder.")
    is_dir = serializers.BooleanField()
    size = serializers.IntegerField(allow_null=True)
    modified = serializers.CharField(allow_null=True)
    importable = serializers.BooleanField(help_text="A folder, a video file or a subtitle.")


class RemoteResultSerializer(serializers.Serializer[Any]):
    ok = serializers.BooleanField()
    code = serializers.CharField(help_text="ok, auth_failed, not_found, unreachable, timeout, ...")
    message = serializers.CharField(allow_blank=True)
    path = serializers.CharField(required=False, allow_blank=True)
    entries = RemoteEntrySerializer(many=True, required=False)
    truncated = serializers.BooleanField(required=False)
    count = serializers.IntegerField(required=False)
    host_keys = serializers.CharField(required=False, allow_blank=True)


REQUEST_STATUSES = ("pending", "done")


class RemoteAnswerSerializer(serializers.Serializer[Any]):
    status = serializers.ChoiceField(choices=list(REQUEST_STATUSES))
    result = RemoteResultSerializer(allow_null=True)


class NamedRefSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    name = serializers.CharField()


class LibraryTargetSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    name = serializers.CharField()
    kind = serializers.ChoiceField(choices=LibraryKind.choices)
    free_bytes = serializers.IntegerField(allow_null=True)
    total_bytes = serializers.IntegerField(allow_null=True)


class ImportStatusSerializer(serializers.Serializer[Any]):
    importer_online = serializers.BooleanField()
    max_concurrent = serializers.IntegerField()
    reserve_bytes = serializers.IntegerField()
    bandwidth_mbit = serializers.IntegerField()
    libraries = LibraryTargetSerializer(many=True)


class ItemSerializer(serializers.Serializer[Any]):
    path = serializers.CharField(max_length=1000)
    is_dir = serializers.BooleanField()


class SkippedSerializer(serializers.Serializer[Any]):
    count = serializers.IntegerField(default=0)
    names = serializers.ListField(child=serializers.CharField(), default=list)


class ImportJobSerializer(serializers.ModelSerializer[ImportJob]):
    source = serializers.SerializerMethodField()  # type: ignore[assignment]
    library = serializers.SerializerMethodField()
    items = ItemSerializer(many=True, read_only=True)
    url = serializers.CharField(source="url_display", read_only=True)
    has_checksum = serializers.SerializerMethodField()
    skipped = serializers.SerializerMethodField()
    origin = serializers.ChoiceField(choices=JobOrigin.choices, read_only=True)
    status = serializers.ChoiceField(choices=JobStatus.choices, read_only=True)
    phase = serializers.ChoiceField(
        choices=["", *JobPhase.values], read_only=True, allow_blank=True
    )
    error_params = serializers.DictField(read_only=True)

    class Meta:
        model = ImportJob
        fields = (
            "id",
            "origin",
            "source",
            "source_name",
            "source_kind",
            "items",
            "url",
            "file_name",
            "has_checksum",
            "library",
            "library_name",
            "subfolder",
            "rights_holder",
            "license_ref",
            "license_expires_at",
            "status",
            "phase",
            "error_code",
            "error",
            "error_params",
            "attempts",
            "retry_at",
            "cancel_requested",
            "files_total",
            "files_done",
            "bytes_total",
            "bytes_done",
            "speed_bps",
            "eta_s",
            "skipped",
            "created_by_name",
            "created_at",
            "started_at",
            "finished_at",
        )
        read_only_fields = fields

    @extend_schema_field(NamedRefSerializer(allow_null=True))
    def get_source(self, job: ImportJob) -> dict[str, Any] | None:
        return {"id": job.source_id, "name": job.source_name} if job.source_id else None

    @extend_schema_field(NamedRefSerializer(allow_null=True))
    def get_library(self, job: ImportJob) -> dict[str, Any] | None:
        return {"id": job.library_id, "name": job.library_name} if job.library_id else None

    def get_has_checksum(self, job: ImportJob) -> bool:
        return bool(job.checksum)

    @extend_schema_field(SkippedSerializer)
    def get_skipped(self, job: ImportJob) -> dict[str, Any]:
        data = job.skipped if isinstance(job.skipped, dict) else {}
        return {"count": int(data.get("count") or 0), "names": list(data.get("names") or [])}


class ImportFileSerializer(serializers.ModelSerializer[ImportFile]):
    relative_path = serializers.CharField(read_only=True)
    kind = serializers.ChoiceField(choices=FileKind.choices, read_only=True)

    class Meta:
        model = ImportFile
        fields = ("id", "relative_path", "remote_path", "kind", "size", "rights_applied_at")
        read_only_fields = fields


class ImportJobDetailSerializer(ImportJobSerializer):
    files = ImportFileSerializer(many=True, read_only=True)

    class Meta(ImportJobSerializer.Meta):
        fields = (*ImportJobSerializer.Meta.fields, "files")  # type: ignore[assignment]
        read_only_fields = fields  # type: ignore[assignment]


class _RightsSerializer(serializers.Serializer[Any]):
    library = serializers.PrimaryKeyRelatedField(queryset=Library.objects.all())
    subfolder = serializers.CharField(required=False, allow_blank=True, max_length=500, default="")
    rights_holder = serializers.CharField(max_length=255)
    license_ref = serializers.CharField(max_length=255)
    license_expires_at = serializers.DateTimeField(required=False, allow_null=True, default=None)


class SourceJobCreateSerializer(_RightsSerializer):
    source = serializers.PrimaryKeyRelatedField(  # type: ignore[assignment]
        queryset=ImportSource.objects.all()
    )
    items = ItemSerializer(many=True)


class LinkJobCreateSerializer(_RightsSerializer):
    url = serializers.CharField(max_length=4000)
    file_name = serializers.CharField(required=False, allow_blank=True, max_length=255, default="")
    checksum = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=128,
        default="",
        help_text="The file's SHA-256, SHA-1 or MD5 in hexadecimal, verified after the download.",
    )
