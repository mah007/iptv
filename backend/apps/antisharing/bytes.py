"""Sessions served far more bytes than their title explains (G-17's second half; ADR-0024).

The edges report what they served each playback session (`apps.playback.bytes`), the
sweeper keeps `PlaybackSession.bytes_sent` up to date, and `check` (from `score_recent`,
every 5 minutes) compares each movie or episode session of the last day with what its
title explains:

    expected = runtime (s) x bitrate (bit/s) / 8

The bitrate is the highest of the renditions of the kind the session played (compat:
the compat MP4s; hls: every rung; source; uhd), or of all the title's renditions when
none of that kind has one. A session at `antisharing.bytes_ratio` times that or more
(and at least `antisharing.bytes_min_mb`) gets an `ExcessBytes` row, updated while it
keeps growing; each one in the last 24 hours is a scoring signal (`excess_bytes_24h`).
Live and catch-up have no runtime and aren't checked. Nothing here locks an account.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final
from uuid import UUID

from django.db.models import Max, Q
from django.utils import timezone

from apps.antisharing.models import ExcessBytes
from apps.core.services import get_setting
from apps.playback.models import PlaybackSession, TitleKind

SESSIONS_PER_RUN: Final = 2000
WINDOW: Final = timedelta(hours=24)
MB: Final = 1024 * 1024

#: The media renditions behind each kind a session plays (the token's rendition).
_RENDITIONS: Final = {
    "compat": ("compat_mp4", "compat_capped"),
    "source": ("source",),
    "uhd": ("uhd",),
    "hls": ("hls_variant", "hls_master"),
}
_ALL_RENDITIONS: Final = (
    "compat_mp4",
    "compat_capped",
    "source",
    "uhd",
    "hls_variant",
    "hls_master",
)


@dataclass(frozen=True, slots=True)
class Explained:
    """What a title explains: its runtime and its renditions' bitrates per kind."""

    runtime_s: int
    bitrates: dict[str, int]

    def expected_bytes(self, rendition: str) -> int | None:
        kinds = _RENDITIONS.get(rendition, ())
        rates = [self.bitrates[kind] for kind in kinds if kind in self.bitrates]
        if not rates:
            rates = list(self.bitrates.values())
        if self.runtime_s <= 0 or not rates:
            return None
        return self.runtime_s * max(rates) // 8


def explained(kind: str, title_id: UUID) -> Explained | None:
    """The runtime and bitrates of a movie or an episode, or None when it has neither."""
    from apps.catalog.models import Episode, Movie  # noqa: PLC0415 (catalog imports accounts)
    from apps.media.models import Rendition, RenditionStatus  # noqa: PLC0415

    if kind == TitleKind.MOVIE:
        movie = Movie.objects.filter(pk=title_id).values("runtime_min").first()
        if movie is None:
            return None
        runtime_min = movie["runtime_min"]
        renditions = Rendition.objects.filter(media_file__movie_id=title_id)
    elif kind == TitleKind.EPISODE:
        episode = (
            Episode.objects.filter(pk=title_id)
            .values("runtime_min", "season__series__episode_run_time")
            .first()
        )
        if episode is None:
            return None
        runtime_min = episode["runtime_min"] or episode["season__series__episode_run_time"]
        renditions = Rendition.objects.filter(media_file__episodes=title_id)
    else:
        return None
    rows = list(
        renditions.filter(
            status=RenditionStatus.READY,
            kind__in=_ALL_RENDITIONS,
            media_file__removed_at__isnull=True,
        )
        .values("kind")
        .annotate(bitrate=Max("bitrate"), duration=Max("duration_s"))
    )
    durations = [row["duration"] for row in rows if row["duration"] and row["kind"] != "hls_master"]
    runtime_s = round(max(durations)) if durations else (runtime_min or 0) * 60
    bitrates = {row["kind"]: int(row["bitrate"]) for row in rows if row["bitrate"]}
    return Explained(runtime_s=runtime_s, bitrates=bitrates)


def _network(ip: str | None) -> str:
    from apps.antisharing.services import network_of  # noqa: PLC0415 (services imports this)

    return (network_of(ip) or "") if ip else ""


def check(now: datetime | None = None) -> int:
    """Record sessions of the last day served far more than their title explains.

    Returns how many sessions are over the limit now (new or still growing)."""
    moment = now or timezone.now()
    limit = float(get_setting("antisharing.bytes_ratio"))
    floor = int(get_setting("antisharing.bytes_min_mb")) * MB
    sessions = (
        PlaybackSession.objects.filter(
            title_kind__in=(TitleKind.MOVIE, TitleKind.EPISODE),
            bytes_sent__gte=floor,
            started_at__gte=moment - 2 * WINDOW,
        )
        .filter(Q(ended_at__isnull=True) | Q(ended_at__gte=moment - WINDOW))
        .order_by("-bytes_sent")[:SESSIONS_PER_RUN]
    )
    titles: dict[tuple[str, UUID], Explained | None] = {}
    over = 0
    for session in sessions:
        key = (session.title_kind, session.title_id)
        if key not in titles:
            titles[key] = explained(*key)
        title = titles[key]
        expected = title.expected_bytes(session.rendition) if title is not None else None
        if not expected:
            continue
        ratio = session.bytes_sent / expected
        if ratio < limit:
            continue
        over += 1
        # `detected_at` stays the first time: the signal counts for a day from then.
        ExcessBytes.objects.update_or_create(
            session=session,
            create_defaults={
                "user_id": session.user_id,
                "device_id": session.device_id,
                "title_name": session.title_name[:255],
                "rendition": session.rendition,
                "network": _network(session.ip),
                "bytes_sent": session.bytes_sent,
                "expected_bytes": expected,
                "ratio": round(ratio, 2),
                "detected_at": moment,
            },
            defaults={
                "user_id": session.user_id,
                "device_id": session.device_id,
                "title_name": session.title_name[:255],
                "rendition": session.rendition,
                "network": _network(session.ip),
                "bytes_sent": session.bytes_sent,
                "expected_bytes": expected,
                "ratio": round(ratio, 2),
            },
        )
    return over


def recent_count(user_id: UUID | str, now: datetime) -> int:
    """Sessions over the limit in the last 24 hours: the `excess_bytes_24h` signal."""
    return ExcessBytes.objects.filter(user_id=user_id, detected_at__gte=now - WINDOW).count()


def recent_users(now: datetime) -> list[UUID]:
    return list(
        ExcessBytes.objects.filter(detected_at__gte=now - WINDOW)
        .order_by()
        .values_list("user_id", flat=True)
        .distinct()
    )
