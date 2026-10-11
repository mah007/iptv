"""Admin sign-in: password, then TOTP or a security key (SPEC §8.2, §11; ADR-0006, ADR-0021).

1. `begin_login` checks the password through Django's `authenticate`, which
   django-axes guards. A correct password does NOT log the admin in: the
   session only remembers who passed step one ("pending"), for a few minutes.
   Admins without a confirmed authenticator get a fresh TOTP secret to enrol.
2. `complete_login` checks the code, or `complete_login_webauthn` an assertion from one
   of the admin's keys (`apps.accounts.passkeys`); only then is the session logged in
   (with a new key), its idle timeout set from `security.admin_idle_timeout_min`, and
   sensitive actions unlocked for the step-up window (`apps.accounts.stepup`).

Wrong passwords, unknown users, customers and inactive admins all get the same
INVALID_CREDENTIALS. Wrong codes and keys count as failed sign-ins for axes, so both
steps share one exponential lockout per username and client IP. On top (G-03), a
pending sign-in allows `PENDING_ATTEMPTS` wrong second factors before the password is
needed again, and an account allows `USER_MFA_FAILURES` within `USER_MFA_WINDOW_S`
from all addresses together.
"""

import contextlib
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, cast

import redis
from axes.handlers.proxy import AxesProxyHandler
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.signals import user_login_failed
from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone

from apps.accounts import crypto, mfa, passkeys, stepup
from apps.accounts.lockout import LOGIN_USERNAME_ATTR
from apps.accounts.models import MfaTotp, User, UserStatus
from apps.audit import services as audit
from apps.core.errors import ErrorCode, ProblemError
from apps.core.http import client_ip
from apps.core.services import get_setting
from apps.core.stores import state_redis

PENDING_KEY = "_admin_login_pending"
PENDING_TTL_S = 300
MODEL_BACKEND = "django.contrib.auth.backends.ModelBackend"
#: Wrong second factors one pending sign-in may try before the password is asked again.
PENDING_ATTEMPTS: Final = 5
#: Wrong second factors per account across every address, within the window (G-03).
USER_MFA_FAILURES: Final = 10
USER_MFA_WINDOW_S: Final = 900
_USER_FAIL_PREFIX: Final = "mfa:fail:"


class LoginStatus(StrEnum):
    MFA_REQUIRED = "mfa_required"
    MFA_SETUP_REQUIRED = "mfa_setup_required"


@dataclass(frozen=True, slots=True)
class LoginStep:
    status: LoginStatus
    otpauth_uri: str | None = None
    #: With MFA_REQUIRED: the second factors this admin may use (`totp`, `webauthn`).
    methods: tuple[str, ...] = field(default=())


def _invalid() -> ProblemError:
    return ProblemError(ErrorCode.INVALID_CREDENTIALS, "The username or password is incorrect.")


def _locked() -> ProblemError:
    return ProblemError(
        ErrorCode.ACCOUNT_LOCKED, "Too many failed sign-in attempts. Try again later."
    )


def _username_for(login_value: str) -> str:
    """Admins sign in with their username or their email address."""
    value = login_value.strip()
    if "@" in value:
        match = User.objects.filter(email__iexact=value).values_list("username", flat=True).first()
        if match is not None:
            return match
    return value


def _can_sign_in(user: User) -> bool:
    return user.is_staff and user.is_active and user.status == UserStatus.ACTIVE


