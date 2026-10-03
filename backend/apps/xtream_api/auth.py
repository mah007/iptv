"""Who is calling: Xtream credentials → account, status and catalog scope (SPEC §7.5, §11).

`authenticate` wraps `accounts.services.authenticate_xtream`, which does the
constant-time part: one query for the credential, Argon2id (or a 5-minute cached
success in redis-state under an HMAC of the pair), and one verification's worth
of work for unknown usernames. Every refusal (unknown user, wrong password,
revoked credential or device) returns None, which every endpoint turns into the
same response. Failures are counted per IP and per username, and past the limit
the answer is the same None without verifying (ratelimit.py).

A known account always signs in, so the app can say why it may not watch:
- `status` is "Active", "Expired" or "Disabled" (suspended or disabled accounts,
  blocked devices, devices awaiting approval, customers without access);
- catalog actions answer empty for anything but "Active".

Device activity (`last_seen`, `last_ip`, the credential's `last_used_at`) is
written at most once a minute per device.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import cast
from urllib.parse import urlsplit

import redis
from django.conf import settings
from django.db.models import Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.accounts import credentials as creds
from apps.accounts import services as account_services
from apps.accounts.models import Device, User, XtreamCredential
from apps.core.services import get_setting
from apps.core.stores import state_redis
from apps.playback import entitlements
from apps.playback.entitlements import Entitlement, EntitlementStatus
from apps.xtream_api import ratelimit
from apps.xtream_api.dto import Locale
from apps.xtream_api.payloads import AccountStatus, ServerInfo
from apps.xtream_api.source import CatalogScope

logger = logging.getLogger(__name__)

TOUCH_INTERVAL_S = 60
_TOUCH_PREFIX = "xc:seen:"


@dataclass(frozen=True, slots=True)
class XtreamAccount:
    user: User
    device: Device
    status: AccountStatus
    locale: Locale
    scope: CatalogScope
    expires_at: datetime | None
    max_connections: int

    @property
    def active(self) -> bool:
        return self.status == "Active"

    @property
    def user_id(self) -> str:
        return str(self.user.pk)


def authenticate(username: str, password: str, *, ip: str | None) -> XtreamAccount | None:
    """The account behind an Xtream username and password, or None (always the same None)."""
    if not username or not password:
        return None
    counted = ratelimit.failures(username, ip)
    if counted.blocked:  # the same refusal, without verifying (ratelimit.py)
        return None
    if "\x00" in username:  # Postgres rejects NUL; spend the same time as any unknown user
        creds.burn_verify(password)
        ratelimit.record_failure(username, ip)
        return None
    login = account_services.authenticate_xtream(username, password)
    if login is None:
        ratelimit.record_failure(username, ip)
        return None
    if counted.username:
        ratelimit.clear_username(username)
    _touch(login.credential, login.device, ip)
    user, device = login.user, login.device
    return account(user, device, entitlements.get(user.pk))


def account(user: User, device: Device, entitlement: Entitlement | None) -> XtreamAccount:
    if entitlement is None:  # no access profile, e.g. a staff member
        return XtreamAccount(
            user=user,
            device=device,
            status="Disabled",
            locale=locale_of(user),
            scope=CatalogScope(frozenset(), False, False, False),
            expires_at=None,
            max_connections=1,
        )
    categories = entitlement["categories"]
    ends_at = entitlement["ends_at"]
    return XtreamAccount(
        user=user,
        device=device,
        status=status_of(entitlement, device),
        locale=locale_of(user),
        scope=CatalogScope(
            categories=None if categories is None else frozenset(categories),
            allow_movies=entitlement["allow_movies"],
            allow_series=entitlement["allow_series"],
            allow_live=entitlement["allow_live"],
        ),
        expires_at=datetime.fromisoformat(ends_at) if ends_at else None,
        max_connections=entitlement["max_streams"],
    )


def status_of(
    entitlement: Entitlement, device: Device, now: datetime | None = None
) -> AccountStatus:
    """user_info.status: the entitlement's status, unless the device may not play."""
    if device.blocked or not device.approved or device.revoked_at is not None:
        return "Disabled"
    match entitlement["status"]:
        case EntitlementStatus.ACTIVE:
            ends_at = entitlement["ends_at"]
            moment = now or timezone.now()
            if ends_at is not None and datetime.fromisoformat(ends_at) <= moment:
                return "Expired"
            return "Active"
        case EntitlementStatus.EXPIRED:
            return "Expired"
        case _:
            return "Disabled"


def locale_of(user: User) -> Locale:
    return "ar" if user.locale == "ar" else "en"


def _touch(credential: XtreamCredential, device: Device, ip: str | None) -> None:
    """Record activity, at most once per TOUCH_INTERVAL_S per device."""
    try:
        first = state_redis().set(f"{_TOUCH_PREFIX}{device.pk}", 1, nx=True, ex=TOUCH_INTERVAL_S)
    except redis.RedisError:
        logger.warning("redis-state unavailable; device activity not recorded")
        return
    if not first:
        return
    now = timezone.now()
    fields: dict[str, object] = {"last_seen": now, "first_seen": Coalesce("first_seen", Value(now))}
    if ip:
        fields["last_ip"] = ip
    Device.objects.filter(pk=device.pk).update(**fields)
    XtreamCredential.objects.filter(pk=credential.pk).update(last_used_at=now)


def server_info() -> ServerInfo:
    """Where apps reach this server (server_info, M3U and play URLs).

    The `xtream.server_url` setting when set, else Django's public scheme and port
    on TV_HOST. The port of the scheme in use comes from that URL; the other one
    from the `xtream.port` / `xtream.https_port` settings.
    """
    configured = str(get_setting("xtream.server_url")).strip()
    parts = urlsplit(configured) if configured else None
    if parts is not None and parts.scheme in ("http", "https") and parts.hostname:
        scheme, host = parts.scheme, parts.hostname
        port = parts.port or (443 if scheme == "https" else 80)
    else:
        scheme, host, port = (
            str(settings.PUBLIC_SCHEME),
            str(settings.TV_HOST),
            int(settings.PUBLIC_PORT),
        )
    http_port = port if scheme == "http" else int(cast("int", get_setting("xtream.port")))
    https_port = port if scheme == "https" else int(cast("int", get_setting("xtream.https_port")))
    return ServerInfo(
        host=host,
        scheme="https" if scheme == "https" else "http",
        http_port=http_port,
        https_port=https_port,
    )
