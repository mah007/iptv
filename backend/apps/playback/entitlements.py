"""Entitlements: what a customer may play right now (SPEC §7.4).

`build(user)` derives the SPEC §7.4 entitlement object from one of two sources;
callers never look at which (ADR-0012):
- the customer's subscription (`billing.Subscription`, its plan snapshot) once
  they have one that is not pending: the current one (active, grace or
  suspended), else the latest that ended, which makes the entitlement `expired`;
- otherwise their manual access profile (`accounts.CustomerAccess`, ADR-0006),
  e.g. customers created before billing, or managed only by hand.

`refresh(user_id)` caches it in redis-state as `ent:{user_id}` (JSON) with
TTL = min(end - now, 1 h), where the end is `grace_until` when there is one, else
`ends_at`: an active entitlement disappears the moment it ends and `get` rebuilds
it as expired. During grace the status stays `active`, with `ends_at` in the past
and `grace_until` ahead, which playback honours (SPEC §7.4 check 2). Users with
neither source (staff) have no entitlement and no key. Services call
`schedule_refresh` inside their transaction, so Redis only ever reflects
committed state.

Two deliberate choices on top of the SPEC field list:
- `categories` is null when every category is allowed, else the allowed ids;
- the rule lists hold the customer's own unexpired rules. Global rules
  (`AccessRule.user` null) are checked by playback directly (M7), so changing
  one never has to rewrite every customer's entitlement.
"""

import json
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from functools import partial
from typing import Any, Literal, TypedDict, cast
from uuid import UUID

from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone

from apps.accounts.models import AccessRule, AccessRuleType, CustomerAccess, User, UserStatus
from apps.billing.models import (
    CURRENT_STATUSES,
    ENDED_STATUSES,
    Subscription,
    SubscriptionStatus,
)
from apps.core.stores import state_redis

ENTITLEMENT_VERSION = 1
KEY_PREFIX = "ent:"
MAX_TTL_S = 3600

_IP_RULES = frozenset({AccessRuleType.IP_ALLOW, AccessRuleType.IP_DENY, AccessRuleType.CIDR_DENY})

type EntitlementSource = Literal["access_profile", "subscription"]


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
    source: EntitlementSource
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


def grace_until(subscription: Subscription) -> datetime | None:
    """The end of the grace period of a subscription still running, else None."""
    if subscription.grace_days <= 0 or subscription.status not in (
        SubscriptionStatus.ACTIVE,
        SubscriptionStatus.GRACE,
    ):
        return None
    return subscription.ends_at + timedelta(days=subscription.grace_days)


def subscription_status(
    user_status: str, subscription: Subscription, now: datetime
) -> EntitlementStatus:
    """Checks 1 and 2 for a subscription. Dates decide, not only the stored status, so
    an entitlement is right even before the 5-minute state job has caught up."""
    if user_status in (UserStatus.DISABLED, UserStatus.SUSPENDED):
        return status_of(user_status, None, now)
    if subscription.status == SubscriptionStatus.SUSPENDED:
        return EntitlementStatus.SUSPENDED
    if subscription.status not in (SubscriptionStatus.ACTIVE, SubscriptionStatus.GRACE):
        return EntitlementStatus.EXPIRED
    end = grace_until(subscription) or subscription.ends_at
    if end <= now or subscription.starts_at > now:
        return EntitlementStatus.EXPIRED
    return EntitlementStatus.ACTIVE


def governing_subscription(subscriptions: Iterable[Subscription]) -> Subscription | None:
    """The subscription that decides the entitlement: the current one, else the one that
    ended last; None when every subscription is still pending (or there is none)."""
    current: Subscription | None = None
    ended: Subscription | None = None
    for subscription in subscriptions:
        if subscription.status in CURRENT_STATUSES:
            if current is None or subscription.ends_at > current.ends_at:
                current = subscription
        elif subscription.status in ENDED_STATUSES and (
            ended is None or subscription.ends_at > ended.ends_at
        ):
            ended = subscription
    return current or ended


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


def _rule(rule: AccessRule) -> EntitlementRule:
    return {"type": rule.type, "value": rule.value, "expires_at": _iso(rule.expires_at)}


def _access_of(user: User) -> CustomerAccess | None:
    try:
        return user.access
    except CustomerAccess.DoesNotExist:
        return None


