"""Optional two-factor sign-in for customers (TOTP; ADR-0024).

A customer turns it on in the portal's account page (`me/mfa`, after the password
step-up): `begin` stores a new secret (encrypted, `MfaTotp`, the admins' table) and
returns its `otpauth://` URI for the QR code; `confirm` checks the first code and
switches it on. `remove` switches it off (step-up again).

Signing in: once it is on, every new sign-in sends a code with the password, on the
portal (session) and on the API host (bearer tokens) alike. The first attempt without one
gets 403 `MFA_REQUIRED` (the password was right) and the app asks for the code. A wrong
code counts as a failed sign-in (django-axes) and against the account's limit in
`mfa:fail:<user>`, the same counter as the admins' second step, so the Lockouts page shows
and clears it. Token families issued before it was switched on keep working until they
end: the code is asked at the next sign-in, never at a refresh.

Recovery is through support: an admin resets it (`customers/{id}/reset-mfa`, step-up,
audited) and the customer signs in with the password alone.
"""

import contextlib
from typing import cast

import redis
from django.contrib.auth.signals import user_login_failed
from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone

from apps.accounts import auth, crypto, mfa, stepup
from apps.accounts.models import MfaTotp, User
from apps.audit import services as audit
from apps.core.errors import ErrorCode, ProblemError
from apps.core.services import get_setting
from apps.core.stores import state_redis


def _fail_key(user: User) -> str:
    return f"{auth.USER_MFA_FAILURES_PREFIX}{user.pk}"


def enrolled(user: User) -> bool:
    """Whether the customer signs in with a code (a confirmed authenticator)."""
    return MfaTotp.objects.filter(user=user, confirmed_at__isnull=False).exists()


def _locked() -> ProblemError:
    return ProblemError(
        ErrorCode.ACCOUNT_LOCKED, "Too many failed sign-in attempts. Try again later."
    )


def _blocked(user: User) -> bool:
    try:
        raw = cast("bytes | None", state_redis().get(_fail_key(user)))
    except redis.RedisError:
        return False
    return raw is not None and int(raw) >= auth.USER_MFA_FAILURES


def _count_failure(user: User) -> None:
    with contextlib.suppress(redis.RedisError):
        pipe = state_redis().pipeline()
        pipe.incr(_fail_key(user))
        pipe.expire(_fail_key(user), auth.USER_MFA_WINDOW_S, nx=True)
        pipe.execute()


def clear_failures(user: User) -> None:
    with contextlib.suppress(redis.RedisError):
        state_redis().delete(_fail_key(user))


def _use_code(totp: MfaTotp, code: str) -> bool:
    """Accept a code once (never the same step twice) and remember its step."""
    secret = crypto.decrypt(totp.secret_encrypted)
    step = mfa.matching_step(secret, code, last_used_step=totp.last_used_step)
    if step is None:
        return False
    totp.last_used_step = step
    return True


def check_sign_in(request: HttpRequest, user: User, code: str) -> None:
    """The second step of a sign-in whose password was right. Returns for customers without
    two-factor sign-in; raises MFA_REQUIRED (no code), MFA_INVALID or ACCOUNT_LOCKED."""
    if not enrolled(user):
        return
    if not code.strip():
        raise ProblemError(
            ErrorCode.MFA_REQUIRED,
            "Enter the code from your authenticator app.",
            status=403,
        )
    if _blocked(user):
        raise _locked()
    with transaction.atomic():
        totp = MfaTotp.objects.select_for_update().get(user=user)
        if totp.confirmed_at is not None and _use_code(totp, code):
            totp.save(update_fields=["last_used_step", "updated_at"])
            clear_failures(user)
            return
    user_login_failed.send(
        sender=__name__, credentials={"username": user.username}, request=request
    )
    _count_failure(user)
    if getattr(request, "axes_locked_out", False):
        raise _locked()
    raise ProblemError(ErrorCode.MFA_INVALID, "The verification code is not valid.")


def begin(user: User) -> str:
    """A new secret waiting for its first code; returns the `otpauth://` URI.

    Refused while two-factor sign-in is on (turn it off first). Each call replaces the
    waiting secret, so a URI seen once is useless later."""
    if enrolled(user):
        raise ProblemError(ErrorCode.CONFLICT, "Two-factor sign-in is already on.", status=409)
    secret = mfa.new_secret()
    MfaTotp.objects.update_or_create(
        user=user,
        defaults={
            "secret_encrypted": crypto.encrypt(secret),
            "confirmed_at": None,
            "last_used_step": None,
        },
    )
    issuer = str(get_setting("branding.service_name_en")) or mfa.ISSUER
    return mfa.provisioning_uri(secret, user.email or user.username, issuer)


def confirm(user: User, code: str, *, ip: str | None) -> None:
    """Switch two-factor sign-in on with the first code of the waiting secret. The session
    keeps its sign-in, and so do the customer's other sign-ins (ADR-0024)."""
    with transaction.atomic():
        totp = MfaTotp.objects.select_for_update().filter(user=user).first()
        if totp is None or totp.confirmed_at is not None:
            raise ProblemError(
                ErrorCode.CONFLICT, "Start setting up two-factor sign-in first.", status=409
            )
        if not _use_code(totp, code):
            wait = stepup.blocked(user)
            if wait:
                raise stepup.too_many(wait)
            stepup.count_failure(user)
            raise ProblemError(ErrorCode.MFA_INVALID, "The verification code is not valid.")
        totp.confirmed_at = timezone.now()
        totp.save(update_fields=["confirmed_at", "last_used_step", "updated_at"])
        User.objects.filter(pk=user.pk).update(mfa_enabled=True, updated_at=timezone.now())
        user.mfa_enabled = True
        audit.record("customer.mfa_enable", actor=user, target=user, ip=ip)


def remove(user: User, *, actor: User | None, ip: str | None, action: str) -> bool:
    """Switch two-factor sign-in off (the customer, or support's reset). Returns whether
    there was anything to remove; audited as `action` when there was."""
    with transaction.atomic():
        locked = User.objects.select_for_update().get(pk=user.pk)
        had = MfaTotp.objects.filter(user=locked, confirmed_at__isnull=False).exists()
        MfaTotp.objects.filter(user=locked).delete()
        if locked.mfa_enabled:
            locked.mfa_enabled = False
            locked.save(update_fields=["mfa_enabled", "updated_at"])
        user.mfa_enabled = False
        if had:
            audit.record(
                action,
                actor=actor,
                target=locked,
                ip=ip,
                before={"two_factor": True},
                after={"two_factor": False},
            )
    clear_failures(user)
    return had
