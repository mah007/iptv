"""Admin API of the core app: the settings registry (SPEC §8.3 Settings; ADR-0019).

- GET   settings                every setting with its effective value and source
- PATCH settings                several at once, all or nothing (null resets one)
- PATCH settings/{key}          change one
- DELETE settings/{key}         forget the stored value (server config or default again)
- POST  settings/{key}/adopt    move the server config's value into the settings
- POST  settings/{key}/image    upload a logo (image settings)

Secret values are never returned. Every write needs `settings.edit`.
"""

from typing import Any, ClassVar, NoReturn

from django.core.exceptions import ValidationError
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import HasPermission, Requirements
from apps.core import branding, services
from apps.core.errors import ErrorCode, ProblemError, field_error
from apps.core.http import acting_user, client_ip
from apps.core.registry import UnknownSettingError, get_definition
from apps.core.schema import problems
from apps.core.serializers import (
    SettingEntrySerializer,
    SettingImageSerializer,
    SettingsBulkSerializer,
    SettingSerializer,
)


def _unknown(key: str) -> NoReturn:
    raise ProblemError(ErrorCode.NOT_FOUND, f"Unknown setting {key}.")


def _invalid(exc: ValidationError, field: str = "value") -> NoReturn:
    code = getattr(exc, "code", None) or "invalid"
    raise ProblemError(
        ErrorCode.VALIDATION_ERROR,
        exc.messages[0],
        field_errors={field: [field_error(message, code=code) for message in exc.messages]},
    ) from None


def _entries(states: list[services.SettingState]) -> Any:
    return serializers.ListSerializer(child=SettingEntrySerializer(), instance=states).data


class SettingListView(APIView):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {
        "GET": "settings.view",
        "PATCH": "settings.edit",
    }

    @extend_schema(
        operation_id="settings_list",
        summary="List every setting with its effective value",
        responses=SettingEntrySerializer(many=True),
    )
    def get(self, request: Request) -> Response:
        return Response(_entries(services.list_settings()))

    @extend_schema(
        operation_id="settings_bulk_update",
        summary="Change several settings at once, all or nothing",
        description="A value stores the setting; null forgets the stored value. Field errors "
        "are keyed `values.<setting key>`. Answers the changed settings.",
        request=SettingsBulkSerializer,
        responses={200: SettingEntrySerializer(many=True), **problems(400, 401, 403, 404)},
    )
    def patch(self, request: Request) -> Response:
        body = SettingsBulkSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        changes: dict[str, object | None] = body.validated_data["values"]
        try:
            states = services.set_settings(
                changes, actor=acting_user(request), ip=client_ip(request)
            )
        except UnknownSettingError as exc:
            _unknown(str(exc.args[0]))
        except services.SettingsValidationError as exc:
            raise ProblemError(
                ErrorCode.VALIDATION_ERROR,
                exc.messages[0],
                field_errors={
                    f"values.{key}": [field_error(m, code="invalid") for m in messages]
                    for key, messages in exc.errors.items()
                },
            ) from None
        return Response(_entries(states))


class SettingDetailView(APIView):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {
        "PATCH": "settings.edit",
        "DELETE": "settings.edit",
    }

    @extend_schema(
        operation_id="settings_update",
        summary="Change a setting; it applies everywhere without a restart",
        request=SettingSerializer,
        responses=SettingEntrySerializer,
    )
    def patch(self, request: Request, key: str) -> Response:
        payload = SettingSerializer(data=request.data, partial=True)
        payload.is_valid(raise_exception=True)
        try:
            if "value" not in payload.validated_data:
                return Response(SettingEntrySerializer(services.setting_state(key)).data)
            state = services.set_setting(
                key,
                payload.validated_data["value"],
                actor=acting_user(request),
                ip=client_ip(request),
            )
        except UnknownSettingError:
            _unknown(key)
        except ValidationError as exc:
            _invalid(exc)
        return Response(SettingEntrySerializer(state).data)

    @extend_schema(
        operation_id="settings_reset",
        summary="Forget the stored value: the server config's or the default applies again",
        request=None,
        responses={200: SettingEntrySerializer, **problems(401, 403, 404)},
    )
    def delete(self, request: Request, key: str) -> Response:
        try:
            definition = get_definition(key)
            previous = "" if definition.widget != "image" else str(services.get_setting(key) or "")
            state = services.reset_setting(key, actor=acting_user(request), ip=client_ip(request))
        except UnknownSettingError:
            _unknown(key)
        if previous and state.value != previous:
            from apps.core import tasks  # noqa: PLC0415 (tasks import the services)

            tasks.delete_branding_image.delay(previous)
        return Response(SettingEntrySerializer(state).data)


