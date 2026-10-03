"""Dashboard aggregates (SPEC §8.3 Dashboard, §8.4): customer and device KPIs.

Two aggregate queries, cached in redis-cache for 30 s (SPEC §8.4). When Redis
is unavailable the figures are computed on every call instead.
"""

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from django.core.cache import cache
from django.db.models import Count, Q
from django.utils import timezone

from apps.accounts.models import Device, User, UserStatus

logger = logging.getLogger(__name__)

CACHE_KEY = "dashboard:kpis:v1"
CACHE_TTL_S = 30
EXPIRING_WINDOW = timedelta(days=7)


@dataclass(frozen=True, slots=True)
class Kpis:
    customers_total: int
    customers_active: int
    customers_expired: int
    customers_suspended: int
    expiring_7d: int
    devices_total: int
    devices_blocked: int
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
    return Kpis(
        customers_total=users["total"],
        customers_active=users["active"],
        customers_expired=users["expired"],
        customers_suspended=users["suspended"],
        expiring_7d=users["expiring"],
        devices_total=devices["total"],
        devices_blocked=devices["blocked"],
        as_of=moment,
    )


def kpis() -> Kpis:
    """The KPIs, at most 30 s old."""
    try:
        cached: dict[str, Any] | None = cache.get(CACHE_KEY)
    except Exception:  # a cache outage must not take the dashboard down
        logger.warning("dashboard cache unavailable", exc_info=True)
        cached = None
    if cached is not None:
        return Kpis(**cached)
    fresh = compute_kpis()
    try:
        cache.set(CACHE_KEY, asdict(fresh), CACHE_TTL_S)
    except Exception:
        logger.warning("dashboard cache unavailable", exc_info=True)
    return fresh


def invalidate() -> None:
    cache.delete(CACHE_KEY)
