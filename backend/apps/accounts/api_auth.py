"""Admin sign-in API on the admin host: /api/v1/auth/* (SPEC §8.2, §11; ADR-0006).

Same-origin session cookie + CSRF (ADR-0004). DRF only checks CSRF for
authenticated sessions, so the views that accept anonymous unsafe requests
(login, MFA verification) enforce it themselves: a cross-site page can't drive
the sign-in flow, and a stolen password alone never yields a session.
"""

from typing import Any

from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts import auth
from apps.accounts.models import User
from apps.accounts.permissions import request_permission_codes
from apps.accounts.rbac import permission_codes
from apps.accounts.serializers import (
    LoginResponseSerializer,
    LoginSerializer,
    MeSerializer,
    MfaVerifySerializer,
)
from apps.core.authentication import SessionAuthentication
from apps.core.errors import ErrorCode, ProblemError
from apps.core.schema import problems


class _EnforceCsrf(SessionAuthentication):
    """Exposes DRF's CSRF check for views that anonymous users call."""

    def check(self, request: Request) -> None:
        self.enforce_csrf(request)


def me_payload(user: User, codes: frozenset[str]) -> dict[str, Any]:
    return dict(MeSerializer(user, context={"permission_codes": codes}).data)


class AnonymousAuthView(APIView):
    """Base for the steps before a session exists: no auth, CSRF enforced."""

    authentication_classes = (SessionAuthentication,)
    permission_classes = (AllowAny,)

    def initial(self, request: Request, *args: Any, **kwargs: Any) -> None:
        super().initial(request, *args, **kwargs)
        _EnforceCsrf().check(request)


@method_decorator(ensure_csrf_cookie, name="get")
@method_decorator(never_cache, name="get")
class CsrfView(APIView):
    """GET /api/v1/auth/csrf: sets the `csrftoken` cookie the SPA echoes as X-CSRFToken."""

    authentication_classes = ()
    permission_classes = (AllowAny,)

    @extend_schema(
        operation_id="auth_csrf",
        summary="Set the CSRF cookie",
        auth=[],
        responses={204: OpenApiResponse(description="The csrftoken cookie is set.")},
    )
    def get(self, request: Request) -> Response:
        return Response(status=204)


class LoginView(AnonymousAuthView):
    """POST /api/v1/auth/login: step one, the password. Never signs in by itself."""

    @extend_schema(
        operation_id="auth_login",
        summary="Check the password; the response says which MFA step follows",
        auth=[],
        request=LoginSerializer,
        responses={200: LoginResponseSerializer, **problems(400, 403, 429)},
    )
    def post(self, request: Request) -> Response:
        payload = LoginSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        step = auth.begin_login(
            request._request,
            payload.validated_data["login"],
            payload.validated_data["password"],
        )
        body: dict[str, Any] = {"status": step.status.value}
        if step.otpauth_uri is not None:
            body["otpauth_uri"] = step.otpauth_uri
        response = Response(LoginResponseSerializer(body).data)
        response["Cache-Control"] = "no-store"
        return response


class MfaVerifyView(AnonymousAuthView):
    """POST /api/v1/auth/mfa/verify: step two, the TOTP code; signs the session in."""

    @extend_schema(
        operation_id="auth_mfa_verify",
        summary="Verify the authenticator code and sign in",
        auth=[],
        request=MfaVerifySerializer,
        responses={200: MeSerializer, **problems(400, 401, 403, 429)},
    )
    def post(self, request: Request) -> Response:
        payload = MfaVerifySerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        user = auth.complete_login(
            request._request,
            payload.validated_data["code"],
        )
        return Response(me_payload(user, permission_codes(user)))


class LogoutView(APIView):
    """POST /api/v1/auth/logout: ends the session (also a half-finished sign-in)."""

    permission_classes = (AllowAny,)

    def initial(self, request: Request, *args: Any, **kwargs: Any) -> None:
        super().initial(request, *args, **kwargs)
        _EnforceCsrf().check(request)

    @extend_schema(
        operation_id="auth_logout",
        summary="Sign out",
        request=None,
        responses={204: OpenApiResponse(description="Signed out."), **problems(403)},
    )
    def post(self, request: Request) -> Response:
        auth.end_session(request._request)
        return Response(status=204)


class MeView(APIView):
    """GET /api/v1/auth/me: the signed-in admin, their roles and permission codes."""

    permission_classes = (IsAuthenticated,)

    @extend_schema(
        operation_id="auth_me",
        summary="The signed-in admin",
        responses={200: MeSerializer, **problems(401, 403)},
    )
    def get(self, request: Request) -> Response:
        user = request.user
        if not isinstance(user, User) or not user.is_staff:
            raise ProblemError(ErrorCode.PERMISSION_DENIED)
        return Response(me_payload(user, request_permission_codes(request)))
