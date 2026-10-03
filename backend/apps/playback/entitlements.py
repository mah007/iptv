"""Entitlements: what a customer may play right now (SPEC §7.4).

`build(user)` derives the SPEC §7.4 entitlement object from the customer's
manual access profile (`accounts.CustomerAccess`, ADR-0006). The commercial
slice adds subscriptions as a second source behind this same function; callers
never look at the source.

`refresh(user_id)` caches it in redis-state as `ent:{user_id}` (JSON) with
TTL = min(ends_at - now, 1 h): an active entitlement disappears the moment it
ends and `get` rebuilds it as expired. Users without an access profile (staff)
have no entitlement and no key. Services call `schedule_refresh` inside their
transaction, so Redis only ever reflects committed state.

Two deliberate choices on top of the SPEC field list:
- `categories` is null when every category is allowed, else the allowed ids;
- the rule lists hold the customer's own unexpired rules. Global rules
  (`AccessRule.user` null) are checked by playback directly (M7), so changing
  one never has to rewrite every customer's entitlement.
"""

import json
from datetime import UTC, datetime
from enum import StrEnum
from functools import partial
from typing import Literal, TypedDict, cast
from uuid import UUID

from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone

from apps.accounts.models import AccessRule, AccessRuleType, CustomerAccess, User, UserStatus
from apps.core.stores import state_redis

ENTITLEMENT_VERSION = 1
KEY_PREFIX = "ent:"
MAX_TTL_S = 3600

_IP_RULES = frozenset({AccessRuleType.IP_ALLOW, AccessRuleType.IP_DENY, AccessRuleType.CIDR_DENY})


class EntitlementStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    SUSPENDED = "suspended"
    DISABLED = "disabled"


class EntitlementRule(TypedDict):
    type: str
    value: str
    expires_at: str | None


class Entitlement(TypedDict):
    v: int
    user_id: str
    source: Literal["access_profile"]
    status: str
    ends_at: str | None
    grace_until: str | None
    max_streams: int
    max_devices: int
    max_quality: int
    policy: str
    categories: list[str] | None
    allow_movies: bool
    allow_series: bool
    allow_live: bool
    allow_download: bool
    country_rules: list[EntitlementRule]
    ip_rules: list[EntitlementRule]


def entitlement_key(user_id: UUID | str) -> str:
    return f"{KEY_PREFIX}{user_id}"


def status_of(user_status: str, expires_at: datetime | None, now: datetime) -> EntitlementStatus:
    """SPEC §7.4 checks 1 and 2: the account first, then the access period."""
    if user_status == UserStatus.DISABLED:
        return EntitlementStatus.DISABLED
    if user_status == UserStatus.SUSPENDED:
        return EntitlementStatus.SUSPENDED
    if expires_at is not None and expires_at <= now:
        return EntitlementStatus.EXPIRED
    return EntitlementStatus.ACTIVE


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


def _rule(rule: AccessRule) -> EntitlementRule:
    return {"type": rule.type, "value": rule.value, "expires_at": _iso(rule.expires_at)}


def _access_of(user: User) -> CustomerAccess | None:
    try:
        return user.access
    except CustomerAccess.DoesNotExist:
        return None


def build(user: User, *, now: datetime | None = None) -> Entitlement | None:
    """The entitlement object for `user`, or None if they have no access profile.

    Reads `user.access`, its categories and the user's access rules; prefetch them
    (see `load_user`) to keep this query-free.
    """
    access = _access_of(user)
    if access is None:
        return None
    moment = now or timezone.now()
    category_ids = sorted(str(category.pk) for category in access.categories.all())
    rules = [
        rule
        for rule in user.access_rules.all()
        if rule.expires_at is None or rule.expires_at > moment
    ]
    return {
        "v": ENTITLEMENT_VERSION,
        "user_id": str(user.pk),
        "source": "access_profile",
        "status": status_of(user.status, access.expires_at, moment).value,
        "ends_at": _iso(access.expires_at),
        "grace_until": None,
        "max_streams": access.max_streams,
        "max_devices": access.max_devices,
        "max_quality": access.max_quality,
        "policy": access.concurrency_policy,
        "categories": category_ids or None,
        "allow_movies": access.allow_movies,
        "allow_series": access.allow_series,
        "allow_live": access.allow_live,
        "allow_download": False,
        "country_rules": [_rule(rule) for rule in rules if rule.type not in _IP_RULES],
        "ip_rules": [_rule(rule) for rule in rules if rule.type in _IP_RULES],
    }


def ttl_seconds(entitlement: Entitlement, now: datetime) -> int:
    """min(ends_at - now, 1 h) while active; non-active entitlements live 1 h."""
    ends_at = entitlement["ends_at"]
    if entitlement["status"] != EntitlementStatus.ACTIVE or ends_at is None:
        return MAX_TTL_S
    remaining = (datetime.fromisoformat(ends_at) - now).total_seconds()
    return max(1, min(MAX_TTL_S, int(remaining)))


def load_user(user_id: UUID | str) -> User | None:
    """The user with everything `build` reads, in three queries."""
    return (
        User.objects.select_related("access")
        .prefetch_related(
            "access__categories",
            Prefetch("access_rules", queryset=AccessRule.objects.order_by("created_at")),
        )
        .filter(pk=user_id)
        .first()
    )


def refresh(user_id: UUID | str) -> Entitlement | None:
    """Rebuild the user's entitlement from the database and store it (or delete it)."""
    user = load_user(user_id)
    now = timezone.now()
    entitlement = build(user, now=now) if user is not None else None
    key = entitlement_key(user_id)
    if entitlement is None:
        state_redis().delete(key)
        return None
    payload = json.dumps(entitlement, separators=(",", ":"))
    state_redis().set(key, payload, ex=ttl_seconds(entitlement, now))
    return entitlement


def get(user_id: UUID | str) -> Entitlement | None:
    """The cached entitlement; rebuilt from the database when missing or expired."""
    raw = cast("bytes | None", state_redis().get(entitlement_key(user_id)))
    if raw is None:
        return refresh(user_id)
    return cast("Entitlement", json.loads(raw))


def schedule_refresh(user_id: UUID | str) -> None:
    """Refresh after the current transaction commits (at once outside one).

    Robust: if Redis is down the change still commits; the stale key ends with
    its TTL (at most an hour) and the next refresh repairs it.
    """
    transaction.on_commit(partial(refresh, user_id), robust=True)
