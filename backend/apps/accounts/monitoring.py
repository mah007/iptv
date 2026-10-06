"""Admin sign-in to the monitoring UIs on grafana.<domain> (SPEC §13, §14; ADR-0018).

Grafana, Prometheus (/prometheus) and Alertmanager (/alertmanager) live on their own
host, so a weakness in any of them can never act on the admin API's same-origin
session. Admins reach them through the admin itself, which already demands MFA:

1. `issue_ticket`: the admin's Monitoring page (session, MFA, CSRF, `monitoring.view`)
   gets a one-time ticket, kept in redis-state for 60 s under its SHA-256 only, and
   the browser carries it to grafana.<domain>/_sso/callback.
2. `redeem_ticket`: the callback consumes the ticket (GETDEL: a second use finds
   nothing), checks the admin again and opens a monitoring session for
   `security.monitoring_session_hours`: a random id in a host-only, HttpOnly cookie
   (`__Host-` prefixed over HTTPS), stored hashed in redis-state.
3. `authorize`: Traefik's forward-auth asks /internal/monitoring-auth about every
   request to the host. The answer comes from Redis; the admin's status and
   permissions are read from PostgreSQL again at most every 60 s, so suspending an
   admin or removing `monitoring.view` ends their access within a minute. A yes
   carries the identity Grafana's auth proxy signs in (owners as Grafana admins,
   everyone else as viewers).
4. `end_session`: Grafana's sign-out link (/_sso/logout) deletes the session.

Tickets and session ids never reach a log: the request log never prints query
strings, and only hashes are stored.
"""

import hashlib
import json
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any, Final, cast
from urllib.parse import quote, urlencode

import redis
from django.conf import settings
from django.core.exceptions import ValidationError

from apps.accounts.models import User, UserStatus
from apps.accounts.rbac import ALL_PERMISSIONS, permission_codes
from apps.audit import services as audit
from apps.core.services import get_setting
from apps.core.stores import state_redis
from config.origins import origin

logger = logging.getLogger(__name__)

PERMISSION: Final = "monitoring.view"
TICKET_TTL_S: Final = 60
RECHECK_S: Final = 60
TICKET_PREFIX: Final = "mon:ticket:"
SESSION_PREFIX: Final = "mon:sess:"
CALLBACK_PATH: Final = "/_sso/callback"
ADMIN_PAGE: Final = "/monitoring"
MAX_NEXT_LENGTH: Final = 512
MAX_SECRET_LENGTH: Final = 128
GRAFANA_ADMIN: Final = "Admin"
GRAFANA_VIEWER: Final = "Viewer"


@dataclass(frozen=True, slots=True)
class Identity:
    """Who Grafana signs in: the X-WEBAUTH-* headers of an authorised request."""

    login: str
    name: str
    role: str


def cookie_name() -> str:
    """`__Host-` needs Secure (HTTPS), so plain-HTTP dev uses the bare name."""
    return "__Host-iptv_monitor" if settings.SESSION_COOKIE_SECURE else "iptv_monitor"


def monitoring_origin() -> str:
    return origin(settings.PUBLIC_SCHEME, settings.GRAFANA_HOST, settings.PUBLIC_PORT)


def admin_origin() -> str:
    return origin(settings.PUBLIC_SCHEME, settings.ADMIN_HOST, settings.PUBLIC_PORT)


def safe_next(value: str | None) -> str:
    """A path on the monitoring host to land on, or "/". Never another site."""
    if not value or len(value) > MAX_NEXT_LENGTH:
        return "/"
    if not value.startswith("/") or value.startswith(("//", "/\\")):
        return "/"
    if any(ord(char) < 0x20 or ord(char) == 0x7F or char == "\\" for char in value):
        return "/"
    if value.startswith("/_sso/"):
        return "/"
    return value


def admin_sign_in_url(next_path: str = "/") -> str:
    """The admin's Monitoring page, which signs the admin in and comes back with a ticket."""
    target = safe_next(next_path)
    query = f"?{urlencode({'next': target})}" if target != "/" else ""
    return f"{admin_origin()}{ADMIN_PAGE}{query}"


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def can_monitor(user: User) -> bool:
    """Active staff holding monitoring.view (owners hold everything)."""
    return (
        user.is_active
        and user.is_staff
        and user.status == UserStatus.ACTIVE
        and PERMISSION in permission_codes(user)
    )


