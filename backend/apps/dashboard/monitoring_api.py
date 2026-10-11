"""Admin API of the monitoring overlay (ADR-0018), beside the sign-in ticket:

- GET  /api/v1/admin/monitoring             what runs, and the values that need a restart
- POST /api/v1/admin/monitoring/test-alert  the fire drill: a real alert, end to end
"""

from typing import Any, ClassVar

from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts import monitoring
from apps.accounts.permissions import HasPermission, Requirements
from apps.core.errors import ErrorCode, ProblemError
from apps.core.http import acting_user, client_ip
from apps.core.schema import problems
from apps.core.services import get_setting
from apps.dashboard import metrics


class MonitoringDeploymentSerializer(serializers.Serializer[Any]):
    """Set when the server is installed or reconfigured (.env); they need a restart."""

    prometheus_retention = serializers.CharField(help_text="How long metrics are kept.")
    prometheus_retention_size = serializers.CharField(help_text="Most disk metrics may use.")
    loki_retention = serializers.CharField(help_text="How long logs are kept.")
    fallback_email = serializers.BooleanField(
        help_text="Alertmanager emails the alerts about web being down itself (the settings' "
        "SMTP and recipients, fetched from Django)."
    )
    fallback_telegram = serializers.BooleanField(
        help_text="Alertmanager sends those alerts to Telegram itself (the settings' bot)."
    )


class MonitoringStatusSerializer(serializers.Serializer[Any]):
    enabled = serializers.BooleanField(help_text="The monitoring overlay runs on this server.")
    grafana_url = serializers.CharField(help_text="Where Grafana lives; empty when off.")
    alerts_enabled = serializers.BooleanField()
    email_recipients = serializers.IntegerField(help_text="How many addresses get alert emails.")
    email_configured = serializers.BooleanField(help_text="An SMTP server is set.")
    telegram_configured = serializers.BooleanField()
    fire_drill_active = serializers.BooleanField()
    deployment = MonitoringDeploymentSerializer()


class TestAlertSerializer(serializers.Serializer[Any]):
    minutes = serializers.IntegerField(
        required=False,
        min_value=metrics.FIRE_DRILL_MIN_MINUTES,
        max_value=metrics.FIRE_DRILL_MAX_MINUTES,
        help_text="How long the test alert fires (default 10).",
    )
    stop = serializers.BooleanField(required=False, help_text="End a running test alert.")


class TestAlertStateSerializer(serializers.Serializer[Any]):
    active = serializers.BooleanField()
    until = serializers.DateTimeField(allow_null=True)


class MonitoringStatusView(APIView):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"GET": monitoring.PERMISSION}

    @extend_schema(
        operation_id="monitoring_status",
        summary="The monitoring overlay: what runs, alert delivery, values that need a restart",
        responses={200: MonitoringStatusSerializer, **problems(401, 403)},
    )
    def get(self, request: Request) -> Response:
        # Local: notifications imports the registry.
        from apps.notifications import alerts, telegram  # noqa: PLC0415
        from apps.notifications.services import email_configured  # noqa: PLC0415

        deployment = dict(settings.MONITORING_DEPLOYMENT)
        fallback = alerts.fallback_status()
        status = {
            "enabled": monitoring.monitoring_enabled(),
            "grafana_url": str(settings.GRAFANA_URL),
            "alerts_enabled": bool(get_setting("monitoring.alerts_enabled")),
            "email_recipients": len(alerts.recipients()),
            "email_configured": email_configured(),
            "telegram_configured": telegram.configured(),
            "fire_drill_active": metrics.fire_drill_active(),
            "deployment": {
                **deployment,
                "fallback_email": fallback["email"],
                "fallback_telegram": fallback["telegram"],
            },
        }
        return Response(MonitoringStatusSerializer(status).data)


class MonitoringTestAlertView(APIView):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"POST": "settings.edit"}

    @extend_schema(
        operation_id="monitoring_test_alert",
        summary="Send a test alert (the fire drill) through Prometheus, Alertmanager and delivery",
        description="404 when the monitoring overlay is not enabled on this server.",
        request=TestAlertSerializer,
        responses={200: TestAlertStateSerializer, **problems(400, 401, 403, 404)},
    )
    def post(self, request: Request) -> Response:
        if not monitoring.monitoring_enabled():
            raise ProblemError(ErrorCode.NOT_FOUND, "Monitoring is not enabled on this server.")
        body = TestAlertSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        actor, ip = acting_user(request), client_ip(request)
        if body.validated_data.get("stop"):
            metrics.stop_fire_drill(actor=actor, ip=ip)
            return Response(TestAlertStateSerializer({"active": False, "until": None}).data)
        until = metrics.start_fire_drill(body.validated_data.get("minutes", 10), actor=actor, ip=ip)
        return Response(TestAlertStateSerializer({"active": True, "until": until}).data)
