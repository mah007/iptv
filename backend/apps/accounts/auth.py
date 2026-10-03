"""Admin sign-in: password, then TOTP (SPEC §8.2, §11; ADR-0006).

1. `begin_login` checks the password through Django's `authenticate`, which
   django-axes guards. A correct password does NOT log the admin in: the
   session only remembers who passed step one ("pending"), for a few minutes.
   Admins without a confirmed authenticator get a fresh TOTP secret to enrol.
2. `complete_login` checks the code; only then is the session logged in (with
   a new key) and its idle timeout set from `security.admin_idle_timeout_min`.

Wrong passwords, unknown users, customers and inactive admins all get the same
INVALID_CREDENTIALS. Wrong codes count as failed sign-ins for axes, so both
steps share one exponential lockout per username and client IP.
"""

import time
from dataclasses import dataclass
from enum import StrEnum

from axes.handlers.proxy import AxesProxyHandler
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.signals import user_login_failed
from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone

from apps.accounts import crypto, mfa
from apps.accounts.lockout import LOGIN_USERNAME_ATTR
from apps.accounts.models import MfaTotp, User, UserStatus
from apps.audit import services as audit
from apps.core.errors import ErrorCode, ProblemError
from apps.core.http import client_ip
from apps.core.services import get_setting

PENDING_KEY = "_admin_login_pending"
PENDING_TTL_S = 300
MODEL_BACKEND = "django.contrib.auth.backends.ModelBackend"


class LoginStatus(StrEnum):
    MFA_REQUIRED = "mfa_required"
    MFA_SETUP_REQUIRED = "mfa_setup_required"


@dataclass(frozen=True, slots=True)
class LoginStep:
    status: LoginStatus
    otpauth_uri: str | None = None


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
    totp = MfaTotp.objects.filter(user=user).first()
    if totp is not None and totp.confirmed_at is not None:
        _remember(request, user, stage="verify")
        return LoginStep(LoginStatus.MFA_REQUIRED)

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


def _remember(request: HttpRequest, user: User, *, stage: str) -> None:
    request.session[PENDING_KEY] = {
        "user_id": str(user.pk),
        "stage": stage,
        "at": int(time.time()),
    }


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


def _fail_code(request: HttpRequest, user: User) -> ProblemError:
    """Count a wrong code as a failed sign-in (axes), then refuse."""
    user_login_failed.send(
        sender=__name__, credentials={"username": user.username}, request=request
    )
    if getattr(request, "axes_locked_out", False):
        request.session.pop(PENDING_KEY, None)
        return _locked()
    return ProblemError(ErrorCode.MFA_INVALID, "The verification code is not valid.")


def complete_login(request: HttpRequest, code: str) -> User:
    """Step two: verify the code and log the session in. Raises MFA_INVALID,
    ACCOUNT_LOCKED, or NOT_AUTHENTICATED when step one is missing or stale."""
    user, stage = _pending_user(request)
    setattr(request, LOGIN_USERNAME_ATTR, user.username)
    if not AxesProxyHandler.is_allowed(request, {"username": user.username}):
        request.session.pop(PENDING_KEY, None)
        raise _locked()
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

    request.session.pop(PENDING_KEY, None)
    login(request, user, backend=MODEL_BACKEND)
    request.session.set_expiry(int(get_setting("security.admin_idle_timeout_min")) * 60)
    return user


def end_session(request: HttpRequest) -> None:
    user = getattr(request, "user", None)
    if isinstance(user, User) and user.is_authenticated:
        audit.record("auth.logout", actor=user, target=user, ip=client_ip(request))
    logout(request)
