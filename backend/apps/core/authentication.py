"""Session authentication for the same-origin admin and portal APIs (ADR-0004)."""

import structlog
from django.contrib.auth.base_user import AbstractBaseUser
from rest_framework import authentication
from rest_framework.request import Request

# Sent with 401s so DRF answers unauthenticated requests with 401, not 403.
# Browsers only prompt for Basic/Digest, so this never opens a login dialog.
WWW_AUTHENTICATE = 'Session realm="api"'


class SessionAuthentication(authentication.SessionAuthentication):
    """DRF session auth that answers 401 when unauthenticated and tags logs with the user.

    CSRF: DRF enforces it for authenticated sessions only. Endpoints that accept
    anonymous unsafe requests, such as login, must enforce CSRF themselves
    (ADR-0004).
    """

    def authenticate(self, request: Request) -> tuple[AbstractBaseUser, None] | None:
        result = super().authenticate(request)
        if result is not None:
            structlog.contextvars.bind_contextvars(user_id=str(result[0].pk))
        return result

    def authenticate_header(self, request: Request) -> str:
        return WWW_AUTHENTICATE
