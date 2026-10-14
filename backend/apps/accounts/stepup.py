"""Step-up re-authentication before sensitive actions (SPEC §8.2, §11; G-31, ADR-0021).

A signed-in session is not enough for some actions: revealing or resetting credentials,
changing roles, admins or settings, backups, payments and refunds, free grants and
deletions in the admin; adding a TV app login in the portal. They need a recent
confirmation that the person at the keyboard is the account's owner:

- **Admins** confirm with an authenticator code or a security key (never the password
  alone: the session already proves it). Window: `security.admin_step_up_window_min`.
- **Customers** confirm with their password. Window: `security.customer_step_up_window_min`.

Signing in counts as a confirmation. The state is a Unix time ("confirmed until") in
redis-state: per session for the admin and the portal (keyed by a hash of the session
key), in the token family for app clients (api.*). Not in the session itself: with
`SESSION_SAVE_EVERY_REQUEST`, a request that loaded the session before a lock would write
the old window back when it ends, and the lock would be undone. `DELETE auth/step-up`
ends it early ("Lock sensitive actions").

Views opt in with `step_up_methods = frozenset({"POST", ...})` (checked by the admin's
`HasPermission` and the customer's `CustomerStepUp` after the permission check), or call
`require(request)` for a conditional case. A missing confirmation is 403
`STEP_UP_REQUIRED`; the SPA then asks and retries the request.

Failed confirmations are counted per account in redis-state: after `FAIL_LIMIT` within
`FAIL_WINDOW_S`, an admin's session ends and a customer's confirmations are refused for
the window (password failures also count towards django-axes' lockout).
"""

import contextlib
import hashlib
import time
from typing import Final, cast

import redis
import structlog
from django.http import HttpRequest
from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.views import APIView

from apps.accounts import customer_tokens
from apps.accounts.customer_tokens import AccessGrant
from apps.accounts.models import User
from apps.core.errors import ErrorCode, ProblemError
from apps.core.services import get_setting
from apps.core.stores import state_redis

logger = structlog.get_logger(__name__)

_SESSION_PREFIX: Final = "stepup:session:"
FAIL_LIMIT: Final = 5
FAIL_WINDOW_S: Final = 900
_FAIL_PREFIX: Final = "stepup:fail:"


def window_s(user: User) -> int:
    key = (
        "security.admin_step_up_window_min"
        if user.is_staff
        else ("security.customer_step_up_window_min")
    )
    return int(cast("int", get_setting(key))) * 60


def _grant(request: HttpRequest | Request) -> AccessGrant | None:
    auth = getattr(request, "auth", None) if isinstance(request, Request) else None
    return auth if isinstance(auth, AccessGrant) else None


def _session(request: HttpRequest | Request) -> object | None:
    raw = request._request if isinstance(request, Request) else request
    return getattr(raw, "session", None)


def _session_state_key(request: HttpRequest | Request, *, create: bool = False) -> str | None:
    """The redis-state key of this session's confirmation (a hash: no session ids in Redis)."""
    session = _session(request)
    if session is None:
        return None
    session_key = getattr(session, "session_key", None)
    if not session_key and create:
        session.save()  # type: ignore[attr-defined]
        session_key = getattr(session, "session_key", None)
    if not session_key:
        return None
    return _SESSION_PREFIX + hashlib.sha256(str(session_key).encode()).hexdigest()


def until(request: HttpRequest | Request) -> int | None:
    """When the current confirmation ends (Unix seconds), or None (also when Redis is
    unreachable: then nothing sensitive is unlocked)."""
    grant = _grant(request)
    try:
        if grant is not None:
            return customer_tokens.step_up_until(grant.family)
        key = _session_state_key(request)
        raw = cast("bytes | None", state_redis().get(key)) if key else None
        return int(raw) if raw else None
    except (redis.RedisError, ValueError):
        return None


def is_elevated(request: HttpRequest | Request) -> bool:
    current = until(request)
    return current is not None and current > time.time()


def elevate(request: HttpRequest | Request, user: User) -> int:
    """Start (or extend) the confirmation window; returns its end."""
    window = window_s(user)
    end = int(time.time()) + window
    grant = _grant(request)
    try:
        if grant is not None:
            customer_tokens.set_step_up(grant.family, end)
        elif key := _session_state_key(request, create=True):
            state_redis().set(key, str(end), ex=window + 60)
    except redis.RedisError:
        # Signing in still works; sensitive actions ask again once Redis is back.
        logger.warning("stepup.store_unavailable")
    clear_failures(user)
    return end


def forget(request: HttpRequest | Request) -> None:
    """End the confirmation now (the UI's "Lock sensitive actions")."""
    grant = _grant(request)
    if grant is not None:
        customer_tokens.set_step_up(grant.family, 0)
        return
    key = _session_state_key(request)
    if key:
        state_redis().delete(key)


def required() -> ProblemError:
    return ProblemError(
        ErrorCode.STEP_UP_REQUIRED, "Confirm it's you to continue: this action is protected."
    )


def require(request: HttpRequest | Request) -> None:
    """Raise STEP_UP_REQUIRED unless the account confirmed itself recently."""
    if not is_elevated(request):
        raise required()


def methods_of(view: APIView | type[APIView]) -> frozenset[str]:
    """The HTTP methods of `view` (an instance or its class) that need a recent confirmation."""
    methods = getattr(view, "step_up_methods", None)
    return frozenset(method.upper() for method in methods) if methods else frozenset()


class CustomerStepUp(BasePermission):
    """Customer endpoints: the view's `step_up_methods` need a recent password confirmation.

    Listed after IsCustomer, so an anonymous request still gets 401 first."""

    def has_permission(self, request: Request, view: APIView) -> bool:
        if (request.method or "").upper() in methods_of(view):
            require(request)
        return True


# --- Failed confirmations ---------------------------------------------------------------------


def _fail_key(user: User) -> str:
    return f"{_FAIL_PREFIX}{user.pk}"


def blocked(user: User) -> int:
    """Seconds left while confirmations are refused for this account; 0 when allowed."""
    try:
        count = cast("bytes | None", state_redis().get(_fail_key(user)))
        if count is None or int(count) < FAIL_LIMIT:
            return 0
        return max(1, int(cast("int", state_redis().ttl(_fail_key(user)))))
    except redis.RedisError:
        return 0


def count_failure(user: User) -> int:
    """Count a failed confirmation; returns the count in the window."""
    try:
        pipe = state_redis().pipeline()
        pipe.incr(_fail_key(user))
        pipe.expire(_fail_key(user), FAIL_WINDOW_S, nx=True)
        count, _ = pipe.execute()
        return int(count)
    except redis.RedisError:
        logger.warning("redis-state unavailable; step-up failure not counted")
        return 0


def clear_failures(user: User) -> None:
    with contextlib.suppress(redis.RedisError):
        state_redis().delete(_fail_key(user))


def too_many(retry_after: int) -> ProblemError:
    error = ProblemError(
        ErrorCode.RATE_LIMITED, "Too many failed confirmations. Try again later.", status=429
    )
    error.wait = retry_after  # type: ignore[attr-defined]  # becomes Retry-After
    return error
