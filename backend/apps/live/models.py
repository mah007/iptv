"""Live TV and its guide (SPEC §6 live, M12; ADR-0017).

- `LiveChannel`: one channel the operator owns or is licensed to redistribute. Its
  group is a live `catalog.Category` (ADR-0017: plans gate by category, and the
  Xtream categories need nothing else). `xc_id` comes from the catalogue's shared
  sequence, so a stream id never names a movie or an episode too. The source URL
  is encrypted at rest (`source_encrypted`) and never serialized or logged.
- `EpgSource`: an XMLTV feed, by URL (encrypted: URLs carry tokens) or uploaded.
- `EpgChannel`: a `<channel>` of a source's last import.
- `EpgProgram`: a programme. The table is partitioned by month on `start`
  (migration 0001, `apps.core.partitions`), so the database primary key is
  `(id, start)`; Django still addresses rows by `id`, which is unique by
  construction (UUIDv7). Its foreign key cascades in the database, not in Django.
- `LiveIntegration`: an ErsatzTV or MediaMTX instance channels are synced from.
"""

import re
from typing import Any, Final

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

from apps.catalog.models import CATALOG_XC_ID_SEQUENCE
from apps.core.db import NextVal
from apps.core.models import BaseModel

EPG_SOURCE_ID_SEQUENCE: Final = "live_epg_source_epg_id_seq"

#: XMLTV ids are also the M3U tvg-id: no whitespace and no quotes (compat/m3u.md).
EPG_CHANNEL_ID = re.compile(r'[^\s"]{1,255}')
epg_channel_id_validator = RegexValidator(
    r'^[^\s"]*$', "No spaces or quotes.", code="invalid_epg_channel_id"
)


class ChannelOutput(models.TextChoices):
    """What the extension-less Xtream URL (`/{u}/{p}/{id}`) plays (ADR-0017)."""

    TS = "ts", "MPEG-TS"
    HLS = "hls", "HLS"


class ChannelTranscode(models.TextChoices):
    """Copy is the rule; real-time H.264 is the capped fallback (SPEC §1.4)."""

    COPY = "copy", "Copy (remux)"
    H264 = "h264", "Real-time H.264"


class ChannelOrigin(models.TextChoices):
    MANUAL = "manual", "Created by an admin"
    ERSATZTV = "ersatztv", "ErsatzTV"
    MEDIAMTX = "mediamtx", "MediaMTX"


class IntegrationKind(models.TextChoices):
    ERSATZTV = "ersatztv", "ErsatzTV"
    MEDIAMTX = "mediamtx", "MediaMTX"


class LiveIntegration(BaseModel):
    """A configured ErsatzTV or MediaMTX instance the admin syncs channels from."""

    kind = models.CharField(max_length=16, choices=IntegrationKind.choices)
    name = models.CharField(max_length=100)
    #: ErsatzTV: its web origin. MediaMTX: its API origin (http://mediamtx:9997).
    base_url = models.CharField(max_length=500)
    #: MediaMTX only: where channels read from, e.g. rtsp://mediamtx:8554.
    stream_base_url = models.CharField(max_length=500, blank=True)
    #: Fernet: JSON {"username", "password", "access_token"} (any of them); never
    #: serialized. MediaMTX uses the user for its API and for reading streams.
    credentials_encrypted = models.TextField(blank=True, default="")
    #: The live group synced channels join (created from the name when unset).
    group = models.ForeignKey(
        "catalog.Category",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        limit_choices_to={"kind": "live"},
    )
    rights_holder = models.CharField(max_length=255)
    license_ref = models.CharField(max_length=255, blank=True)
    last_sync_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")
    last_result = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("name", "id")

    def __str__(self) -> str:
        return f"{self.kind}:{self.name}"


class EpgSourceKind(models.TextChoices):
    URL = "url", "URL"
    UPLOAD = "upload", "Uploaded file"


