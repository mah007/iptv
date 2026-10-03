"""Renditions and transcode jobs (SPEC §6 media, §7.3; ADR-0010).

A `Rendition` is one playable form of a media file, stored on the media volume under
`renditions/<storage_key>/` (the asset directory; `storage_key` is a key, never a
path): `compat.mp4` produced by a `TranscodeJob`, or `source.<ext>`, a symlink to the
library file when the source already plays everywhere. The edge serves both through
signed `/v/<token>/` URLs (ADR-0007).
"""

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from apps.core.models import BaseModel


class RenditionKind(models.TextChoices):
    COMPAT_MP4 = "compat_mp4", "Compat MP4"
    SOURCE = "source", "Source (direct play)"
    HLS_VARIANT = "hls_variant", "HLS variant"
    HLS_MASTER = "hls_master", "HLS master"
    UHD = "uhd", "UHD"


class RenditionStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    READY = "ready", "Ready"
    FAILED = "failed", "Failed"


#: Renditions a player can open on their own (what `playable_title` offers).
PLAYABLE_KINDS = (RenditionKind.COMPAT_MP4, RenditionKind.SOURCE)


class Rendition(BaseModel):
    media_file = models.ForeignKey(
        "catalog.MediaFile", on_delete=models.CASCADE, related_name="renditions"
    )
    kind = models.CharField(max_length=16, choices=RenditionKind.choices)
    #: The asset directory under DATA_ROOT/renditions ([A-Za-z0-9_-]{1,64}); never a path.
    storage_key = models.CharField(max_length=64)
    #: File extension of a progressive rendition (`compat.mp4`, `source.mkv`).
    container = models.CharField(max_length=8, default="mp4")
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    bitrate = models.BigIntegerField(null=True, blank=True)
    codec = models.CharField(max_length=32, blank=True)
    size = models.BigIntegerField(default=0)
    status = models.CharField(
        max_length=8, choices=RenditionStatus.choices, default=RenditionStatus.PENDING
    )
    encoder_used = models.CharField(max_length=32, blank=True)
    duration_s = models.FloatField(null=True, blank=True)
    error = models.CharField(max_length=500, blank=True)
    ready_at = models.DateTimeField(null=True, blank=True)
    #: The source's xxhash64 when the rendition was made: a changed file makes it stale.
    source_hash = models.CharField(max_length=16, blank=True)

    class Meta:
        ordering = ("media_file", "kind", "-height")
        constraints = (
            models.UniqueConstraint(
                fields=("media_file", "kind"),
                condition=~Q(kind="hls_variant"),
                name="media_rendition_file_kind",
            ),
        )
        indexes = (models.Index(fields=("status", "kind"), name="media_rendition_status"),)

    def __str__(self) -> str:
        return f"rendition:{self.kind}:{self.pk}"


class TranscodeProfile(models.TextChoices):
    COMPAT_MP4 = "compat_mp4", "Compat MP4"


class TranscodeBackend(models.TextChoices):
    CPU = "cpu", "CPU (libx264)"
    QSV = "qsv", "Intel QSV"
    VAAPI = "vaapi", "VA-API"
    NVENC = "nvenc", "NVIDIA NVENC"


class JobStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    DONE = "done", "Done"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


ACTIVE_JOB_STATUSES = (JobStatus.QUEUED, JobStatus.RUNNING)
PRIORITY_MIN = 0
PRIORITY_MAX = 9
PRIORITY_DEFAULT = 5
#: A viewer is waiting (on-demand fallback): run before ingest work.
PRIORITY_URGENT = 8


class TranscodeJob(BaseModel):
    """One output for one media file. `priority` 0-9, higher runs first."""

    media_file = models.ForeignKey(
        "catalog.MediaFile", on_delete=models.CASCADE, related_name="transcode_jobs"
    )
    rendition = models.ForeignKey(
        Rendition, null=True, blank=True, on_delete=models.SET_NULL, related_name="jobs"
    )
    profile = models.CharField(
        max_length=16, choices=TranscodeProfile.choices, default=TranscodeProfile.COMPAT_MP4
    )
    #: Video is copied (container or audio fixes only): seconds to minutes.
    remux = models.BooleanField(default=False)
    backend = models.CharField(
        max_length=8, choices=TranscodeBackend.choices, default=TranscodeBackend.CPU
    )
    #: The encoder of the last attempt (`libx264`, `h264_nvenc`, `copy`).
    encoder = models.CharField(max_length=32, blank=True)
    priority = models.PositiveSmallIntegerField(
        default=PRIORITY_DEFAULT,
        validators=[MinValueValidator(PRIORITY_MIN), MaxValueValidator(PRIORITY_MAX)],
    )
    status = models.CharField(max_length=12, choices=JobStatus.choices, default=JobStatus.QUEUED)
    progress = models.FloatField(default=0.0)  # 0-100
    fps = models.FloatField(null=True, blank=True)
    speed = models.FloatField(null=True, blank=True)
    eta_s = models.PositiveIntegerField(null=True, blank=True)
    worker_host = models.CharField(max_length=128, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    #: A stable code: `ffmpeg`, `timeout`, `verify:<problems>`, `source_missing`, ...
    error = models.CharField(max_length=500, blank=True)
    #: The last 50 lines of ffmpeg's stderr, storage paths redacted.
    error_tail = models.TextField(blank=True)
    #: Bumped on every (re-)dispatch; a Celery message for an older value is stale.
    dispatches = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = (
            models.UniqueConstraint(
                fields=("media_file", "profile"),
                condition=Q(status__in=("queued", "running")),
                name="media_job_one_active",
            ),
        )
        indexes = (
            models.Index(fields=("status", "-priority", "created_at"), name="media_job_queue"),
            models.Index(fields=("-created_at",), name="media_job_recent"),
        )

    def __str__(self) -> str:
        return f"transcode:{self.pk}"
