"""Anti-sharing evidence and flags (SPEC §11.1; G-27, ADR-0021)."""

from django.db import models

from apps.core.models import BaseModel


class SightingSource(models.TextChoices):
    PLAYBACK = "playback", "Playback start"
    XTREAM = "xtream", "IPTV app sign-in"
    STREAM = "stream", "Stream heartbeat"
    PORTAL = "portal", "Portal sign-in"


class Sighting(BaseModel):
    """One customer (and device) seen from one network within one clock hour.

    `network` is the client's /24 (IPv4) or /64 (IPv6), never the address itself, or
    `local` for the operator's own network. `asn`, `asn_org` and `country` come from
    DB-IP Lite at the time (empty when unknown). Rows are kept for
    `privacy.antisharing_retention_days`.
    """

    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="sightings")
    device = models.ForeignKey(
        "accounts.Device", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    network = models.CharField(max_length=48)
    asn = models.PositiveIntegerField(null=True, blank=True)
    asn_org = models.CharField(max_length=120, blank=True)
    country = models.CharField(max_length=2, blank=True)
    source = models.CharField(max_length=16, choices=SightingSource.choices)
    hour = models.DateTimeField()
    first_seen = models.DateTimeField()
    last_seen = models.DateTimeField()
    hits = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ("-last_seen",)
        constraints = (
            models.UniqueConstraint(
                fields=("user", "device", "network", "source", "hour"),
                name="antisharing_sighting_once_per_hour",
                nulls_distinct=False,
            ),
        )
        indexes = (
            models.Index(fields=("user", "-last_seen"), name="antisharing_sighting_user"),
            models.Index(fields=("last_seen",), name="antisharing_sighting_seen"),
        )

    def __str__(self) -> str:
        return f"{self.user_id}@{self.network}"


class FlagStatus(models.TextChoices):
    OPEN = "open", "Open"
    REVIEWED = "reviewed", "Reviewed"


class RiskFlag(BaseModel):
    """A customer whose score reached `antisharing.flag_score`, waiting for an admin.

    One open flag per customer; it keeps the highest score and the evidence of that
    moment, and the escalation steps taken (`actions`: warn, reset, suspend). A flag
    never changes the account by itself.
    """

    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="risk_flags")
    status = models.CharField(max_length=16, choices=FlagStatus.choices, default=FlagStatus.OPEN)
    score = models.PositiveIntegerField(default=0)
    signals = models.JSONField(default=dict, blank=True)
    evidence = models.JSONField(default=list, blank=True)
    opened_at = models.DateTimeField()
    last_scored_at = models.DateTimeField()
    #: The score the admins were last told about (they hear again only when it rises).
    notified_score = models.PositiveIntegerField(default=0)
    actions = models.JSONField(default=list, blank=True)
    reviewed_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ("-opened_at",)
        constraints = (
            models.UniqueConstraint(
                fields=("user",),
                condition=models.Q(status="open"),
                name="antisharing_one_open_flag_per_user",
            ),
        )
        indexes = (models.Index(fields=("status", "-opened_at"), name="antisharing_flag_status"),)

    def __str__(self) -> str:
        return f"flag:{self.user_id}:{self.status}:{self.score}"


class ExcessBytes(BaseModel):
    """A playback session that was served far more bytes than its title explains (G-17).

    `expected_bytes` is the title's runtime times the bitrate of the rendition the session
    played (the highest, for HLS); `ratio` is `bytes_sent / expected_bytes`. Re-serving a
    signed URL to other viewers, or pulling a file again and again, shows up here; a
    player that buffers ahead or seeks back stays far below `antisharing.bytes_ratio`.
    One row per session, updated while the session keeps growing; it counts towards the
    customer's score (signal `excess_bytes_24h`) and never locks anything. Rows are kept
    for `privacy.antisharing_retention_days`, like sightings.
    """

    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="excess_bytes")
    session = models.OneToOneField(
        "playback.PlaybackSession", on_delete=models.CASCADE, related_name="excess_bytes"
    )
    device = models.ForeignKey(
        "accounts.Device", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    title_name = models.CharField(max_length=255, blank=True)
    rendition = models.CharField(max_length=32, blank=True)
    network = models.CharField(max_length=48, blank=True)
    bytes_sent = models.BigIntegerField()
    expected_bytes = models.BigIntegerField()
    ratio = models.FloatField()
    detected_at = models.DateTimeField()

    class Meta:
        ordering = ("-detected_at",)
        indexes = (
            models.Index(fields=("user", "-detected_at"), name="antisharing_excess_user"),
            models.Index(fields=("detected_at",), name="antisharing_excess_detected"),
        )

    def __str__(self) -> str:
        return f"excess:{self.user_id}:{self.ratio:.1f}x"