class EpgSource(BaseModel):
    name = models.CharField(max_length=100)
    kind = models.CharField(max_length=8, choices=EpgSourceKind.choices)
    #: Fernet; http(s) only. Never serialized: feeds often carry an access token.
    url_encrypted = models.TextField(blank=True, default="")
    #: The uploaded XMLTV, gzip-compressed (the web process has no media volume).
    upload = models.BinaryField(null=True, blank=True)
    upload_name = models.CharField(max_length=255, blank=True)
    refresh_cron = models.CharField(max_length=100, default="0 */6 * * *")
    #: Lower first when two sources describe the same XMLTV id.
    priority = models.IntegerField(default=100)
    enabled = models.BooleanField(default=True)
    #: The Xtream `epg_id` of its programmes (an integer, SPEC §7.5).
    epg_id = models.BigIntegerField(
        unique=True, editable=False, db_default=NextVal(EPG_SOURCE_ID_SEQUENCE)
    )
    integration = models.ForeignKey(
        LiveIntegration,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="epg_sources",
    )
    etag = models.CharField(max_length=255, blank=True)
    last_modified = models.CharField(max_length=64, blank=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_ok_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")
    stats = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("priority", "name", "id")

    def __str__(self) -> str:
        return self.name


class EpgChannel(BaseModel):
    """A `<channel>` of a source, as of its last import."""

    source = models.ForeignKey(EpgSource, on_delete=models.CASCADE, related_name="channels")
    xmltv_id = models.CharField(max_length=255)
    #: {"en": "...", "ar": "...", "": "..."}: display names by language.
    names = models.JSONField(default=dict, blank=True)
    icon_url = models.CharField(max_length=1024, blank=True)

    class Meta:
        ordering = ("xmltv_id", "id")
        constraints = (
            models.UniqueConstraint(fields=("source", "xmltv_id"), name="live_epg_channel_unique"),
        )
        indexes = (models.Index(fields=("xmltv_id",), name="live_epg_channel_xmltv"),)

    def __str__(self) -> str:
        return self.xmltv_id

    def display_name(self) -> str:
        names = self.names or {}
        return str(names.get("en") or names.get("") or next(iter(names.values()), self.xmltv_id))


class EpgProgram(BaseModel):
    """One programme (partitioned monthly on `start`; see the module docstring)."""

    # DO_NOTHING here; the database cascades (migration 0001), so deleting a source
    # never makes Django collect hundreds of thousands of rows first.
    channel = models.ForeignKey(EpgChannel, on_delete=models.DO_NOTHING, related_name="programs")
    start = models.DateTimeField()
    stop = models.DateTimeField()
    title = models.CharField(max_length=500)
    title_ar = models.CharField(max_length=500, blank=True)
    description = models.TextField(blank=True)
    description_ar = models.TextField(blank=True)
    category = models.CharField(max_length=100, blank=True)
    #: ISO 639 code of `title` ("" when the feed did not say).
    lang = models.CharField(max_length=3, blank=True)

    class Meta:
        ordering = ("start", "id")
        indexes = (models.Index(fields=("channel", "start"), name="live_epgprogram_chan_start"),)
        constraints = (
            models.CheckConstraint(
                condition=models.Q(stop__gt=models.F("start")), name="live_epgprogram_stop_after"
            ),
        )

    def __str__(self) -> str:
        return f"{self.title} ({self.start:%Y-%m-%d %H:%M})"


class LiveChannel(BaseModel):
    xc_id = models.BigIntegerField(
        unique=True, editable=False, db_default=NextVal(CATALOG_XC_ID_SEQUENCE)
    )
    name = models.CharField(max_length=255)
    name_ar = models.CharField(max_length=255, blank=True)
    group = models.ForeignKey(
        "catalog.Category",
        on_delete=models.PROTECT,
        related_name="live_channels",
        limit_choices_to={"kind": "live"},
    )
    sort = models.IntegerField(default=0)
    epg_channel_id = models.CharField(
        max_length=255, blank=True, validators=[epg_channel_id_validator]
    )
    #: The source to take the guide from; None: any source, lowest priority first.
    epg_source = models.ForeignKey(
        EpgSource, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    #: Stored artwork: {"w185": {"webp": "images/...", "avif": ...}, ...}.
    logo = models.JSONField(default=dict, blank=True)
    #: Fernet of the source URL (http(s), rtmp(s), rtsp(s), srt). Never serialized.
    source_encrypted = models.TextField()
    output = models.CharField(max_length=4, choices=ChannelOutput.choices, default="ts")
    transcode = models.CharField(max_length=8, choices=ChannelTranscode.choices, default="copy")
    catchup_days = models.PositiveSmallIntegerField(default=0)
    #: Keep packaging without viewers (instant start; costs CPU and source bandwidth).
    always_on = models.BooleanField(default=False)
    enabled = models.BooleanField(default=False)
    rights_holder = models.CharField(max_length=255, blank=True)
    license_ref = models.CharField(max_length=255, blank=True)
    license_expires_at = models.DateTimeField(null=True, blank=True)
    #: Set by `enforce_licences` once the licence has run out and sessions were stopped.
    license_lapsed = models.BooleanField(default=False)
    origin = models.CharField(max_length=16, choices=ChannelOrigin.choices, default="manual")
    origin_ref = models.CharField(max_length=128, blank=True)
    integration = models.ForeignKey(
        LiveIntegration,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="channels",
    )
    #: The last source probe: codecs, width, height, fps, bitrate_kbps, copy_ok, at.
    probe = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("sort", "xc_id")
        constraints = (
            models.UniqueConstraint(
                fields=("integration", "origin_ref"),
                condition=models.Q(integration__isnull=False),
                name="live_channel_integration_ref",
            ),
            models.CheckConstraint(
                condition=models.Q(catchup_days__lte=365), name="live_channel_catchup_days"
            ),
            models.CheckConstraint(
                condition=~models.Q(enabled=True, rights_holder=""),
                name="live_channel_enabled_needs_rights",
            ),
        )
        indexes = (
            models.Index(fields=("group", "sort"), name="live_channel_group_sort"),
            models.Index(fields=("epg_channel_id",), name="live_channel_epg_id"),
        )

    def __str__(self) -> str:
        return self.name

    @property
    def storage_key(self) -> str:
        """The media token's `title`: the channel's folder under the live root."""
        return str(self.pk)

    @property
    def height(self) -> int:
        """The probed video height (0 when unknown: the quality ceiling allows it)."""
        value = (self.probe or {}).get("height")
        return int(value) if isinstance(value, int) and value > 0 else 0

    def license_valid(self, now: Any = None) -> bool:
        moment = now or timezone.now()
        return self.license_expires_at is None or self.license_expires_at > moment

    def clean(self) -> None:
        super().clean()
        if self.group_id is not None and self.group.kind != "live":
            raise ValidationError({"group": "The group must be a live category."})
        if self.enabled and not self.rights_holder.strip():
            raise ValidationError(
                {"rights_holder": ValidationError("Required to enable a channel.", code="required")}
            )
