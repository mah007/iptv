"""Admin sign-in API on the admin host: /api/v1/auth/* (SPEC §8.2, §11; ADR-0006).

Same-origin session cookie + CSRF (ADR-0004). DRF only checks CSRF for
authenticated sessions, so the views that accept anonymous unsafe requests
(login, MFA verification) enforce it themselves: a cross-site page can't drive
the sign-in flow, and a stolen password alone never yields a session.
"""

from datetime import UTC, datetime
from typing import Any

from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts import auth, passkeys, stepup
from apps.accounts.models import User, WebAuthnCredential
from apps.accounts.permissions import request_permission_codes
from apps.accounts.rbac import permission_codes
from apps.accounts.serializers import (
    LoginResponseSerializer,
    LoginSerializer,
    MeSerializer,
    MfaVerifySerializer,
    PasswordChangeSerializer,
    StepUpResultSerializer,
    StepUpSerializer,
    WebAuthnCredentialSerializer,
    WebAuthnOptionsSerializer,
    WebAuthnRegisterSerializer,
    WebAuthnRenameSerializer,
    WebAuthnVerifySerializer,
)
from apps.core.authentication import SessionAuthentication
from apps.core.errors import ErrorCode, ProblemError
from apps.core.schema import problems


class _EnforceCsrf(SessionAuthentication):
    """Exposes DRF's CSRF check for views that anonymous users call."""

    def check(self, request: Request) -> None:
        self.enforce_csrf(request)


def me_payload(user: User, codes: frozenset[str], request: Any = None) -> dict[str, Any]:
    context: dict[str, Any] = {"permission_codes": codes}
    if request is not None:
        context["step_up_until"] = stepup.until(request)
        context["webauthn_enrolment_required"] = passkeys.enrolment_required(user)
    return dict(MeSerializer(user, context=context).data)


def _until(end: int) -> Response:
    body = {"step_up_until": datetime.fromtimestamp(end, tz=UTC)}
    response = Response(StepUpResultSerializer(body).data)
    response["Cache-Control"] = "no-store"
    return response


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
        if step.methods:
            body["methods"] = list(step.methods)
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
        return Response(me_payload(user, permission_codes(user), request._request))


class MfaWebAuthnOptionsView(AnonymousAuthView):
    """POST /api/v1/auth/mfa/webauthn/options: the key request for the pending sign-in."""

    @extend_schema(
        operation_id="auth_mfa_webauthn_options",
        summary="Get the security key request that completes the sign-in",
        auth=[],
        request=None,
        responses={200: WebAuthnOptionsSerializer, **problems(401, 403, 404, 429)},
    )
    def post(self, request: Request) -> Response:
        options = auth.webauthn_login_options(request._request)
        response = Response({"options": options})
        response["Cache-Control"] = "no-store"
        return response


class MfaWebAuthnVerifyView(AnonymousAuthView):
    """POST /api/v1/auth/mfa/webauthn/verify: step two with a security key; signs in."""

    @extend_schema(
        operation_id="auth_mfa_webauthn_verify",
        summary="Verify the security key and sign in",
        auth=[],
        request=WebAuthnVerifySerializer,
        responses={200: MeSerializer, **problems(400, 401, 403, 404, 429)},
    )
    def post(self, request: Request) -> Response:
        payload = WebAuthnVerifySerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        user = auth.complete_login_webauthn(request._request, payload.validated_data["credential"])
        return Response(me_payload(user, permission_codes(user), request._request))


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
        return Response(me_payload(user, request_permission_codes(request), request._request))


class StaffView(APIView):
    """Signed-in staff only (their own account's security)."""

    permission_classes = (IsAuthenticated,)

    def staff(self, request: Request) -> User:
        user = request.user
        if not isinstance(user, User) or not user.is_staff:
            raise ProblemError(ErrorCode.PERMISSION_DENIED)
        return user


class StepUpView(StaffView):
    """POST /api/v1/auth/step-up confirms it's you; DELETE locks sensitive actions again."""

    @extend_schema(
        operation_id="auth_step_up",
        summary="Confirm it's you (an authenticator code or a security key)",
        request=StepUpSerializer,
        responses={200: StepUpResultSerializer, **problems(400, 401, 403, 429)},
    )
    def post(self, request: Request) -> Response:
        user = self.staff(request)
        payload = StepUpSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        end = auth.step_up(
            request._request,
            user,
            code=payload.validated_data.get("code"),
            credential=payload.validated_data.get("credential"),
        )
        return _until(end)

    @extend_schema(
        operation_id="auth_step_up_forget",
        summary="Lock sensitive actions now",
        request=None,
        responses={204: OpenApiResponse(description="Locked."), **problems(401, 403)},
    )
    def delete(self, request: Request) -> Response:
        self.staff(request)
        stepup.forget(request._request)
        return Response(status=204)


