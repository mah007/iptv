"""Customer API: sign-in, the signed-in customer and their devices (SPEC §10 Auth, Me;
ADR-0013), plus the admin's password invitation.

Sign-in has two flavours on the same paths: the portal host (`app.<domain>/api/v1/auth/*`)
opens a session; the API host (`api.<domain>/api/v1/auth/*`) returns bearer tokens.
Anonymous unsafe requests on the portal check CSRF themselves (ADR-0004). Responses
with a secret (tokens, Xtream passwords, password links) are `Cache-Control: no-store`.
"""

from datetime import UTC, datetime
from typing import Any, ClassVar
from uuid import UUID

from django.conf import settings
from django.shortcuts import get_object_or_404
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from drf_spectacular.openapi import AutoSchema
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts import customer_auth, customer_mfa, customer_services, services, stepup
from apps.accounts.customer_auth import (
    CUSTOMER_AUTHENTICATION,
    BearerTokenAuthentication,
    CsrfOnPortal,
    IsCustomer,
    PortalSessionAuthentication,
    customer_of,
)
from apps.accounts.customer_tokens import AccessGrant, TokenPair
from apps.accounts.models import Device, User
from apps.accounts.permissions import HasPermission, Requirements
from apps.accounts.serializers import (
    CustomerStepUpSerializer,
    PasswordChangeSerializer,
    StepUpResultSerializer,
)
from apps.accounts.serializers_customer import (
    AppLoginSerializer,
    AppSignInSerializer,
    CustomerLoginSerializer,
    CustomerMeSerializer,
    CustomerMeUpdateSerializer,
    ForgotPasswordSerializer,
    MfaConfirmSerializer,
    MfaSetupSerializer,
    MyCredentialResetSerializer,
    MyDeviceCreateSerializer,
    MyDeviceSerializer,
    MyDeviceUpdateSerializer,
    MyIssuedCredentialSerializer,
    PasswordInvitationSerializer,
    RefreshSerializer,
    ResetPasswordSerializer,
    TokenPairSerializer,
)
from apps.accounts.stepup import CustomerStepUp
from apps.core.errors import ErrorCode, ProblemError
from apps.core.http import acting_user, client_ip
from apps.core.schema import problems
from apps.core.throttling import DeviceCredentialThrottle


def no_store(response: Response) -> Response:
    response["Cache-Control"] = "no-store"
    return response


def me_body(user: User) -> dict[str, Any]:
    fresh = User.objects.select_related("access").get(pk=user.pk)
    return dict(CustomerMeSerializer(fresh).data)


def tokens_body(pair: TokenPair) -> dict[str, Any]:
    return {
        "token_type": "Bearer",
        "access_token": pair.access_token,
        "expires_in": pair.access_expires_in,
        "refresh_token": pair.refresh_token,
        "refresh_expires_in": pair.refresh_expires_in,
    }


class AnonymousView(APIView):
    """Before sign-in: no authentication; CSRF checked on the portal host."""

    authentication_classes = ()
    permission_classes = (AllowAny,)

    def initial(self, request: Request, *args: Any, **kwargs: Any) -> None:
        super().initial(request, *args, **kwargs)
        CsrfOnPortal().check(request)


class CustomerView(APIView):
    authentication_classes = CUSTOMER_AUTHENTICATION
    permission_classes: tuple[type[BasePermission], ...] = (IsCustomer,)


# --- Portal sign-in (session) -----------------------------------------------------------------


class PortalLoginView(AnonymousView):
    @extend_schema(
        operation_id="auth_login",
        summary="Sign in to the portal (session cookie)",
        description="Opens a session after `GET auth/csrf`; send the CSRF token as "
        "X-CSRFToken. The session lasts security.customer_session_days of idle time. A "
        "customer with two-factor sign-in gets 403 MFA_REQUIRED until `code` is sent too.",
        auth=[],
        request=CustomerLoginSerializer,
        responses={200: CustomerMeSerializer, **problems(400, 403, 429)},
    )
    def post(self, request: Request) -> Response:
        payload = CustomerLoginSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        user = customer_auth.portal_login(
            request._request,
            payload.validated_data["login"],
            payload.validated_data["password"],
            payload.validated_data["code"],
        )
        return no_store(Response(me_body(user)))


