"""Customer sign-in for the customer API (SPEC §9, §10 Auth, §11; ADR-0013).

Two transports over the same endpoints and services:

- **The portal** (`app.<domain>/api/v1`, `config.urls_portal`): a same-origin session
  cookie plus CSRF, like the admin (ADR-0004). HttpOnly, SameSite=Lax, host-only, and
  `security.customer_session_days` of idle time.
- **Apps** (`api.<domain>/api/v1`, `config.urls_api`): `Authorization: Bearer` access
  tokens with rotating refresh tokens (`customer_tokens`). No cookies, so no CSRF.

Each authentication class answers only on its own host, so a portal cookie is never
accepted on api.<domain> and a bearer token never on app.<domain>.

Sign-in takes a username, email or phone number with the password. django-axes
counts failures per username and client IP (exponential cool-off, never permanent,
`apps.accounts.lockout`), and a per-IP counter in redis-state bounds guessing across
usernames and password-reset requests. Staff accounts never sign in here: admins use
the admin host with MFA. Every refusal reads the same (INVALID_CREDENTIALS).

Passwords are set through single-use, time-limited links: "forgot password" (emailed,
answered identically whether or not the account exists) and the admin's invitation for
customers created without a usable password. Links are Django's password-reset tokens
(HMAC over the user's password hash and last sign-in, so they stop working once used);
email goes through a pluggable sender (`set_password_link_sender`).
"""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final, Literal, cast
from urllib.parse import urlencode
from uuid import UUID

import redis
import structlog
from django.conf import settings
from django.contrib.auth import authenticate, login, logout, user_logged_in
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.utils.encoding import force_bytes, force_str
from django.utils.http import base36_to_int, urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework import authentication, exceptions
from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.views import APIView

from apps.accounts import customer_tokens, services
from apps.accounts.customer_tokens import AccessGrant, RefreshOutcome, TokenPair
from apps.accounts.lockout import LOGIN_USERNAME_ATTR
from apps.accounts.models import Device, DeviceKind, User, UserStatus
from apps.accounts.validators import normalize_phone
from apps.audit import services as audit
from apps.core.authentication import WWW_AUTHENTICATE, SessionAuthentication
from apps.core.errors import ErrorCode, ProblemError, field_error
from apps.core.http import client_ip
from apps.core.services import get_setting
from apps.core.stores import state_redis
from config.origins import origin

logger = logging.getLogger(__name__)

PORTAL_URLCONF: Final = "config.urls_portal"
API_URLCONF: Final = "config.urls_api"
BEARER_REALM: Final = 'Bearer realm="api"'
MODEL_BACKEND: Final = "django.contrib.auth.backends.ModelBackend"
#: The session's browser device (created at its first playback).
SESSION_DEVICE_KEY: Final = "_customer_web_device"
#: The portal page that sets a password from a link (`?uid=&token=`).
PASSWORD_PAGE: Final = "/reset-password"  # noqa: S105 (a page path)
RATE_WINDOW_S: Final = 900
_RESET_EMAIL_WINDOW_S: Final = 3600

type Purpose = Literal["reset", "invite"]


# --- Hosts and authentication -----------------------------------------------------------------


def _urlconf(request: HttpRequest | Request) -> str | None:
    raw = request._request if isinstance(request, Request) else request
    return cast("str | None", getattr(raw, "urlconf", None))


def on_portal(request: HttpRequest | Request) -> bool:
    return _urlconf(request) == PORTAL_URLCONF


def on_api(request: HttpRequest | Request) -> bool:
    return _urlconf(request) == API_URLCONF


class PortalSessionAuthentication(SessionAuthentication):
    """The portal's session cookie (CSRF enforced on unsafe methods), on app.<domain> only."""

    def authenticate(self, request: Request) -> tuple[Any, None] | None:
        if not on_portal(request):
            return None
        return super().authenticate(request)