class PasswordChangeView(StaffView):
    """POST /api/v1/auth/password: change my own password (Q1, ADR-0023). It needs the
    current password and a recent confirmation (a code or a key: never the password
    alone, ADR-0021); every other session of mine ends."""

    @extend_schema(
        operation_id="auth_password_change",
        summary="Change my password; my other sessions end",
        request=PasswordChangeSerializer,
        responses={204: OpenApiResponse(description="Changed."), **problems(400, 401, 403)},
    )
    def post(self, request: Request) -> Response:
        user = self.staff(request)
        stepup.require(request)
        payload = PasswordChangeSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        auth.change_password(
            request._request,
            user,
            current=payload.validated_data["current_password"],
            new=payload.validated_data["new_password"],
        )
        return Response(status=204)


class StepUpOptionsView(StaffView):
    """POST /api/v1/auth/step-up/options: a security key request to confirm it's you."""

    @extend_schema(
        operation_id="auth_step_up_options",
        summary="Get a security key request to confirm it's you",
        request=None,
        responses={200: WebAuthnOptionsSerializer, **problems(401, 403, 404, 429)},
    )
    def post(self, request: Request) -> Response:
        options = auth.step_up_options(request._request, self.staff(request))
        response = Response({"options": options})
        response["Cache-Control"] = "no-store"
        return response


class WebAuthnCredentialListView(StaffView):
    """GET/POST /api/v1/auth/webauthn/credentials: the admin's own keys; register one."""

    @extend_schema(
        operation_id="auth_webauthn_credentials_list",
        summary="My security keys and passkeys",
        responses={200: WebAuthnCredentialSerializer(many=True), **problems(401, 403)},
    )
    def get(self, request: Request) -> Response:
        user = self.staff(request)
        keys = WebAuthnCredential.objects.filter(user=user).order_by("created_at")
        return Response(WebAuthnCredentialSerializer(keys, many=True).data)

    @extend_schema(
        operation_id="auth_webauthn_credentials_create",
        summary="Register a security key or passkey (after confirming it's you)",
        request=WebAuthnRegisterSerializer,
        responses={201: WebAuthnCredentialSerializer, **problems(400, 401, 403, 409)},
    )
    def post(self, request: Request) -> Response:
        user = self.staff(request)
        stepup.require(request)
        payload = WebAuthnRegisterSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        key = passkeys.finish_registration(
            request._request,
            user,
            payload.validated_data["credential"],
            payload.validated_data["name"],
        )
        return Response(WebAuthnCredentialSerializer(key).data, status=201)


class WebAuthnRegisterOptionsView(StaffView):
    """POST /api/v1/auth/webauthn/credentials/options: the creation request for a new key."""

    @extend_schema(
        operation_id="auth_webauthn_credentials_options",
        summary="Get the request that registers a new security key",
        request=None,
        responses={200: WebAuthnOptionsSerializer, **problems(401, 403, 409)},
    )
    def post(self, request: Request) -> Response:
        user = self.staff(request)
        stepup.require(request)
        response = Response({"options": passkeys.registration_options(request._request, user)})
        response["Cache-Control"] = "no-store"
        return response


class WebAuthnCredentialDetailView(StaffView):
    """PATCH/DELETE /api/v1/auth/webauthn/credentials/{id}: rename or remove one key."""

    def key(self, request: Request, pk: str) -> tuple[User, WebAuthnCredential]:
        user = self.staff(request)
        return user, get_object_or_404(WebAuthnCredential, pk=pk, user=user)

    @extend_schema(
        operation_id="auth_webauthn_credentials_partial_update",
        summary="Rename a security key",
        request=WebAuthnRenameSerializer,
        responses={200: WebAuthnCredentialSerializer, **problems(400, 401, 403, 404)},
    )
    def patch(self, request: Request, pk: str) -> Response:
        user, key = self.key(request, pk)
        payload = WebAuthnRenameSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        passkeys.rename(request._request, user, key, payload.validated_data["name"])
        return Response(WebAuthnCredentialSerializer(key).data)

    @extend_schema(
        operation_id="auth_webauthn_credentials_destroy",
        summary="Remove a security key (after confirming it's you)",
        request=None,
        responses={204: OpenApiResponse(description="Removed."), **problems(401, 403, 404, 409)},
    )
    def delete(self, request: Request, pk: str) -> Response:
        user, key = self.key(request, pk)
        stepup.require(request)
        passkeys.remove(request._request, user, key)
        return Response(status=204)