class PortalLogoutView(APIView):
    authentication_classes = (PortalSessionAuthentication,)
    permission_classes = (AllowAny,)

    def initial(self, request: Request, *args: Any, **kwargs: Any) -> None:
        super().initial(request, *args, **kwargs)
        CsrfOnPortal().check(request)

    @extend_schema(
        operation_id="auth_logout",
        summary="Sign out; this browser's playback stops",
        request=None,
        responses={204: OpenApiResponse(description="Signed out."), **problems(403)},
    )
    def post(self, request: Request) -> Response:
        customer_auth.portal_logout(request._request)
        return Response(status=204)


# --- App sign-in (bearer tokens) --------------------------------------------------------------


class AppLoginView(AnonymousView):
    @extend_schema(
        operation_id="auth_token_login",
        summary="Sign in an app: access and refresh tokens",
        description="A customer with two-factor sign-in gets 403 MFA_REQUIRED until `code` "
        "is sent too; refreshing never asks for it (ADR-0024).",
        request=AppLoginSerializer,
        auth=[],
        responses={200: AppSignInSerializer, **problems(400, 403, 429)},
    )
    def post(self, request: Request) -> Response:
        payload = AppLoginSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        signed_in = customer_auth.app_login(
            request._request,
            payload.validated_data["login"],
            payload.validated_data["password"],
            device_name=payload.validated_data["device_name"],
            code=payload.validated_data["code"],
        )
        body = {**tokens_body(signed_in.tokens), "user": me_body(signed_in.user)}
        return no_store(Response(body))


class AppRefreshView(AnonymousView):
    @extend_schema(
        operation_id="auth_token_refresh",
        summary="Exchange the refresh token for a new pair",
        description="Each refresh token works once. Presenting one that was already used "
        "signs the app out everywhere it was copied to (the token family is revoked).",
        request=RefreshSerializer,
        auth=[],
        responses={200: TokenPairSerializer, **problems(400, 401)},
    )
    def post(self, request: Request) -> Response:
        payload = RefreshSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        pair = customer_auth.app_refresh(request._request, payload.validated_data["refresh_token"])
        return no_store(Response(tokens_body(pair)))


class AppLogoutView(APIView):
    authentication_classes = (BearerTokenAuthentication,)
    permission_classes = (IsCustomer,)

    @extend_schema(
        operation_id="auth_token_logout",
        summary="Sign the app out: its tokens and device are revoked",
        request=None,
        responses={204: OpenApiResponse(description="Signed out."), **problems(401)},
    )
    def post(self, request: Request) -> Response:
        grant = request.auth
        if isinstance(grant, AccessGrant):
            customer_auth.app_logout(request._request, customer_of(request), grant)
        return Response(status=204)


# --- Password links (both hosts) --------------------------------------------------------------


class ForgotPasswordView(AnonymousView):
    @extend_schema(
        operation_id="auth_password_forgot",
        summary="Email a password-reset link",
        description="Always 202, whether or not the login names an account with an email.",
        auth=[],
        request=ForgotPasswordSerializer,
        responses={202: OpenApiResponse(description="Accepted."), **problems(400, 403, 429)},
    )
    def post(self, request: Request) -> Response:
        payload = ForgotPasswordSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        customer_auth.request_password_reset(request._request, payload.validated_data["login"])
        return Response(status=202)


class ResetPasswordView(AnonymousView):
    @extend_schema(
        operation_id="auth_password_reset",
        summary="Set the password from a reset or invitation link",
        description="The link works once. Other sign-ins of the account end.",
        auth=[],
        request=ResetPasswordSerializer,
        responses={204: OpenApiResponse(description="Password set."), **problems(400, 403, 429)},
    )
    def post(self, request: Request) -> Response:
        payload = ResetPasswordSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        customer_auth.reset_password(
            request._request,
            payload.validated_data["uid"],
            payload.validated_data["token"],
            payload.validated_data["password"],
        )
        return Response(status=204)