class BearerTokenAuthentication(authentication.BaseAuthentication):
    """`Authorization: Bearer <access token>` on api.<domain> only (`customer_tokens`)."""

    def authenticate(self, request: Request) -> tuple[User, AccessGrant] | None:
        if not on_api(request):
            return None
        parts = authentication.get_authorization_header(request).split()
        if not parts or parts[0].lower() != b"bearer":
            return None
        if len(parts) != 2:
            raise exceptions.AuthenticationFailed("Malformed Authorization header.")
        try:
            token = parts[1].decode("ascii")
        except UnicodeDecodeError:
            raise exceptions.AuthenticationFailed("Malformed Authorization header.") from None
        grant = customer_tokens.authenticate(token)
        user = User.objects.filter(pk=grant.user_id).first() if grant is not None else None
        if grant is None or user is None or not _is_customer(user):
            raise exceptions.AuthenticationFailed("The access token is invalid or has expired.")
        structlog.contextvars.bind_contextvars(user_id=str(user.pk))
        return user, grant

    def authenticate_header(self, request: Request) -> str:
        return BEARER_REALM if on_api(request) else WWW_AUTHENTICATE


#: Every customer endpoint: bearer first, so 401s on api.<domain> name the Bearer scheme.
CUSTOMER_AUTHENTICATION: Final = (BearerTokenAuthentication, PortalSessionAuthentication)


def _is_customer(user: object) -> bool:
    return (
        isinstance(user, User)
        and user.is_authenticated
        and user.is_active
        and not user.is_staff
        and user.status != UserStatus.DISABLED
    )


class IsCustomer(BasePermission):
    """A signed-in customer (never staff, never a disabled account)."""

    def has_permission(self, request: Request, view: APIView) -> bool:
        return _is_customer(request.user)


def customer_of(request: Request) -> User:
    """The signed-in customer (views guarded by IsCustomer)."""
    return cast("User", request.user)


class CsrfOnPortal(SessionAuthentication):
    """Exposes DRF's CSRF check for anonymous portal requests (sign-in, password links)."""

    def check(self, request: Request) -> None:
        if on_portal(request):
            self.enforce_csrf(request)


# --- Rate limits ------------------------------------------------------------------------------


def _rate_key(kind: str, value: str) -> str:
    return f"capi:rl:{kind}:{value}"


def _ip_limit() -> int:
    return int(cast("int", get_setting("security.customer_auth_requests_per_ip")))


def _check_ip(ip: str | None) -> None:
    """Refuse with RATE_LIMITED (and Retry-After) while the IP is over its budget."""
    if not ip:
        return
    key = _rate_key("ip", ip)
    try:
        count = cast("bytes | None", state_redis().get(key))
        if count is not None and int(count) >= _ip_limit():
            ttl = int(cast("int", state_redis().ttl(key)))
            raise exceptions.Throttled(wait=max(ttl, 1))
    except redis.RedisError:
        logger.warning("redis-state unavailable; customer sign-ins not rate limited")


def _hit(key: str, window_s: int) -> int:
    try:
        pipe = state_redis().pipeline(transaction=True)
        pipe.incr(key)
        pipe.expire(key, window_s, nx=True)
        count, _ = pipe.execute()
        return int(count)
    except redis.RedisError:
        logger.warning("redis-state unavailable; customer sign-ins not rate limited")
        return 0


def _count_ip(ip: str | None) -> None:
    if ip:
        _hit(_rate_key("ip", ip), RATE_WINDOW_S)


# --- Finding the customer ---------------------------------------------------------------------

_PHONE_LIKE = re.compile(r"\+?[0-9][0-9 ()-]{5,}")


def find_customer(login_value: str) -> User | None:
    """The customer a username, email or phone number names; None if none or ambiguous."""
    value = login_value.strip()
    if not value:
        return None
    customers = User.objects.filter(is_staff=False)
    if "@" in value:
        match = list(customers.filter(email__iexact=value)[:2])
    elif _PHONE_LIKE.fullmatch(value):
        try:
            phone = normalize_phone(value)
        except ValidationError:
            phone = ""
        match = list(customers.filter(phone=phone)[:2]) if phone else []
        if not match:
            match = list(customers.filter(username__iexact=value)[:2])
    else:
        match = list(customers.filter(username__iexact=value)[:2])
    return match[0] if len(match) == 1 else None


def _invalid() -> ProblemError:
    return ProblemError(
        ErrorCode.INVALID_CREDENTIALS, "The username, email, phone or password is incorrect."
    )


