"""Dashboard KPIs (SPEC §8.3 Dashboard, §8.4): customers, devices, streams and queues.

A handful of aggregate queries, cached in redis-cache for 30 s (SPEC §8.4). When
Redis is unavailable the figures are computed on every call instead.
"""

import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from django.core.cache import cache
from django.db.models import Count, Q
from django.utils import timezone

from apps.accounts.models import Device, User, UserStatus
from apps.catalog.models import MatchReview, ReviewStatus
from apps.media.models import JobStatus, TranscodeJob
from apps.playback.models import PlaybackSession

logger = logging.getLogger(__name__)

CACHE_KEY = "dashboard:kpis:v2"
CACHE_TTL_S = 30
EXPIRING_WINDOW = timedelta(days=7)
FAILED_JOBS_WINDOW = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class Kpis:
    customers_total: int
    customers_active: int
    customers_expired: int
    customers_suspended: int
    expiring_7d: int
    devices_total: int
    devices_blocked: int
    streams_now: int
    stream_users_now: int
    reviews_open: int
    transcode_queued: int
    transcode_running: int
    transcode_failed_24h: int
    as_of: datetime


def compute_kpis(*, now: datetime | None = None) -> Kpis:
    moment = now or timezone.now()
    active = Q(status=UserStatus.ACTIVE)
    open_ended = Q(access__expires_at__isnull=True) | Q(access__expires_at__gt=moment)
    users = User.objects.filter(is_staff=False).aggregate(
        total=Count("pk"),
        active=Count("pk", filter=active & open_ended),
        expired=Count("pk", filter=active & Q(access__expires_at__lte=moment)),
        suspended=Count("pk", filter=Q(status=UserStatus.SUSPENDED)),
        expiring=Count(
            "pk",
            filter=active
            & Q(access__expires_at__gt=moment, access__expires_at__lte=moment + EXPIRING_WINDOW),
        ),
    )
    devices = Device.objects.filter(user__is_staff=False, revoked_at__isnull=True).aggregate(
        total=Count("pk"), blocked=Count("pk", filter=Q(blocked=True))
    )
    # Open rows: the sweeper closes a stalled stream within a couple of minutes.
    streams = PlaybackSession.objects.filter(ended_at__isnull=True).aggregate(
        total=Count("pk"), users=Count("user", distinct=True)
    )
    jobs = TranscodeJob.objects.aggregate(
        queued=Count("pk", filter=Q(status=JobStatus.QUEUED)),
        running=Count("pk", filter=Q(status=JobStatus.RUNNING)),
        failed=Count(
            "pk",
            filter=Q(status=JobStatus.FAILED, updated_at__gte=moment - FAILED_JOBS_WINDOW),
        ),
    )
    return Kpis(
        customers_total=users["total"],
        customers_active=users["active"],
        customers_expired=users["expired"],
        customers_suspended=users["suspended"],
        expiring_7d=users["expiring"],
        devices_total=devices["total"],
        devices_blocked=devices["blocked"],
        streams_now=streams["total"],
        stream_users_now=streams["users"],
        reviews_open=MatchReview.objects.filter(status=ReviewStatus.OPEN).count(),
        transcode_queued=jobs["queued"],
        transcode_running=jobs["running"],
        transcode_failed_24h=jobs["failed"],
        as_of=moment,
    )


def cached[T](
    key: str, compute: Callable[[], T], encode: Callable[[T], Any], decode: Callable[[Any], T]
) -> T:
    """`compute()` at most every 30 s through redis-cache; a cache outage only costs speed."""
    try:
        hit = cache.get(key)
    except Exception:  # a cache outage must not take the dashboard down
        logger.warning("dashboard cache unavailable", exc_info=True)
        hit = None
    if hit is not None:
        return decode(hit)
    fresh = compute()
    try:
        cache.set(key, encode(fresh), CACHE_TTL_S)
    except Exception:
        logger.warning("dashboard cache unavailable", exc_info=True)
    return fresh


def kpis() -> Kpis:
    """The KPIs, at most 30 s old."""
    return cached(CACHE_KEY, compute_kpis, asdict, lambda data: Kpis(**data))


def invalidate() -> None:
    cache.delete(CACHE_KEY)