# --- Me ---------------------------------------------------------------------------------------


class MeView(CustomerView):
    @extend_schema(
        operation_id="me_retrieve",
        summary="The signed-in customer and what their access allows",
        responses={200: CustomerMeSerializer, **problems(401, 403)},
    )
    def get(self, request: Request) -> Response:
        return Response(me_body(customer_of(request)))

    @extend_schema(
        operation_id="me_update",
        summary="Change name, language, time zone or marketing consent",
        request=CustomerMeUpdateSerializer,
        responses={200: CustomerMeSerializer, **problems(400, 401, 403)},
    )
    def patch(self, request: Request) -> Response:
        user = customer_of(request)
        payload = CustomerMeUpdateSerializer(user, data=request.data, partial=True)
        payload.is_valid(raise_exception=True)
        customer_services.update_profile(user, payload.validated_data, ip=client_ip(request))
        return Response(me_body(user))


def _current_device_id(request: Request) -> str | None:
    if isinstance(request.auth, AccessGrant):
        return str(request.auth.device_id)
    session = getattr(request._request, "session", None)
    value = session.get(customer_auth.SESSION_DEVICE_KEY) if session is not None else None
    return str(value) if value else None


def _device_context(request: Request) -> dict[str, Any]:
    return {"current_device_id": _current_device_id(request)}


def _issued_body(issued: services.IssuedCredential) -> dict[str, Any]:
    device = Device.objects.select_related("credential").get(pk=issued.device.pk)
    return {
        "device": device,
        "server_url": services.xtream_server_url(),
        "username": issued.username,
        "password": issued.password,
    }


class CustomerStepUpView(CustomerView):
    """POST /api/v1/auth/step-up: the password again (G-31); DELETE locks again."""

    @extend_schema(
        operation_id="auth_step_up",
        summary="Confirm it's you with your password",
        description="Adding a TV app or a new TV app login needs a confirmation within "
        "security.customer_step_up_window_min; signing in counts as one.",
        request=CustomerStepUpSerializer,
        responses={200: StepUpResultSerializer, **problems(400, 401, 403, 429)},
    )
    def post(self, request: Request) -> Response:
        payload = CustomerStepUpSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        end = customer_auth.confirm_password(
            request, customer_of(request), payload.validated_data["password"]
        )
        body = {"step_up_until": datetime.fromtimestamp(end, tz=UTC)}
        return no_store(Response(StepUpResultSerializer(body).data))

    @extend_schema(
        operation_id="auth_step_up_forget",
        summary="Lock adding TV apps until the next confirmation",
        request=None,
        responses={204: OpenApiResponse(description="Locked."), **problems(401, 403)},
    )
    def delete(self, request: Request) -> Response:
        stepup.forget(request)
        return Response(status=204)


class MyPasswordView(CustomerView):
    """POST me/password (ADR-0023): change my password with the current one; my other
    sign-ins end (portal sessions, app sign-ins and their playback). TV logins keep
    working: they have their own passwords."""

    @extend_schema(
        operation_id="me_password_change",
        summary="Change my password; my other sign-ins end",
        request=PasswordChangeSerializer,
        responses={204: OpenApiResponse(description="Changed."), **problems(400, 401, 403, 429)},
    )
    def post(self, request: Request) -> Response:
        payload = PasswordChangeSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        customer_auth.change_password(
            request,
            customer_of(request),
            current=payload.validated_data["current_password"],
            new=payload.validated_data["new_password"],
        )
        return Response(status=204)


class MyMfaView(CustomerView):
    """me/mfa (ADR-0024): optional two-factor sign-in. POST starts setting it up (a new
    secret waiting for its first code at me/mfa/confirm); DELETE switches it off. Both
    after the password step-up. Sign-ins already open keep working."""

    permission_classes = (IsCustomer, CustomerStepUp)
    step_up_methods = frozenset({"POST", "DELETE"})

    @extend_schema(
        operation_id="me_mfa_setup",
        summary="Start setting up two-factor sign-in: the authenticator app's URI",
        request=None,
        responses={200: MfaSetupSerializer, **problems(401, 403, 409)},
    )
    def post(self, request: Request) -> Response:
        uri = customer_mfa.begin(customer_of(request))
        return no_store(Response(MfaSetupSerializer({"otpauth_uri": uri}).data))

    @extend_schema(
        operation_id="me_mfa_remove",
        summary="Switch two-factor sign-in off",
        request=None,
        responses={204: OpenApiResponse(description="Off."), **problems(401, 403)},
    )
    def delete(self, request: Request) -> Response:
        user = customer_of(request)
        customer_mfa.remove(user, actor=user, ip=client_ip(request), action="customer.mfa_disable")
        return Response(status=204)