def check_password(request: HttpRequest, login_value: str, password: str) -> User:
    """The customer these credentials name. Raises INVALID_CREDENTIALS, ACCOUNT_LOCKED or
    RATE_LIMITED."""
    ip = client_ip(request)
    _check_ip(ip)
    customer = find_customer(login_value)
    username = customer.username if customer is not None else login_value.strip()[:150]
    setattr(request, LOGIN_USERNAME_ATTR, username)
    user = authenticate(request, username=username, password=password)
    if getattr(request, "axes_locked_out", False):
        raise ProblemError(
            ErrorCode.ACCOUNT_LOCKED, "Too many failed sign-in attempts. Try again later."
        )
    if not _is_customer(user):
        _count_ip(ip)
        raise _invalid()
    return cast("User", user)


def _record_sign_in(user: User, ip: str | None) -> None:
    if ip and user.last_login_ip != ip:
        user.last_login_ip = ip
        user.save(update_fields=["last_login_ip", "updated_at"])


# --- The portal (session) ---------------------------------------------------------------------


def portal_login(request: HttpRequest, login_value: str, password: str) -> User:
    user = check_password(request, login_value, password)
    login(request, user, backend=MODEL_BACKEND)  # a new session key
    days = int(cast("int", get_setting("security.customer_session_days")))
    request.session.set_expiry(days * 86400)
    _record_sign_in(user, client_ip(request))
    return user


def portal_logout(request: HttpRequest) -> None:
    """End the session and retire its browser device (its playback stops)."""
    user = getattr(request, "user", None)
    device_id = request.session.get(SESSION_DEVICE_KEY) if hasattr(request, "session") else None
    if isinstance(user, User) and user.is_authenticated and device_id:
        device = Device.objects.filter(pk=device_id, user=user, kind=DeviceKind.WEB).first()
        if device is not None:
            services.revoke_device(device, actor=user, ip=client_ip(request))
    logout(request)


# --- Apps (bearer tokens) ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AppSignIn:
    user: User
    device: Device
    tokens: TokenPair


def app_login(
    request: HttpRequest, login_value: str, password: str, *, device_name: str = ""
) -> AppSignIn:
    """Check the password and start a token family on a new `app` device."""
    user = check_password(request, login_value, password)
    ip = client_ip(request)
    now = timezone.now()
    device = Device.objects.create(
        user=user,
        kind=DeviceKind.APP,
        name=device_name.strip()[:100] or "App",
        approved=True,
        first_seen=now,
        last_seen=now,
        last_ip=ip,
    )
    pair = customer_tokens.issue(user.pk, device.pk)
    # last_login and django-axes' reset on success, as a session sign-in would do.
    user_logged_in.send(sender=User, request=request, user=user)
    _record_sign_in(user, ip)
    return AppSignIn(user=user, device=device, tokens=pair)


def _retire_device(device_id: UUID | None, *, actor: User | None, ip: str | None) -> None:
    device = Device.objects.filter(pk=device_id).first() if device_id else None
    if device is not None and device.revoked_at is None:
        services.revoke_device(device, actor=actor, ip=ip)


def app_refresh(request: HttpRequest, refresh_token: str) -> TokenPair:
    """Rotate the pair. A reused refresh token revokes its family and device."""
    rotation = customer_tokens.rotate(refresh_token)
    if rotation.outcome is RefreshOutcome.OK and rotation.pair is not None:
        user = User.objects.filter(pk=rotation.user_id).first() if rotation.user_id else None
        if user is not None and _is_customer(user):
            if rotation.device_id is not None:
                Device.objects.filter(pk=rotation.device_id).update(last_seen=timezone.now())
            return rotation.pair
        customer_tokens.revoke_family(rotation.pair.family)
    if rotation.outcome is RefreshOutcome.REUSE:
        ip = client_ip(request)
        user = User.objects.filter(pk=rotation.user_id).first() if rotation.user_id else None
        with transaction.atomic():
            if user is not None:
                audit.record("auth.token_reuse", actor=None, target=user, ip=ip)
            _retire_device(rotation.device_id, actor=None, ip=ip)
        logger.warning("customer refresh token reused; family revoked")
    raise ProblemError(
        ErrorCode.NOT_AUTHENTICATED, "The refresh token is invalid or has expired.", status=401
    )


def app_logout(request: HttpRequest, user: User, grant: AccessGrant) -> None:
    device_id = customer_tokens.revoke_family(grant.family)
    _retire_device(device_id or grant.device_id, actor=user, ip=client_ip(request))


def end_app_sign_ins(user: User) -> None:
    """Revoke every token family of the user and retire their app devices."""
    for device_id in customer_tokens.revoke_user(user.pk):
        _retire_device(device_id, actor=None, ip=None)