def _identity(user: User) -> Identity:
    codes = permission_codes(user)
    role = GRAFANA_ADMIN if codes >= ALL_PERMISSIONS else GRAFANA_VIEWER
    name = (user.name or user.get_username()).strip()
    try:
        name.encode("latin-1")
    except UnicodeEncodeError:
        # HTTP headers are Latin-1; Grafana shows the login instead of a mangled name.
        name = user.get_username()
    return Identity(login=user.get_username(), name=name[:150], role=role)


def issue_ticket(user: User, next_path: str = "/", *, ip: str | None = None) -> str:
    """A single-use URL that opens a monitoring session for `user` (who must qualify)."""
    if not can_monitor(user):
        msg = "This admin may not open monitoring."
        raise PermissionError(msg)
    raw = secrets.token_urlsafe(32)
    record = json.dumps({"user": str(user.pk), "next": safe_next(next_path)})
    state_redis().set(TICKET_PREFIX + _hash(raw), record, ex=TICKET_TTL_S)
    audit.record("monitoring.sign_in", actor=user, ip=ip)
    return f"{monitoring_origin()}{CALLBACK_PATH}?ticket={quote(raw, safe='')}"


def redeem_ticket(raw: str | None) -> tuple[str, str] | None:
    """Consume a ticket: (new session id, path to land on), or None when it is no good."""
    if not raw or len(raw) > MAX_SECRET_LENGTH:
        return None
    try:
        stored = cast("bytes | None", state_redis().getdel(TICKET_PREFIX + _hash(raw)))
    except redis.RedisError:
        logger.warning("monitoring: redis-state unavailable while redeeming a ticket")
        return None
    if stored is None:
        return None
    try:
        record = json.loads(stored)
        user = User.objects.get(pk=str(record["user"]))
    except (ValueError, KeyError, TypeError, ValidationError, User.DoesNotExist):
        return None
    if not can_monitor(user):
        return None
    session = secrets.token_urlsafe(32)
    hours = int(get_setting("security.monitoring_session_hours"))
    identity = _identity(user)
    now = time.time()
    data = {
        "user": str(user.pk),
        "login": identity.login,
        "name": identity.name,
        "role": identity.role,
        "checked": now,
    }
    state_redis().set(SESSION_PREFIX + _hash(session), json.dumps(data), ex=hours * 3600)
    return session, safe_next(record.get("next"))


def _load_session(key: str) -> dict[str, Any] | None:
    """The stored session, or None when missing, unreadable or Redis is down."""
    client = state_redis()
    try:
        stored = cast("bytes | None", client.get(key))
    except redis.RedisError:
        logger.warning("monitoring: redis-state unavailable; access refused")
        return None
    if stored is None:
        return None
    try:
        data = json.loads(stored)
        float(data["checked"])
        if not all(isinstance(data.get(field), str) for field in ("login", "name", "role")):
            raise TypeError
    except (ValueError, KeyError, TypeError):
        client.delete(key)
        return None
    return cast("dict[str, Any]", data)


def authorize(raw: str | None, *, now: float | None = None) -> Identity | None:
    """The identity behind a monitoring cookie, or None to send the browser to sign in."""
    if not raw or len(raw) > MAX_SECRET_LENGTH:
        return None
    key = SESSION_PREFIX + _hash(raw)
    data = _load_session(key)
    if data is None:
        return None
    moment = time.time() if now is None else now
    if moment - float(data["checked"]) < RECHECK_S:
        return Identity(login=data["login"], name=data["name"], role=data["role"])
    try:
        user = User.objects.filter(pk=str(data.get("user", ""))).first()
    except (ValueError, ValidationError):
        user = None
    if user is None or not can_monitor(user):
        state_redis().delete(key)
        return None
    identity = _identity(user)
    data.update(login=identity.login, name=identity.name, role=identity.role, checked=moment)
    state_redis().set(key, json.dumps(data), keepttl=True, xx=True)
    return identity


def end_session(raw: str | None) -> None:
    if raw and len(raw) <= MAX_SECRET_LENGTH:
        try:
            state_redis().delete(SESSION_PREFIX + _hash(raw))
        except redis.RedisError:
            logger.warning("monitoring: redis-state unavailable while signing out")