class SettingAdoptView(APIView):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"POST": "settings.edit"}

    @extend_schema(
        operation_id="settings_adopt",
        summary="Move the server config's value (.env) into the settings",
        description="The value is copied on the server (encrypted for a secret) and never "
        "sent; afterwards the variable can be removed from .env.",
        request=None,
        responses={200: SettingEntrySerializer, **problems(400, 401, 403, 404)},
    )
    def post(self, request: Request, key: str) -> Response:
        try:
            state = services.adopt_setting(key, actor=acting_user(request), ip=client_ip(request))
        except UnknownSettingError:
            _unknown(key)
        except ValidationError as exc:
            _invalid(exc)
        return Response(SettingEntrySerializer(state).data)


class SettingImageView(APIView):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"POST": "settings.edit"}
    parser_classes = (MultiPartParser,)

    @extend_schema(
        operation_id="settings_image_upload",
        summary="Upload a logo for an image setting",
        description="Checked here and stored by a worker (a PNG of at most 512 px); the "
        "setting changes when it is stored, a few seconds later.",
        request={"multipart/form-data": SettingImageSerializer},
        responses={202: None, **problems(400, 401, 403, 404)},
    )
    def post(self, request: Request, key: str) -> Response:
        upload = request.FILES.get("file")
        if upload is None:
            raise ProblemError(
                ErrorCode.VALIDATION_ERROR,
                "Choose an image.",
                field_errors={"file": [field_error("Choose an image.", code="required")]},
            )
        if upload.size is not None and upload.size > branding.LOGO_MAX_BYTES:
            _invalid(ValidationError("The image is too large (2 MB at most).", code="too_large"))
        try:
            branding.queue_logo(
                key, upload.read(), actor=acting_user(request), ip=client_ip(request)
            )
        except UnknownSettingError:
            _unknown(key)
        except ValidationError as exc:
            _invalid(exc, "file")
        return Response(status=status.HTTP_202_ACCEPTED)


class BrandingNamesSerializer(serializers.Serializer[Any]):
    en = serializers.CharField()
    ar = serializers.CharField()


class BrandingSupportSerializer(serializers.Serializer[Any]):
    email = serializers.CharField()
    phone = serializers.CharField()
    whatsapp = serializers.CharField()


class BrandingSerializer(serializers.Serializer[Any]):
    service_name = BrandingNamesSerializer()
    accent_color = serializers.CharField()
    logo_light_url = serializers.CharField(allow_null=True)
    logo_dark_url = serializers.CharField(allow_null=True)
    support = BrandingSupportSerializer()
    default_language = serializers.ChoiceField(choices=[("ar", "ar"), ("en", "en")])


class BrandingView(APIView):
    """Public: every app shows the admin's branding, sign-in pages included."""

    authentication_classes = ()
    permission_classes = (AllowAny,)

    @extend_schema(
        operation_id="branding_retrieve",
        summary="The service's name, logos, colour and support contacts",
        auth=[],
        responses={200: BrandingSerializer},
    )
    def get(self, request: Request) -> Response:
        response = Response(BrandingSerializer(branding.public_branding()).data)
        response["Cache-Control"] = "public, max-age=60"
        return response