# --- The device a request plays on ------------------------------------------------------------

_BROWSERS: Final = (
    ("Edg/", "Edge"),
    ("OPR/", "Opera"),
    ("SamsungBrowser/", "Samsung Internet"),
    ("Firefox/", "Firefox"),
    ("Chrome/", "Chrome"),
    ("Safari/", "Safari"),
)
_SYSTEMS: Final = (
    ("Web0S", "webOS TV"),
    ("Tizen", "Tizen TV"),
    ("Android", "Android"),
    ("iPhone", "iPhone"),
    ("iPad", "iPad"),
    ("Mac OS X", "macOS"),
    ("Windows", "Windows"),
    ("CrOS", "ChromeOS"),
    ("Linux", "Linux"),
)


def browser_name(user_agent: str) -> str:
    """ "Chrome on Windows", from the User-Agent; "Web browser" when unknown."""
    browser = next((name for token, name in _BROWSERS if token in user_agent), "")
    system = next((name for token, name in _SYSTEMS if token in user_agent), "")
    if browser and system:
        return f"{browser} on {system}"
    return browser or system or "Web browser"


def device_for(request: Request, user: User) -> Device:
    """The device this request plays on: the token's `app` device on api.<domain>, else
    the session's browser device, created at the session's first playback."""
    ip = client_ip(request)
    now = timezone.now()
    if isinstance(request.auth, AccessGrant):
        device = Device.objects.filter(pk=request.auth.device_id, user=user).first()
        if device is None:
            raise exceptions.AuthenticationFailed("The access token is invalid or has expired.")
    else:
        session = request._request.session
        device_id = session.get(SESSION_DEVICE_KEY)
        device = (
            Device.objects.filter(pk=device_id, user=user, kind=DeviceKind.WEB).first()
            if device_id
            else None
        )
        if device is None or device.revoked_at is not None:
            agent = request.META.get("HTTP_USER_AGENT", "")
            device = Device.objects.create(
                user=user,
                kind=DeviceKind.WEB,
                name=browser_name(agent),
                approved=True,
                first_seen=now,
            )
            session[SESSION_DEVICE_KEY] = str(device.pk)
    Device.objects.filter(pk=device.pk).update(last_seen=now, last_ip=ip, updated_at=now)
    return device


# --- Password links ---------------------------------------------------------------------------


class _LinkTokens(PasswordResetTokenGenerator):
    """Django's password-reset token with our own salt and lifetime per purpose.

    The HMAC covers the password hash and the last sign-in, so a link stops working
    once the password is set (or the customer signs in). `check_token` is Django's,
    reading the lifetime from the purpose's setting instead of PASSWORD_RESET_TIMEOUT.
    """

    def __init__(self, purpose: Purpose) -> None:
        super().__init__()
        self.purpose = purpose
        self.key_salt = f"apps.accounts.customer_auth.{purpose}"

    def lifetime_s(self) -> int:
        if self.purpose == "invite":
            return int(cast("int", get_setting("security.password_invite_ttl_days"))) * 86400
        return int(cast("int", get_setting("security.password_reset_ttl_min"))) * 60

    def check_token(self, user: Any, token: str | None) -> bool:
        if not (user and token):
            return False
        try:
            ts_b36, _hash = token.split("-")
            ts = base36_to_int(ts_b36)
        except ValueError:
            return False
        for secret in [self.secret, *self.secret_fallbacks]:
            if constant_time_compare(self._make_token_with_timestamp(user, ts, secret), token):
                break
        else:
            return False
        return (self._num_seconds(self._now()) - ts) <= self.lifetime_s()


def _tokens(purpose: Purpose) -> _LinkTokens:
    return _LinkTokens(purpose)


@dataclass(frozen=True, slots=True)
class PasswordLink:
    """A single-use link to set a password; `url` carries the secret, never log it."""

    user: User
    purpose: Purpose
    url: str
    expires_at: datetime


type PasswordLinkSender = Callable[[PasswordLink], bool]
_sender: PasswordLinkSender | None = None


def set_password_link_sender(sender: PasswordLinkSender | None) -> None:
    """Install the function that emails password links (notifications); None removes it."""
    global _sender  # noqa: PLW0603 (one process-wide hook, set at app start)
    _sender = sender


def _no_sender(link: PasswordLink) -> bool:
    logger.warning("password link not emailed: no email sender is installed")
    return False


