"""Playback history (SPEC §6 playback): one row per playback session.

A row opens when a stream starts and closes when the session ends: an explicit
stop, a kick, the access period ending, or the sweeper finding it idle. While it
is open, the live state (heartbeats, bytes, slot) lives in redis-state under
`sess:<session_key>`; the sweeper copies it back.

`session_key` is SPEC's session id, `sha256(user|device|title_ref)` cut to the
32 hex digits the media token carries (ADR-0007). It repeats whenever the same
device plays the same title again, so it is not the primary key: at most one
open row holds a key at a time, and each playback gets its own UUIDv7 row.

The title is a generic reference (kind + UUID) for now; slice 3 may turn it into
foreign keys once the catalogue models are settled. `title_name` keeps what the
title was called when it played.
"""

from django.db import models

from apps.core.models import BaseModel


class TitleKind(models.TextChoices):
    MOVIE = "movie", "Movie"
    EPISODE = "episode", "Episode"
    LIVE = "live", "Live channel"  # title_id is a live.LiveChannel (M12)
    CATCHUP = "catchup", "Catch-up"  # a live channel's archive (timeshift)


class EndReason(models.TextChoices):
    STOPPED = "stopped", "Stopped"  # the player stopped, or its device started another title
    KICKED = "kicked", "Kicked"  # an admin stopped it, or the account or device lost access
    EXPIRED = "expired", "Access expired"
    LIMIT = "limit", "Stream limit"  # a newer stream took its slot (kick_oldest)
    IDLE = "idle", "Idle"  # no activity for too long (the sweeper)
    ERROR = "error", "Error"


class PlaybackSession(BaseModel):
    session_key = models.CharField(max_length=32)
    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="playback_sessions"
    )
    device = models.ForeignKey(
        "accounts.Device",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="playback_sessions",
    )
    title_kind = models.CharField(max_length=8, choices=TitleKind.choices)
    title_id = models.UUIDField()
    title_name = models.CharField(max_length=255, blank=True)
    rendition = models.CharField(max_length=32)
    ip = models.GenericIPAddressField(null=True, blank=True)
    country = models.CharField(max_length=2, blank=True)
    asn = models.PositiveIntegerField(null=True, blank=True)
    user_agent = models.CharField(max_length=256, blank=True)
    player = models.CharField(max_length=32, blank=True)
    started_at = models.DateTimeField()
    last_heartbeat_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    bytes_sent = models.BigIntegerField(default=0)
    end_reason = models.CharField(max_length=8, choices=EndReason.choices, blank=True)

    class Meta:
        ordering = ("-started_at", "-id")
        constraints = (
            models.UniqueConstraint(
                fields=("session_key",),
                condition=models.Q(ended_at__isnull=True),
                name="playback_session_one_open_per_key",
            ),
            models.CheckConstraint(
                condition=models.Q(ended_at__isnull=True, end_reason="")
                | (models.Q(ended_at__isnull=False) & ~models.Q(end_reason="")),
                name="playback_session_end_reason_iff_ended",
            ),
        )
        indexes = (
            models.Index(fields=("user", "-started_at"), name="playback_session_user"),
            models.Index(fields=("-started_at",), name="playback_session_started"),
            models.Index(
                fields=("last_heartbeat_at",),
                condition=models.Q(ended_at__isnull=True),
                name="playback_session_open",
            ),
        )

    def __str__(self) -> str:
        return f"{self.title_kind}:{self.title_id} ({self.session_key[:8]})"

    @property
    def title_ref(self) -> str:
        return f"{self.title_kind}:{self.title_id}"

    @property
    def is_active(self) -> bool:
        return self.ended_at is None
