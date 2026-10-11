"""Backups and the restore drill (ADR-0020).

A `BackupRun` is one job for the backup service (the agent in the `backup` container):
Django queues it (on schedule, or when an admin presses "Back up now"), the agent
claims it on its next poll, runs it and reports the result here. The run history is
what the admin's Backups page shows and what the staleness alerts read.
"""

from django.conf import settings
from django.db import models

from apps.core.models import BaseModel


class BackupKind(models.TextChoices):
    DB_FULL = "db_full", "Database (full)"
    DB_DIFF = "db_diff", "Database (differential)"
    FILES = "files", "Configuration, artwork and database dump"
    OFFSITE = "offsite", "Offsite copy"
    DRILL = "drill", "Restore drill"
    CHECK = "check", "Check the destinations"


DB_KINDS = (BackupKind.DB_FULL, BackupKind.DB_DIFF)


class RunStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"


class RunTrigger(models.TextChoices):
    SCHEDULE = "schedule", "Schedule"
    MANUAL = "manual", "Admin"
    INITIAL = "initial", "First backup"
    COMMAND = "command", "Command line"


class BackupRun(BaseModel):
    kind = models.CharField(max_length=16, choices=BackupKind.choices)
    status = models.CharField(max_length=16, choices=RunStatus.choices, default=RunStatus.QUEUED)
    trigger = models.CharField(max_length=16, choices=RunTrigger.choices)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    #: Options the agent reads (offsite `full`, `prune`; drill `timeout_s`).
    params = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    duration_s = models.FloatField(null=True, blank=True)
    size_bytes = models.BigIntegerField(null=True, blank=True)
    #: What the agent reported: backup label, snapshot ids, the drill's checks.
    detail = models.JSONField(default=dict, blank=True)
    #: Why it failed, scrubbed of secrets by the agent and redacted again here.
    error = models.TextField(blank=True)

    class Meta:
        ordering = ("-created_at", "-id")
        indexes = (
            models.Index(fields=("kind", "status", "-created_at"), name="ops_run_kind_status"),
            models.Index(fields=("status", "created_at"), name="ops_run_status_created"),
        )

    def __str__(self) -> str:
        return f"{self.kind} {self.status}"