def send_password_link(link: PasswordLink) -> bool:
    """Email the link; False when it could not be handed to a sender."""
    if not link.user.email:
        return False
    try:
        return bool((_sender or _no_sender)(link))
    except Exception:
        logger.exception("password link sender failed")
        return False


def portal_origin() -> str:
    return origin(settings.PUBLIC_SCHEME, settings.APP_HOST, settings.PUBLIC_PORT)


def make_password_link(user: User, purpose: Purpose) -> PasswordLink:
    generator = _tokens(purpose)
    query = {
        "uid": urlsafe_base64_encode(force_bytes(user.pk)),
        "token": generator.make_token(user),
    }
    if purpose == "invite":
        query["welcome"] = "1"
    return PasswordLink(
        user=user,
        purpose=purpose,
        url=f"{portal_origin()}{PASSWORD_PAGE}?{urlencode(query)}",
        expires_at=timezone.now() + timedelta(seconds=generator.lifetime_s()),
    )


def request_password_reset(request: HttpRequest, login_value: str) -> None:
    """ "Forgot password": email a reset link if the login names a customer with an email.

    The caller answers the same whatever happens here, so nobody learns whether an
    account exists. Counted against the IP's budget; each account receives at most
    `security.password_reset_emails_per_hour` emails.
    """
    ip = client_ip(request)
    _check_ip(ip)
    _count_ip(ip)
    user = find_customer(login_value)
    if user is None or not user.email or user.status == UserStatus.DISABLED:
        return
    sent = _hit(_rate_key("reset", str(user.pk)), _RESET_EMAIL_WINDOW_S)
    if sent > int(cast("int", get_setting("security.password_reset_emails_per_hour"))):
        return
    send_password_link(make_password_link(user, "reset"))


def _bad_link() -> ProblemError:
    return ProblemError(
        ErrorCode.VALIDATION_ERROR,
        "This link is invalid or has expired.",
        field_errors={
            "token": [field_error("This link is invalid or has expired.", code="invalid_token")]
        },
    )


def _link_user(uid: str) -> User | None:
    try:
        pk = UUID(force_str(urlsafe_base64_decode(uid)))
    except (ValueError, TypeError, UnicodeDecodeError):
        return None
    return User.objects.filter(pk=pk, is_staff=False).exclude(status=UserStatus.DISABLED).first()


def reset_password(request: HttpRequest, uid: str, token: str, password: str) -> User:
    """Set the password from a reset or invitation link (single use). Ends app sign-ins
    and, through the session auth hash, every other portal session."""
    ip = client_ip(request)
    _check_ip(ip)
    user = _link_user(uid)
    purpose: Purpose | None = None
    if user is not None:
        purposes: tuple[Purpose, ...] = ("reset", "invite")
        purpose = next((p for p in purposes if _tokens(p).check_token(user, token)), None)
    if user is None or purpose is None:
        _count_ip(ip)
        raise _bad_link()
    try:
        validate_password(password, user)
    except ValidationError as exc:
        raise ProblemError(
            ErrorCode.VALIDATION_ERROR,
            exc.messages[0],
            field_errors={
                "password": [
                    field_error(error.messages[0], code=error.code or "invalid")
                    for error in exc.error_list
                ]
            },
        ) from None
    with transaction.atomic():
        user.set_password(password)
        user.save(update_fields=["password", "updated_at"])
        audit.record(
            "customer.password_set", actor=user, target=user, after={"via": purpose}, ip=ip
        )
        transaction.on_commit(lambda: end_app_sign_ins(user))
    return user


@dataclass(frozen=True, slots=True)
class Invitation:
    link: PasswordLink
    emailed: bool


def invite_customer(user: User, *, actor: User | None, ip: str | None = None) -> Invitation:
    """An admin's "set your password" invitation for a customer (audited, link not stored)."""
    if user.is_staff:
        raise ProblemError(ErrorCode.NOT_FOUND)
    if user.status == UserStatus.DISABLED:
        raise ProblemError(ErrorCode.CONFLICT, "Disabled customers cannot be invited.")
    link = make_password_link(user, "invite")
    emailed = send_password_link(link)
    audit.record(
        "customer.password_invite",
        actor=actor,
        target=user,
        after={"emailed": emailed, "expires_at": link.expires_at},
        ip=ip,
    )
    return Invitation(link=link, emailed=emailed)