def begin_login(request: HttpRequest, login_value: str, password: str) -> LoginStep:
    """Step one. Raises INVALID_CREDENTIALS or ACCOUNT_LOCKED."""
    username = _username_for(login_value)
    setattr(request, LOGIN_USERNAME_ATTR, username)
    user = authenticate(request, username=username, password=password)
    if getattr(request, "axes_locked_out", False):
        raise _locked()
    if not isinstance(user, User) or not _can_sign_in(user):
        raise _invalid()

    # A new, empty session for the half-authenticated state: nothing from before
    # survives, including a sign-in the browser already had.
    request.session.flush()
    _check_user_failures(user)
    totp = MfaTotp.objects.filter(user=user).first()
    methods = second_factors(user, totp)
    if methods:
        _remember(request, user, stage="verify")
        return LoginStep(LoginStatus.MFA_REQUIRED, methods=methods)

    # Enrolment: a new secret on every attempt, so a URI seen once is useless later.
    secret = mfa.new_secret()
    MfaTotp.objects.update_or_create(
        user=user,
        defaults={
            "secret_encrypted": crypto.encrypt(secret),
            "confirmed_at": None,
            "last_used_step": None,
        },
    )
    _remember(request, user, stage="setup")
    account = user.email or user.username
    issuer = str(get_setting("branding.service_name_en")) or mfa.ISSUER
    return LoginStep(LoginStatus.MFA_SETUP_REQUIRED, mfa.provisioning_uri(secret, account, issuer))


def second_factors(user: User, totp: MfaTotp | None = None) -> tuple[str, ...]:
    """The confirmed second factors of an admin, in the order the UI offers them.

    With `security.webauthn_required_for` covering the admin and a key registered, the
    authenticator code no longer signs in (it still works after an MFA reset, until a
    key is registered)."""
    if totp is None:
        totp = MfaTotp.objects.filter(user=user).first()
    keys = passkeys.has_credentials(user)
    methods: list[str] = []
    if keys:
        methods.append("webauthn")
    if (
        totp is not None
        and totp.confirmed_at is not None
        and not (keys and passkeys.required_for(user))
    ):
        methods.append("totp")
    return tuple(methods)


def _remember(request: HttpRequest, user: User, *, stage: str) -> None:
    request.session[PENDING_KEY] = {
        "user_id": str(user.pk),
        "stage": stage,
        "at": int(time.time()),
        "attempts": 0,
    }


def _user_fail_key(user: User) -> str:
    return f"{_USER_FAIL_PREFIX}{user.pk}"


def _check_user_failures(user: User) -> None:
    """ACCOUNT_LOCKED while the account's wrong second factors are over the limit."""
    try:
        raw = cast("bytes | None", state_redis().get(_user_fail_key(user)))
    except redis.RedisError:
        return
    if raw is not None and int(raw) >= USER_MFA_FAILURES:
        raise _locked()


def _count_user_failure(request: HttpRequest, user: User) -> None:
    pending = request.session.get(PENDING_KEY)
    if isinstance(pending, dict):
        pending["attempts"] = int(pending.get("attempts", 0)) + 1
        if pending["attempts"] >= PENDING_ATTEMPTS:
            request.session.pop(PENDING_KEY, None)
        else:
            request.session[PENDING_KEY] = pending
    try:
        pipe = state_redis().pipeline()
        pipe.incr(_user_fail_key(user))
        pipe.expire(_user_fail_key(user), USER_MFA_WINDOW_S, nx=True)
        pipe.execute()
    except redis.RedisError:
        pass


def _clear_user_failures(user: User) -> None:
    with contextlib.suppress(redis.RedisError):
        state_redis().delete(_user_fail_key(user))


def _pending_user(request: HttpRequest) -> tuple[User, str]:
    pending = request.session.get(PENDING_KEY)
    if not isinstance(pending, dict) or time.time() - pending.get("at", 0) > PENDING_TTL_S:
        request.session.pop(PENDING_KEY, None)
        raise ProblemError(
            ErrorCode.NOT_AUTHENTICATED, "Sign in with your password first.", status=401
        )
    user_id = pending.get("user_id")
    user = User.objects.filter(pk=user_id).first() if isinstance(user_id, str) else None
    if user is None or not _can_sign_in(user):
        request.session.pop(PENDING_KEY, None)
        raise _invalid()
    return user, str(pending.get("stage"))


