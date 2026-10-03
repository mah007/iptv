"""Libraries and their scans (SPEC §6 library, §7.1).

A library is a folder under the read-only media mount (`/media` in the containers,
`settings.LIBRARY_ROOT`). Its files are catalog `MediaFile` rows keyed by the path
relative to the library, so the admin and the API never show storage paths.
"""

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.core.models import BaseModel


class LibraryKind(models.TextChoices):
    """Decides how files are classified (`apps.library.parsing.parse_path`)."""

    MOVIES = "movies", "Movies"
    SERIES = "series", "Series"
    DOCUMENTARIES = "documentaries", "Documentaries"
    KIDS = "kids", "Kids"
    MIXED = "mixed", "Mixed"


class ProcessingPolicy(models.TextChoices):
    """When outputs are produced (`apps.media.planner.ProcessingPolicy`)."""

    INGEST = "ingest", "At ingest"
    ON_DEMAND = "on_demand", "On first play"
    PASSTHROUGH = "passthrough", "Never (serve the source)"


SCAN_INTERVAL_MAX_MIN = 7 * 24 * 60


class Library(BaseModel):
    name = models.CharField(max_length=100, unique=True)
    kind = models.CharField(max_length=16, choices=LibraryKind.choices)
    #: Absolute container path under LIBRARY_ROOT, e.g. /media/movies.
    path = models.CharField(max_length=500, unique=True)
    processing_policy = models.CharField(
        max_length=16, choices=ProcessingPolicy.choices, default=ProcessingPolicy.INGEST
    )
    #: Added to every title matched from this library, besides the genre categories.
    default_categories = models.ManyToManyField("catalog.Category", blank=True, related_name="+")
    scan_interval_min = models.PositiveIntegerField(
        default=15, validators=[MinValueValidator(1), MaxValueValidator(SCAN_INTERVAL_MAX_MIN)]
    )
    enabled = models.BooleanField(default=True)
    last_scan_at = models.DateTimeField(null=True, blank=True)
    #: Totals after the last scan: files, bytes, movies, episodes, errors, review.
    stats = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("name",)
        verbose_name_plural = "libraries"

    def __str__(self) -> str:
        return self.name


class ScanTrigger(models.TextChoices):
    WATCHER = "watcher", "File watcher"
    SCHEDULE = "schedule", "Schedule"
    MANUAL = "manual", "Manual"


class ScanStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    DONE = "done", "Done"
    FAILED = "failed", "Failed"


ACTIVE_SCAN_STATUSES = (ScanStatus.QUEUED, ScanStatus.RUNNING)


class ScanJob(BaseModel):
    """One reconciliation scan of a library, or of one path the watcher reported.

    Counts: `found` files seen; `new`, `changed`, `moved` and `removed` the differences
    from the catalogue (SPEC §7.1); `errors` files that could not be read. `log` holds
    short messages with library-relative paths only.
    """

    library = models.ForeignKey(Library, on_delete=models.CASCADE, related_name="scans")
    trigger = models.CharField(max_length=16, choices=ScanTrigger.choices)
    status = models.CharField(max_length=16, choices=ScanStatus.choices, default=ScanStatus.QUEUED)
    #: The folder or file the watcher reported, relative to the library; empty: everything.
    path = models.CharField(max_length=1024, blank=True)
    found = models.PositiveIntegerField(default=0)
    new = models.PositiveIntegerField(default=0)
    changed = models.PositiveIntegerField(default=0)
    moved = models.PositiveIntegerField(default=0)
    removed = models.PositiveIntegerField(default=0)
    errors = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    log = models.TextField(blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = (
            models.Index(fields=("library", "-created_at"), name="library_scan_recent"),
            models.Index(fields=("status",), name="library_scan_status"),
        )

    def __str__(self) -> str:
        return f"scan:{self.pk}"
