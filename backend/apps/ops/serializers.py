"""Admin API shapes of the Backups page (ADR-0020)."""

from typing import Any

from rest_framework import serializers

from apps.ops.models import BackupKind, BackupRun, RunStatus, RunTrigger


class BackupRunSerializer(serializers.ModelSerializer[BackupRun]):
    requested_by = serializers.SerializerMethodField()

    class Meta:
        model = BackupRun
        fields = (
            "id",
            "kind",
            "status",
            "trigger",
            "requested_by",
            "created_at",
            "started_at",
            "finished_at",
            "duration_s",
            "size_bytes",
            "detail",
            "error",
        )
        read_only_fields = fields

    def get_requested_by(self, run: BackupRun) -> str | None:
        return run.requested_by.username if run.requested_by else None


class DbBackupSerializer(serializers.Serializer[Any]):
    """A base backup in the local pgBackRest repository, as the agent last reported."""

    backup_label = serializers.CharField(source="label", help_text="pgBackRest's label.")
    type = serializers.CharField()
    start = serializers.IntegerField(allow_null=True, help_text="Unix time.")
    stop = serializers.IntegerField(allow_null=True, help_text="Unix time.")
    size = serializers.IntegerField(allow_null=True, help_text="Database size, bytes.")
    repo_size = serializers.IntegerField(allow_null=True, help_text="Stored, bytes.")
    error = serializers.BooleanField()


class SnapshotsSerializer(serializers.Serializer[Any]):
    ok = serializers.BooleanField()
    snapshots = serializers.IntegerField()
    latest = serializers.CharField(allow_null=True)


class BackupAgentSerializer(serializers.Serializer[Any]):
    seen_at = serializers.DateTimeField(allow_null=True)
    healthy = serializers.BooleanField(help_text="The backup service asked for work recently.")
    archiving = serializers.BooleanField(help_text="WAL archiving is on (point-in-time recovery).")
    passphrase = serializers.BooleanField(help_text="The backup passphrase file is in place.")
    disk_total_bytes = serializers.IntegerField(allow_null=True)
    disk_free_bytes = serializers.IntegerField(allow_null=True)
    db_backups = DbBackupSerializer(many=True)
    local_snapshots = SnapshotsSerializer(allow_null=True)
    offsite_snapshots = SnapshotsSerializer(allow_null=True)


class OffsiteSerializer(serializers.Serializer[Any]):
    enabled = serializers.BooleanField()
    configured = serializers.BooleanField(help_text="Switched on, with address, bucket and keys.")
    endpoint = serializers.CharField()
    bucket = serializers.CharField()
    prefix = serializers.CharField()


class LatestRunsSerializer(serializers.Serializer[Any]):
    db = BackupRunSerializer(allow_null=True)
    files = BackupRunSerializer(allow_null=True)
    offsite = BackupRunSerializer(allow_null=True)
    drill = BackupRunSerializer(allow_null=True)
    check = BackupRunSerializer(allow_null=True)


class BackupSummarySerializer(serializers.Serializer[Any]):
    enabled = serializers.BooleanField()
    agent = BackupAgentSerializer()
    local_dir = serializers.CharField(help_text="Where the server keeps backups (set at install).")
    s3 = OffsiteSerializer()
    next_backup_at = serializers.DateTimeField(allow_null=True)
    next_drill_at = serializers.DateTimeField(allow_null=True)
    last_success_db_at = serializers.DateTimeField(allow_null=True)
    last_success_drill_at = serializers.DateTimeField(allow_null=True)
    pending = serializers.IntegerField(help_text="Jobs queued or running.")
    latest = LatestRunsSerializer()


RUN_REQUESTS = ("backup", "drill", "check")


class RunRequestSerializer(serializers.Serializer[Any]):
    what = serializers.ChoiceField(
        choices=RUN_REQUESTS,
        help_text="backup: database, files and offsite now; drill: the restore drill; "
        "check: test the destinations.",
    )


class RunQuerySerializer(serializers.Serializer[Any]):
    kind = serializers.ChoiceField(choices=BackupKind.choices, required=False)
    status = serializers.ChoiceField(choices=RunStatus.choices, required=False)


__all__ = [
    "BackupRunSerializer",
    "BackupSummarySerializer",
    "RunQuerySerializer",
    "RunRequestSerializer",
    "RunTrigger",
]