def _rules(user: User, moment: datetime) -> tuple[list[EntitlementRule], list[EntitlementRule]]:
    """The user's unexpired (country rules, IP rules)."""
    rules = [
        rule
        for rule in user.access_rules.all()
        if rule.expires_at is None or rule.expires_at > moment
    ]
    return (
        [_rule(rule) for rule in rules if rule.type not in _IP_RULES],
        [_rule(rule) for rule in rules if rule.type in _IP_RULES],
    )


def _from_subscription(user: User, subscription: Subscription, moment: datetime) -> Entitlement:
    snapshot: dict[str, Any] = subscription.plan_snapshot
    country_rules, ip_rules = _rules(user, moment)
    category_ids = sorted(str(value) for value in snapshot.get("category_ids") or [])
    return {
        "v": ENTITLEMENT_VERSION,
        "user_id": str(user.pk),
        "source": "subscription",
        "status": subscription_status(user.status, subscription, moment).value,
        "ends_at": _iso(subscription.ends_at),
        "grace_until": _iso(grace_until(subscription)),
        "max_streams": int(snapshot["max_streams"]),
        "max_devices": int(snapshot["max_devices"]),
        "max_quality": int(snapshot["max_quality"]),
        "policy": str(snapshot["concurrency_policy"]),
        "categories": category_ids or None,
        "allow_movies": bool(snapshot["allow_movies"]),
        "allow_series": bool(snapshot["allow_series"]),
        "allow_live": bool(snapshot["allow_live"]),
        "allow_download": bool(snapshot.get("allow_download", False)),
        "country_rules": country_rules,
        "ip_rules": ip_rules,
    }


def _from_access_profile(user: User, access: CustomerAccess, moment: datetime) -> Entitlement:
    category_ids = sorted(str(category.pk) for category in access.categories.all())
    country_rules, ip_rules = _rules(user, moment)
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
        "country_rules": country_rules,
        "ip_rules": ip_rules,
    }


def build(user: User, *, now: datetime | None = None) -> Entitlement | None:
    """The entitlement object for `user`, or None if they have neither a subscription
    nor an access profile.

    Reads `user.subscriptions`, `user.access`, its categories and the user's access
    rules; prefetch them (see `load_user`) to keep this query-free.
    """
    moment = now or timezone.now()
    subscription = governing_subscription(user.subscriptions.all())
    if subscription is not None:
        return _from_subscription(user, subscription, moment)
    access = _access_of(user)
    if access is None:
        return None
    return _from_access_profile(user, access, moment)


def ttl_seconds(entitlement: Entitlement, now: datetime) -> int:
    """min(end - now, 1 h) while active, the end being grace_until or else ends_at;
    non-active entitlements live 1 h."""
    end = entitlement.get("grace_until") or entitlement["ends_at"]
    if entitlement["status"] != EntitlementStatus.ACTIVE or end is None:
        return MAX_TTL_S
    remaining = (datetime.fromisoformat(end) - now).total_seconds()
    return max(1, min(MAX_TTL_S, int(remaining)))


def load_user(user_id: UUID | str) -> User | None:
    """The user with everything `build` reads, in four queries."""
    return (
        User.objects.select_related("access")
        .prefetch_related(
            "access__categories",
            Prefetch("access_rules", queryset=AccessRule.objects.order_by("created_at")),
            Prefetch("subscriptions", queryset=Subscription.objects.order_by("-ends_at")),
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


def device_limit(user_id: UUID | str) -> int | None:
    """How many devices the customer may have (the plan's or the profile's
    `max_devices`), or None for users without an entitlement.

    Built from the database, never the cache: it runs inside device writes, which
    must not store `ent:{user}` mid-transaction (writers refresh it after commit).
    """
    user = load_user(user_id)
    entitlement = build(user) if user is not None else None
    return entitlement["max_devices"] if entitlement is not None else None


def schedule_refresh(user_id: UUID | str) -> None:
    """Refresh after the current transaction commits (at once outside one).

    Robust: if Redis is down the change still commits; the stale key ends with
    its TTL (at most an hour) and the next refresh repairs it.
    """
    transaction.on_commit(partial(refresh, user_id), robust=True)
