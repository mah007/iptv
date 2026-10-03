"""Request helpers shared by views: who is acting, and from which address."""

import ipaddress

from django.http import HttpRequest
from rest_framework.request import Request

from apps.accounts.models import User


def client_ip(request: HttpRequest | Request) -> str | None:
    """The client's IP address, or None when nothing valid is available.

    Django is only reachable through Traefik (edge network) or from inside the
    Docker network. Traefik does not trust incoming X-Forwarded-For from clients,
    so the right-most entry is the address Traefik itself saw; that's the one we
    use. Requests without the header (internal callers) use REMOTE_ADDR.
    """
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    candidates = [part.strip() for part in forwarded.split(",") if part.strip()]
    candidate = candidates[-1] if candidates else request.META.get("REMOTE_ADDR", "")
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def acting_user(request: HttpRequest | Request) -> User | None:
    """The authenticated user behind a request, for audit records; None if anonymous."""
    user = getattr(request, "user", None)
    if isinstance(user, User) and user.is_authenticated:
        return user
    return None
