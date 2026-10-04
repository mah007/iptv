"""Renditions, transcode jobs and tracks (SPEC §6 media, §7.3; ADR-0010, ADR-0014).

A `Rendition` is one output of a media file, stored on the media volume under
`renditions/<storage_key>/` (the asset directory; `storage_key` is a key, never a
path; the layout is in `apps.media.layout`):

- `compat.mp4` produced by a `TranscodeJob`, or `source.<ext>`, a symlink to the
  library file when the source already plays everywhere;
- the HLS ladder: one `hls_variant` per rung (`hls/v0/`, ...) and one `hls_master`
  per presentation (`hls/master.m3u8`, and capped or UHD ones such as `hls720/`);
- `uhd.<ext>`, the UHD version, with its HLS variant in `hls2160/uhd/`;
- `thumbs/`, the scrubbing sprites and `thumbs.vtt`.

The edge serves them through signed `/v/<token>/` URLs (ADR-0007). `AudioTrack` and
`SubtitleTrack` describe the file's tracks; text subtitles are converted to WebVTT and
SRT under `subs/`.
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
    THUMBNAILS = "thumbnails", "Thumbnails"


class RenditionStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    READY = "ready", "Ready"
    FAILED = "failed", "Failed"


#: Renditions a player can open on their own that make a title `ready` (ADR-0010).
PLAYABLE_KINDS = (RenditionKind.COMPAT_MP4, RenditionKind.SOURCE)
#: Kinds with several rows per file, told apart by `name`.
NAMED_KINDS = (RenditionKind.HLS_VARIANT, RenditionKind.HLS_MASTER)


class Rendition(BaseModel):
    media_file = models.ForeignKey(
        "catalog.MediaFile", on_delete=models.CASCADE, related_name="renditions"
    )
    kind = models.CharField(max_length=16, choices=RenditionKind.choices)
    #: The asset directory under DATA_ROOT/renditions ([A-Za-z0-9_-]{1,64}); never a path.
    storage_key = models.CharField(max_length=64)
    #: File extension of a progressive rendition (`compat.mp4`, `source.mkv`).
    container = models.CharField(max_length=8, default="mp4")
    #: Its name in the asset directory (`apps.media.layout`): the file stem (`compat`,
    #: `uhd`), the presentation directory and token rendition of an `hls_master`
    #: (`hls`, `hls720`, `hls2160`), or the folder of an `hls_variant` (`v0`, `uhd`).
    name = models.CharField(max_length=32, blank=True)
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
    #: Facts the HLS master needs and the admin shows: `codecs`, `peak_bps`,
    #: `average_bps`, `frame_rate`, `video_range`, `box_height` (variants); `audio`
    #: renditions and the ceiling (masters); sheet and tile counts (thumbnails).
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("media_file", "kind", "-height")
        constraints = (
            models.UniqueConstraint(
                fields=("media_file", "kind"),
                condition=~Q(kind__in=("hls_variant", "hls_master")),
                name="media_rendition_file_kind",
            ),
            models.UniqueConstraint(
                fields=("media_file", "kind", "name"),
                condition=Q(kind__in=("hls_variant", "hls_master")),
                name="media_rendition_file_kind_name",
            ),
        )
        indexes = (models.Index(fields=("status", "kind"), name="media_rendition_status"),)

    def __str__(self) -> str:
        return f"rendition:{self.kind}:{self.pk}"


class TranscodeProfile(models.TextChoices):
    COMPAT_MP4 = "compat_mp4", "Compat MP4"
    HLS = "hls", "HLS ladder"
    UHD = "uhd", "UHD version"
    THUMBNAILS = "thumbnails", "Thumbnails"
    SUBTITLES = "subtitles", "Subtitles"


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
#: Default priority per profile: quick jobs that complete a ready title first, the long
#: ladder and UHD encodes after every pending compat MP4.
PROFILE_PRIORITY = {
    "compat_mp4": PRIORITY_DEFAULT,
    "subtitles": 6,
    "thumbnails": 6,
    "hls": 3,
    "uhd": 2,
}


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


# --- Tracks (SPEC §6) -------------------------------------------------------------------


class AudioTrack(BaseModel):
    """An audio stream of a media file, as probed. `default` is the track players start
    with; an admin may change it and the language and title (kept across re-probes)."""

    media_file = models.ForeignKey(
        "catalog.MediaFile", on_delete=models.CASCADE, related_name="audio_tracks"
    )
    stream_index = models.PositiveIntegerField()
    language = models.CharField(max_length=8, default="und")  # ISO 639-2
    codec = models.CharField(max_length=32)
    channels = models.PositiveSmallIntegerField(default=0)
    title = models.CharField(max_length=200, blank=True)
    default = models.BooleanField(default=False)
    forced = models.BooleanField(default=False)
    commentary = models.BooleanField(default=False)

    class Meta:
        ordering = ("media_file", "stream_index")
        constraints = (
            models.UniqueConstraint(
                fields=("media_file", "stream_index"), name="media_audio_file_stream"
            ),
        )

    def __str__(self) -> str:
        return f"audio:{self.pk}"


class SubtitleFormat(models.TextChoices):
    SRT = "srt", "SubRip"
    VTT = "vtt", "WebVTT"
    ASS = "ass", "ASS/SSA"
    PGS = "pgs", "PGS (image)"
    VOBSUB = "vobsub", "VobSub (image)"
    OTHER = "other", "Other"


class SubtitleStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    READY = "ready", "Ready"
    FAILED = "failed", "Failed"
    #: Image subtitles (PGS, VobSub): no text to convert; a burn-in candidate.
    UNSUPPORTED = "unsupported", "Image (burn-in only)"


#: Largest subtitle file accepted from a sidecar or an upload.
SUBTITLE_MAX_BYTES = 4 * 1024 * 1024


class SubtitleTrack(BaseModel):
    """A subtitle of a media file: an embedded stream (`stream_index`), a sidecar file
    next to it in the library (`external`, `sidecar_path`), or an admin upload
    (`external`, `upload`). Text subtitles are converted to UTF-8 WebVTT and SRT at
    `renditions/<asset>/subs/<storage_key>.{vtt,srt}` and listed in the HLS masters."""

    media_file = models.ForeignKey(
        "catalog.MediaFile", on_delete=models.CASCADE, related_name="subtitle_tracks"
    )
    stream_index = models.PositiveIntegerField(null=True, blank=True)
    external = models.BooleanField(default=False)
    #: A sidecar's path inside its library (shown to admins; never an absolute path).
    sidecar_path = models.CharField(max_length=1024, blank=True)
    #: An uploaded sidecar's bytes, as received (converted by the subtitles job).
    upload = models.BinaryField(null=True, blank=True, editable=False)
    #: The uploaded file's name (for its extension and the admin).
    upload_name = models.CharField(max_length=255, blank=True)
    codec = models.CharField(max_length=32, blank=True)  # subrip, ass, webvtt, mov_text, ...
    format = models.CharField(max_length=8, choices=SubtitleFormat.choices)
    language = models.CharField(max_length=8, default="und")  # ISO 639-2
    title = models.CharField(max_length=200, blank=True)
    default = models.BooleanField(default=False)
    forced = models.BooleanField(default=False)
    hearing_impaired = models.BooleanField(default=False)
    #: The detected character encoding of a sidecar or upload (`utf-8`, `cp1256`, ...).
    encoding = models.CharField(max_length=32, blank=True)
    #: The xxhash64 of a sidecar's bytes when it was converted (a change converts again).
    source_hash = models.CharField(max_length=16, blank=True)
    #: File stem under `subs/` (`3.eng`, `x1.ara`); empty until converted.
    storage_key = models.CharField(max_length=64, blank=True)
    status = models.CharField(
        max_length=12, choices=SubtitleStatus.choices, default=SubtitleStatus.PENDING
    )
    error = models.CharField(max_length=200, blank=True)
    #: Cues in the converted WebVTT (0 until converted).
    cues = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("media_file", "external", "stream_index", "created_at")
        constraints = (
            models.UniqueConstraint(
                fields=("media_file", "stream_index"),
                condition=Q(stream_index__isnull=False),
                name="media_subtitle_file_stream",
            ),
            models.UniqueConstraint(
                fields=("media_file", "sidecar_path"),
                condition=~Q(sidecar_path=""),
                name="media_subtitle_file_sidecar",
            ),
            models.UniqueConstraint(
                fields=("media_file", "storage_key"),
                condition=~Q(storage_key=""),
                name="media_subtitle_file_key",
            ),
        )

    def __str__(self) -> str:
        return f"subtitle:{self.pk}"

    @property
    def is_text(self) -> bool:
        return self.format not in (SubtitleFormat.PGS, SubtitleFormat.VOBSUB)
