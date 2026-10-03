"""Admin API of the core app: the settings registry (SPEC §8.3 Settings)."""

from typing import ClassVar

from django.core.exceptions import ValidationError
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import HasPermission, Requirements
from apps.core import services
from apps.core.errors import ErrorCode, ProblemError
from apps.core.http import acting_user, client_ip
from apps.core.registry import UnknownSettingError
from apps.core.serializers import SettingEntrySerializer, SettingSerializer


class SettingListView(APIView):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"GET": "settings.view"}

    @extend_schema(
        operation_id="settings_list",
        summary="List every setting with its effective value",
        responses=SettingEntrySerializer(many=True),
    )
    def get(self, request: Request) -> Response:
        states = services.list_settings()
        return Response(
            serializers.ListSerializer(child=SettingEntrySerializer(), instance=states).data
        )


class SettingDetailView(APIView):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"PATCH": "settings.edit"}

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
            raise ProblemError(ErrorCode.NOT_FOUND, f"Unknown setting {key}.") from None
        except ValidationError as exc:
            raise ProblemError(
                ErrorCode.VALIDATION_ERROR,
                exc.messages[0],
                field_errors={"value": exc.messages},
            ) from None
        return Response(SettingEntrySerializer(state).data)