def _fail_code(request: HttpRequest, user: User, message: str | None = None) -> ProblemError:
    """Count a wrong code or key as a failed sign-in (axes and per account), then refuse."""
    user_login_failed.send(
        sender=__name__, credentials={"username": user.username}, request=request
    )
    _count_user_failure(request, user)
    if getattr(request, "axes_locked_out", False):
        request.session.pop(PENDING_KEY, None)
        return _locked()
    return ProblemError(ErrorCode.MFA_INVALID, message or "The verification code is not valid.")


def _guard_second_step(request: HttpRequest) -> tuple[User, str]:
    """The pending admin, once lockouts allow another try."""
    user, stage = _pending_user(request)
    setattr(request, LOGIN_USERNAME_ATTR, user.username)
    if not AxesProxyHandler.is_allowed(request, {"username": user.username}):
        request.session.pop(PENDING_KEY, None)
        raise _locked()
    try:
        _check_user_failures(user)
    except ProblemError:
        request.session.pop(PENDING_KEY, None)
        raise
    return user, stage


def _sign_in(request: HttpRequest, user: User) -> None:
    """Log the session in (a new key), set its idle expiry, unlock sensitive actions."""
    request.session.pop(PENDING_KEY, None)
    login(request, user, backend=MODEL_BACKEND)
    request.session.set_expiry(int(get_setting("security.admin_idle_timeout_min")) * 60)
    stepup.elevate(request, user)
    _clear_user_failures(user)
    if passkeys.required_for(user) and not passkeys.has_credentials(user):
        request.session[passkeys.ENROL_SESSION_KEY] = True


def complete_login(request: HttpRequest, code: str) -> User:
    """Step two: verify the code and log the session in. Raises MFA_INVALID,
    ACCOUNT_LOCKED, or NOT_AUTHENTICATED when step one is missing or stale."""
    user, stage = _guard_second_step(request)
    if stage != "setup" and "totp" not in second_factors(user):
        raise ProblemError(ErrorCode.MFA_INVALID, "Sign in with your security key.")
    ip = client_ip(request)
    with transaction.atomic():
        totp = MfaTotp.objects.select_for_update().filter(user=user).first()
        if totp is None:
            request.session.pop(PENDING_KEY, None)
            raise _invalid()
        enrolling = stage == "setup"
        if enrolling == (totp.confirmed_at is not None):
            # Enrolment finished elsewhere, or MFA was reset since step one.
            request.session.pop(PENDING_KEY, None)
            raise ProblemError(
                ErrorCode.NOT_AUTHENTICATED, "Sign in with your password again.", status=401
            )
        step = mfa.matching_step(
            crypto.decrypt(totp.secret_encrypted), code, last_used_step=totp.last_used_step
        )
        if step is None:
            failure = _fail_code(request, user)
        else:
            failure = None
            totp.last_used_step = step
            if enrolling:
                totp.confirmed_at = timezone.now()
            totp.save()
            if enrolling:
                user.mfa_enabled = True
            user.last_login_ip = ip
            user.save(update_fields=["mfa_enabled", "last_login_ip", "updated_at"])
            if enrolling:
                audit.record("auth.mfa_enroll", actor=user, target=user, ip=ip)
            audit.record("auth.login", actor=user, target=user, ip=ip)
    if failure is not None:
        raise failure

    _sign_in(request, user)
    return user


def webauthn_login_options(request: HttpRequest) -> dict[str, object]:
    """The pending admin's key request for navigator.credentials.get()."""
    user, stage = _guard_second_step(request)
    if stage != "verify":
        raise ProblemError(ErrorCode.NOT_FOUND, "No security key is registered.", status=404)
    return passkeys.authentication_options(request, user, "login")


def complete_login_webauthn(request: HttpRequest, credential: object) -> User:
    """Step two with a security key or passkey. Raises like `complete_login`."""
    user, stage = _guard_second_step(request)
    if stage != "verify":
        raise ProblemError(ErrorCode.NOT_FOUND, "No security key is registered.", status=404)
    try:
        key = passkeys.verify_assertion(request, user, credential, "login")
    except ProblemError:
        raise _fail_code(request, user, "The security key could not be verified.") from None
    ip = client_ip(request)
    with transaction.atomic():
        user.last_login_ip = ip
        user.save(update_fields=["last_login_ip", "updated_at"])
        audit.record(
            "auth.login",
            actor=user,
            target=user,
            ip=ip,
            after={"method": "webauthn", "key": str(key.pk)},
        )
    _sign_in(request, user)
    return user