class MyMfaConfirmView(CustomerView):
    """me/mfa/confirm (ADR-0024): the first code switches two-factor sign-in on."""

    permission_classes = (IsCustomer, CustomerStepUp)
    step_up_methods = frozenset({"POST"})

    @extend_schema(
        operation_id="me_mfa_confirm",
        summary="Switch two-factor sign-in on with the app's first code",
        request=MfaConfirmSerializer,
        responses={200: CustomerMeSerializer, **problems(400, 401, 403, 409, 429)},
    )
    def post(self, request: Request) -> Response:
        payload = MfaConfirmSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        user = customer_of(request)
        customer_mfa.confirm(user, payload.validated_data["code"], ip=client_ip(request))
        return no_store(Response(me_body(user)))


class MyDeviceListView(CustomerView):
    throttle_classes = (DeviceCredentialThrottle,)
    permission_classes = (IsCustomer, CustomerStepUp)
    step_up_methods = frozenset({"POST"})

    @extend_schema(
        operation_id="me_devices_list",
        summary="The customer's devices: IPTV apps, browsers and apps signed in",
        responses={200: MyDeviceSerializer(many=True), **problems(401, 403)},
    )
    def get(self, request: Request) -> Response:
        devices = customer_services.my_devices(customer_of(request))
        return Response(
            MyDeviceSerializer(devices, many=True, context=_device_context(request)).data
        )

    @extend_schema(
        operation_id="me_devices_create",
        summary='"Add TV app": a device with Xtream credentials, shown once',
        description="Username and password are generated unless given. Refused with "
        "DEVICE_LIMIT past the access's max_devices (only IPTV app devices count).",
        request=MyDeviceCreateSerializer,
        responses={201: MyIssuedCredentialSerializer, **problems(400, 401, 403, 409)},
    )
    def post(self, request: Request) -> Response:
        payload = MyDeviceCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        user = customer_of(request)
        issued = services.create_device_credential(
            user,
            name=payload.validated_data["name"],
            app_hint=payload.validated_data["app_hint"],
            username=payload.validated_data["username"],
            password=payload.validated_data["password"],
            actor=user,
            ip=client_ip(request),
        )
        body = MyIssuedCredentialSerializer(_issued_body(issued), context=_device_context(request))
        return no_store(Response(body.data, status=201))


class MyDeviceDetailView(CustomerView):
    @extend_schema(
        operation_id="me_devices_update",
        summary="Rename a device",
        request=MyDeviceUpdateSerializer,
        responses={200: MyDeviceSerializer, **problems(400, 401, 403, 404)},
    )
    def patch(self, request: Request, pk: UUID) -> Response:
        user = customer_of(request)
        device = customer_services.my_device(user, pk)
        payload = MyDeviceUpdateSerializer(data=request.data, partial=True)
        payload.is_valid(raise_exception=True)
        customer_services.rename_device(
            device, payload.validated_data, actor=user, ip=client_ip(request)
        )
        fresh = customer_services.my_device(user, pk)
        return Response(MyDeviceSerializer(fresh, context=_device_context(request)).data)

    @extend_schema(
        operation_id="me_devices_delete",
        summary="Remove a device: its credentials stop working and its playback stops",
        responses={204: None, **problems(401, 403, 404)},
    )
    def delete(self, request: Request, pk: UUID) -> Response:
        user = customer_of(request)
        device = customer_services.my_device(user, pk)
        services.revoke_device(device, actor=user, ip=client_ip(request))
        return Response(status=204)


