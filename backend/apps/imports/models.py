"""Imports from controlled storage, licensed VOD, or one direct link (ADR-0025/0027).

SPEC §1.1: media enters only through admin libraries. An import is how a file the
operator owns or is licensed for reaches a library folder without being uploaded from a
home connection: the server pulls it at datacentre speed.

- `ImportSource`: a remote the admin set up (an S3-compatible bucket, an SFTP or
  WebDAV server, Google Drive, Dropbox or OneDrive through rclone, or licensed Xtream
  VOD). Credentials are Fernet encrypted (`credentials_encrypted`) and never
  serialized; the API reports which ones are set.
- `ImportJob`: one admin's request to copy specific files or folders of a source, or
  one direct link, into a library, with who asked, from where and under which rights.
  Jobs run on the `import` queue in the `importer` service, the only one that mounts
  the media root read-write.
- `ImportFile`: a file a job placed in a library, keyed like `MediaFile` (library and
  library-relative path), so the rights recorded with the job reach the title it is
  matched to (`apps.imports.rights`).
"""

from typing import Final

from django.conf import settings
from django.db import models

from apps.core.models import BaseModel


class SourceKind(models.TextChoices):
    S3 = "s3", "S3-compatible bucket"
    SFTP = "sftp", "SFTP"
    WEBDAV = "webdav", "WebDAV"
    DRIVE = "drive", "Google Drive"
    DROPBOX = "dropbox", "Dropbox"
    ONEDRIVE = "onedrive", "OneDrive"
    XTREAM = "xtream", "Xtream / IPTV VOD"


#: Kinds whose credential is an OAuth token from `rclone authorize` (ADR-0025).
OAUTH_KINDS: Final = frozenset({SourceKind.DRIVE, SourceKind.DROPBOX, SourceKind.ONEDRIVE})


class TestStatus(models.TextChoices):
    """The last Test of a source: `ok` or why it failed (codes the admin translates)."""

    __test__ = False  # not a pytest class

    OK = "ok", "Reachable"
    AUTH_FAILED = "auth_failed", "Sign-in refused"
    NOT_FOUND = "not_found", "Folder or bucket not found"
    UNREACHABLE = "unreachable", "Unreachable"
    TIMEOUT = "timeout", "Timed out"
    TLS_FAILED = "tls_failed", "Certificate not trusted"
    UNSAFE = "unsafe_destination", "Destination not allowed"
    UNEXPECTED = "unexpected", "Unexpected error"


class ImportSource(BaseModel):
    name = models.CharField(max_length=100, unique=True)
    kind = models.CharField(max_length=16, choices=SourceKind.choices)
    #: What isn't secret, per kind (`apps.imports.remotes.OPTIONS`): endpoint, region,
    #: bucket, host, port, user name, URL, root folder, drive id, a pinned host key.
    options = models.JSONField(default=dict, blank=True)
    #: Fernet: JSON of the kind's secrets (keys, passwords, a private key, the OAuth
    #: token). Never serialized or logged.
    credentials_encrypted = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    last_test_at = models.DateTimeField(null=True, blank=True)
    last_test_status = models.CharField(
        max_length=32, choices=TestStatus.choices, blank=True, default=""
    )
    last_test_message = models.CharField(max_length=300, blank=True, default="")

    class Meta:
        ordering = ("name", "id")

    def __str__(self) -> str:
        return f"{self.kind}:{self.name}"


class JobStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    DONE = "done", "Done"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


ACTIVE_JOB_STATUSES: Final = (JobStatus.QUEUED, JobStatus.RUNNING)


class JobPhase(models.TextChoices):
    """Where a running job is."""

    PLANNING = "planning", "Listing and checking"
    COPYING = "copying", "Copying"
    MOVING = "moving", "Moving into the library"


class JobOrigin(models.TextChoices):
    SOURCE = "source", "A source"
    LINK = "link", "A direct link"


