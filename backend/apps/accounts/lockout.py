"""django-axes hooks: client IP, exponential cool-off, problem+json lockouts (SPEC §11).

Admin sign-in failures (wrong password or wrong MFA code) are counted per
username and client IP. After `AXES_FAILURE_LIMIT` of them the pair is locked
for COOL_OFF_BASE; every further failure doubles that, up to COOL_OFF_MAX.
Lockouts are never permanent: a pair's record expires one cool-off after its
last failure, and the count starts again.
"""

from datetime import timedelta

from axes.models import AccessAttempt
from django.conf import settings
from django.db.models import Max
from django.http import HttpRequest, HttpResponse

from apps.core.errors import ErrorCode, problem_response
from apps.core.http import client_ip

COOL_OFF_BASE = timedelta(minutes=15)
COOL_OFF_MAX = timedelta(hours=24)
# The view sets this to the username being tried, so the cool-off can find its record.
LOGIN_USERNAME_ATTR = "axes_login_username"


def axes_client_ip(request: HttpRequest) -> str | None:
    """AXES_CLIENT_IP_CALLABLE: the address Traefik saw, never a client-supplied one."""
    return client_ip(request)


def _failures(request: HttpRequest | None) -> int:
    if request is None:
        return 0
    username = getattr(request, LOGIN_USERNAME_ATTR, None)
    ip_address = getattr(request, "axes_ip_address", None)
    if username is None:
        return 0
    stored = AccessAttempt.objects.filter(username=username, ip_address=ip_address).aggregate(
        failures=Max("failures_since_start")
    )["failures"]
    return int(stored or 0)


def cool_off(request: HttpRequest | None) -> timedelta:
    """AXES_COOLOFF_TIME: the base period, doubled per failure past the limit, capped."""
    excess = max(0, _failures(request) - int(settings.AXES_FAILURE_LIMIT))
    # Doubling past 2**7 base periods is already beyond the cap.
    period = COOL_OFF_BASE * (1 << min(excess, 7))
    return min(period, COOL_OFF_MAX)


def lockout_response(
    request: HttpRequest, original_response: object = None, credentials: object = None
) -> HttpResponse:
    """AXES_LOCKOUT_CALLABLE: 429 problem+json ACCOUNT_LOCKED with Retry-After."""
    response = problem_response(
        ErrorCode.ACCOUNT_LOCKED, "Too many failed sign-in attempts. Try again later."
    )
    response["Retry-After"] = str(int(cool_off(request).total_seconds()))
    return response
