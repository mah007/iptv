"""Admin API of the Integrations and System pages (CFG, ADR-0019):

- GET  /api/v1/admin/integrations              one card per integration, with its settings
- POST /api/v1/admin/integrations/{name}/test  test it with the saved settings
- GET  /api/v1/admin/system                    read-only facts about this installation

Integration settings are changed through the settings endpoints (PATCH settings).
"""

from typing import Any, ClassVar

from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import HasPermission, Requirements
from apps.core.http import acting_user, client_ip
from apps.core.integration import TestCode
from apps.core.schema import problems
from apps.core.serializers import SettingEntrySerializer
from apps.dashboard import integrations, system


class IntegrationSerializer(serializers.Serializer[Any]):
    name = serializers.ChoiceField(
        choices=[(name.value, name.name) for name in integrations.IntegrationName]
    )
    status = serializers.ChoiceField(
        choices=[(status.value, status.name) for status in integrations.IntegrationStatus],
        help_text="configured: stored in the settings; server_config: complete, partly from "
        ".env; partial: something it needs is missing; not_set: nothing is set.",
    )
    enabled = serializers.BooleanField(
        allow_null=True, help_text="Offered to customers (payment providers); null otherwise."
    )
    webhook_url = serializers.CharField(
        allow_null=True, help_text="Where the provider must send its webhooks."
    )
    settings = SettingEntrySerializer(many=True)


class IntegrationTestResultSerializer(serializers.Serializer[Any]):
    ok = serializers.BooleanField()
    code = serializers.ChoiceField(choices=[(code.value, code.name) for code in TestCode])
    message = serializers.CharField(help_text="A short English explanation, without secrets.")
    details = serializers.DictField(
        child=serializers.JSONField(),
        help_text="Harmless facts: the bot's name, test or live mode, the email's reference.",
    )


class SystemHostSerializer(serializers.Serializer[Any]):
    role = serializers.CharField()
    host = serializers.CharField()
    url = serializers.CharField()


class ServerConfigSettingSerializer(serializers.Serializer[Any]):
    key = serializers.CharField()
    env = serializers.ListField(child=serializers.CharField())


class BootstrapSecretSerializer(serializers.Serializer[Any]):
    name = serializers.CharField()
    reason = serializers.ChoiceField(
        choices=[(reason.value, reason.name) for reason in system.SecretReason]
    )
    location = serializers.ChoiceField(
        choices=[(location.value, location.name) for location in system.SecretLocation]
    )
    required = serializers.BooleanField()  # type: ignore[assignment]


class KeyRotationSerializer(serializers.Serializer[Any]):
    kind = serializers.CharField()
    at = serializers.DateTimeField()
    actor = serializers.CharField(allow_null=True)


class SystemInfoSerializer(serializers.Serializer[Any]):
    environment = serializers.ChoiceField(
        choices=[(env.value, env.name) for env in system.SystemEnvironment]
    )
    version = serializers.CharField()
    commit = serializers.CharField(help_text="The git commit this installation runs; may be empty.")
    domain = serializers.CharField()
    public_scheme = serializers.CharField()
    hosts = SystemHostSerializer(many=True)
    media_root = serializers.CharField(help_text="The host folder holding the libraries.")
    library_root = serializers.CharField()
    data_root = serializers.CharField()
    monitoring_enabled = serializers.BooleanField()
    live_ingest_enabled = serializers.BooleanField()
    server_time = serializers.DateTimeField()
    time_zone = serializers.CharField()
    from_server_config = ServerConfigSettingSerializer(
        many=True, help_text="Settings whose value still comes from .env."
    )
    secrets = BootstrapSecretSerializer(
        many=True, help_text="What stays in .env or secrets/ and why; never a value."
    )
    key_rotations = KeyRotationSerializer(many=True)


class IntegrationListView(APIView):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"GET": "settings.view"}

    @extend_schema(
        operation_id="integrations_list",
        summary="Every integration with its status and settings (secrets never sent)",
        responses={200: IntegrationSerializer(many=True), **problems(401, 403)},
    )
    def get(self, request: Request) -> Response:
        return Response(IntegrationSerializer(integrations.cards(), many=True).data)


class IntegrationTestView(APIView):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"POST": "settings.edit"}
    step_up_methods = frozenset({"POST"})

    @extend_schema(
        operation_id="integrations_test",
        summary="Test an integration with its saved settings",
        description="Email sends a test message to the signed-in admin; Telegram sends one "
        "to the admins' chat. A failed test still answers 200, with ok false and a code.",
        request=None,
        responses={200: IntegrationTestResultSerializer, **problems(401, 403, 404, 429)},
    )
    def post(self, request: Request, name: str) -> Response:
        actor = acting_user(request)
        assert actor is not None  # noqa: S101 (HasPermission lets only admins through)
        result = integrations.run_test(name, actor=actor, ip=client_ip(request))
        payload = {
            "ok": result.ok,
            "code": result.code.value,
            "message": result.message,
            "details": dict(result.details),
        }
        return Response(IntegrationTestResultSerializer(payload).data)


class SystemInfoView(APIView):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"GET": "settings.view"}

    @extend_schema(
        operation_id="system_info",
        summary="Read-only facts about this installation (hosts, version, bootstrap secrets)",
        responses={200: SystemInfoSerializer, **problems(401, 403)},
    )
    def get(self, request: Request) -> Response:
        return Response(SystemInfoSerializer(system.system_info()).data)