class ImportJob(BaseModel):
    origin = models.CharField(max_length=8, choices=JobOrigin.choices)
    source = models.ForeignKey(
        ImportSource, null=True, blank=True, on_delete=models.SET_NULL, related_name="jobs"
    )
    #: The source's name and kind when the job was made (history outlives sources).
    source_name = models.CharField(max_length=100, blank=True)
    source_kind = models.CharField(max_length=16, blank=True)
    #: What to copy: [{"path": "Films/Metropolis (1927)", "is_dir": true}, ...], paths
    #: relative to the source's root.
    items = models.JSONField(default=list, blank=True)
    #: A direct link, Fernet encrypted (signed links carry tokens); `url_display` is the
    #: same without user info and query string.
    url_encrypted = models.TextField(blank=True, default="")
    url_display = models.CharField(max_length=500, blank=True)
    #: A direct link's file name in the library (default: the link's last segment).
    file_name = models.CharField(max_length=255, blank=True)
    #: The expected SHA-256, SHA-1 or MD5 of a direct link's file (hex), when given.
    checksum = models.CharField(max_length=128, blank=True)
    library = models.ForeignKey(
        "library.Library", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    library_name = models.CharField(max_length=100, blank=True)
    #: A folder inside the library the files go to ("" for its top level).
    subfolder = models.CharField(max_length=500, blank=True)
    rights_holder = models.CharField(max_length=255)
    license_ref = models.CharField(max_length=255)
    license_expires_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=JobStatus.choices, default=JobStatus.QUEUED)
    phase = models.CharField(max_length=16, choices=JobPhase.choices, blank=True, default="")
    #: A stable code for a failure (`insufficient_space`, `auth_failed`, ...).
    error_code = models.CharField(max_length=32, blank=True, default="")
    error = models.CharField(max_length=500, blank=True, default="")
    #: What the admin's translated message needs: sizes for `insufficient_space`,
    #: names for `target_exists` or `unsupported_type`.
    error_params = models.JSONField(default=dict, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    #: Bumped at each dispatch; a task carrying an older number is a stale duplicate.
    dispatch_no = models.PositiveIntegerField(default=0)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    #: A retry after a transient error waits until then.
    retry_at = models.DateTimeField(null=True, blank=True)
    cancel_requested = models.BooleanField(default=False)
    files_total = models.PositiveIntegerField(default=0)
    files_done = models.PositiveIntegerField(default=0)
    bytes_total = models.BigIntegerField(default=0)
    bytes_done = models.BigIntegerField(default=0)
    speed_bps = models.BigIntegerField(null=True, blank=True)
    eta_s = models.PositiveIntegerField(null=True, blank=True)
    #: Files left out of a folder because of their type (count, and a few names).
    skipped = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    #: Who asked, as shown in the history after the account is gone.
    created_by_name = models.CharField(max_length=150, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = (models.Index(fields=("status", "created_at"), name="imports_job_status"),)

    def __str__(self) -> str:
        return f"import:{self.pk}"


class FileKind(models.TextChoices):
    VIDEO = "video", "Video"
    SUBTITLE = "subtitle", "Subtitle"


class ImportFile(BaseModel):
    job = models.ForeignKey(ImportJob, on_delete=models.CASCADE, related_name="files")
    library = models.ForeignKey(
        "library.Library", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    #: The path inside the library (what `MediaFile.storage_key` will be), never absolute.
    storage_key = models.CharField(max_length=1024)
    #: Where it came from: the path on the source, or the link's display form.
    remote_path = models.CharField(max_length=1024)
    kind = models.CharField(max_length=8, choices=FileKind.choices)
    size = models.BigIntegerField(default=0)
    #: When the job's rights were applied to the title this file was matched to.
    rights_applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("storage_key",)
        indexes = (models.Index(fields=("library", "storage_key"), name="imports_file_key"),)

    def __str__(self) -> str:
        return f"imported:{self.pk}"

    @property
    def relative_path(self) -> str:
        """The path inside its library, for display (never an absolute storage path)."""
        return self.storage_key