def end_session(request: HttpRequest) -> None:
    user = getattr(request, "user", None)
    if isinstance(user, User) and user.is_authenticated:
        audit.record("auth.logout", actor=user, target=user, ip=client_ip(request))
    logout(request)


# --- Step-up (ADR-0021) -----------------------------------------------------------------------


def step_up_options(request: HttpRequest, user: User) -> dict[str, object]:
    """A key request to confirm a sensitive action (navigator.credentials.get())."""
    wait = stepup.blocked(user)
    if wait:
        raise stepup.too_many(wait)
    return passkeys.authentication_options(request, user, "step_up")


def step_up(
    request: HttpRequest, user: User, *, code: str | None = None, credential: object = None
) -> int:
    """Confirm the signed-in admin with a code or a key; returns when the window ends.

    Admins confirm with a second factor, never the password alone. `stepup.FAIL_LIMIT`
    wrong confirmations end the session (a stolen session can't guess forever)."""
    wait = stepup.blocked(user)
    if wait:
        raise stepup.too_many(wait)
    ip = client_ip(request)
    method = ""
    if credential is not None:
        try:
            passkeys.verify_assertion(request, user, credential, "step_up")
            method = "webauthn"
        except ProblemError:
            method = ""
    elif code and "totp" in second_factors(user):
        with transaction.atomic():
            totp = (
                MfaTotp.objects.select_for_update()
                .filter(user=user, confirmed_at__isnull=False)
                .first()
            )
            step = (
                mfa.matching_step(
                    crypto.decrypt(totp.secret_encrypted), code, last_used_step=totp.last_used_step
                )
                if totp is not None
                else None
            )
            if totp is not None and step is not None:
                totp.last_used_step = step
                totp.save(update_fields=["last_used_step", "updated_at"])
                method = "totp"
    if not method:
        failures = stepup.count_failure(user)
        audit.record("auth.step_up_failed", actor=user, target=user, ip=ip)
        if failures >= stepup.FAIL_LIMIT:
            audit.record("auth.step_up_locked", actor=user, target=user, ip=ip)
            logout(request)
            raise ProblemError(
                ErrorCode.NOT_AUTHENTICATED,
                "Too many failed confirmations: you have been signed out.",
                status=401,
            )
        raise ProblemError(ErrorCode.MFA_INVALID, "The code or security key could not be verified.")
    end = stepup.elevate(request, user)
    audit.record("auth.step_up", actor=user, target=user, ip=ip, after={"method": method})
    return end


# --- MFA reset (recovery, ADR-0021) -------------------------------------------------------------


def reset_mfa(target: User, *, actor: User | None, ip: str | None = None) -> None:
    """Remove an admin's authenticator and keys and end their sessions.

    Their next sign-in enrols a new authenticator app. Used by another admin
    (`admins/{id}/reset-mfa`) and by `manage.py reset_admin_mfa` on the host, the
    owner's last resort."""
    with transaction.atomic():
        locked = User.objects.select_for_update().get(pk=target.pk)
        keys = list(locked.webauthn_credentials.values_list("name", flat=True))
        MfaTotp.objects.filter(user=locked).delete()
        locked.webauthn_credentials.all().delete()
        locked.mfa_enabled = False
        locked.auth_epoch += 1
        locked.save(update_fields=["mfa_enabled", "auth_epoch", "updated_at"])
        audit.record(
            "admin.mfa_reset",
            actor=actor,
            target=locked,
            ip=ip,
            before={"totp": True, "keys": keys},
            after={"totp": False, "keys": []},
        )
    _clear_user_failures(target)
    stepup.clear_failures(target)