class MyDeviceCredentialsView(CustomerView):
    throttle_classes = (DeviceCredentialThrottle,)
    permission_classes = (IsCustomer, CustomerStepUp)
    step_up_methods = frozenset({"POST"})

    @extend_schema(
        operation_id="me_devices_xtream_credentials",
        summary="New Xtream credentials for an IPTV app device, shown once",
        description="The old password stops working at once. Give a username to rename "
        "the login and a password to choose it; empty fields keep the username and "
        "generate a password.",
        request=MyCredentialResetSerializer,
        responses={200: MyIssuedCredentialSerializer, **problems(400, 401, 403, 404, 409)},
    )
    def post(self, request: Request, pk: UUID) -> Response:
        user = customer_of(request)
        device = customer_services.my_device(user, pk)
        payload = MyCredentialResetSerializer(data=request.data or {})
        payload.is_valid(raise_exception=True)
        issued = services.reset_credential(
            device,
            username=payload.validated_data["username"],
            password=payload.validated_data["password"],
            actor=user,
            ip=client_ip(request),
        )
        body = MyIssuedCredentialSerializer(_issued_body(issued), context=_device_context(request))
        return no_store(Response(body.data))


# --- Admin: password invitations --------------------------------------------------------------


class CustomerPasswordInviteView(APIView):
    step_up_methods = frozenset({"POST"})
    """POST /api/v1/admin/customers/{id}/password-invite (admin host)."""

    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"POST": "customers.edit"}

    @extend_schema(
        operation_id="customers_password_invite",
        summary="Invite the customer to set a portal password (single-use link)",
        description="Emails the link when the customer has an email and a sender is "
        "configured; the response holds the link either way, shown once.",
        request=None,
        responses={200: PasswordInvitationSerializer, **problems(401, 403, 404, 409)},
    )
    def post(self, request: Request, pk: UUID) -> Response:
        user = get_object_or_404(User.objects.filter(is_staff=False), pk=pk)
        invitation = customer_auth.invite_customer(
            user, actor=acting_user(request), ip=client_ip(request)
        )
        body = PasswordInvitationSerializer(
            {
                "url": invitation.link.url,
                "expires_at": invitation.link.expires_at,
                "emailed": invitation.emailed,
            }
        )
        return no_store(Response(body.data))


class CustomerResetMfaView(APIView):
    """POST /api/v1/admin/customers/{id}/reset-mfa (admin host; ADR-0024): support's
    recovery for a customer who lost their authenticator app. Their next sign-in needs
    the password alone; audited as `customer.mfa_reset`."""

    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"POST": "customers.edit"}
    step_up_methods = frozenset({"POST"})

    @extend_schema(
        operation_id="customers_reset_mfa",
        summary="Switch a customer's two-factor sign-in off (support's recovery)",
        request=None,
        responses={
            204: OpenApiResponse(description="Off; the next sign-in needs the password only."),
            **problems(401, 403, 404, 409),
        },
    )
    def post(self, request: Request, pk: UUID) -> Response:
        user = get_object_or_404(User.objects.filter(is_staff=False), pk=pk)
        removed = customer_mfa.remove(
            user, actor=acting_user(request), ip=client_ip(request), action="customer.mfa_reset"
        )
        if not removed:
            raise ProblemError(
                ErrorCode.CONFLICT, "Two-factor sign-in is not on for this customer.", status=409
            )
        return Response(status=204)


# --- OpenAPI: the two customer authentication schemes -------------------------------------------


class PortalSessionScheme(OpenApiAuthenticationExtension):
    target_class = "apps.accounts.customer_auth.PortalSessionAuthentication"
    name = "portalSession"

    def get_security_definition(self, auto_schema: AutoSchema) -> dict[str, str]:
        return {"type": "apiKey", "in": "cookie", "name": settings.SESSION_COOKIE_NAME}


class BearerTokenScheme(OpenApiAuthenticationExtension):
    target_class = "apps.accounts.customer_auth.BearerTokenAuthentication"
    name = "bearerToken"

    def get_security_definition(self, auto_schema: AutoSchema) -> dict[str, str]:
        return {"type": "http", "scheme": "bearer"}
