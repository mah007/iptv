"""The monitoring sign-in's three doors (ADR-0018; the flow is in apps.accounts.monitoring).

- `MonitoringTicketView`: POST /api/v1/admin/monitoring/ticket on the admin host.
- `sso_callback` and `sso_logout`: grafana.<domain>/_sso/callback and /_sso/logout
  (config/urls_monitoring.py, the only Django routes on that host).
- `forward_auth`: /internal/monitoring-auth (config/urls_internal.py), asked by
  Traefik's forward-auth about every request to grafana.<domain>.
"""

from typing import Any, ClassVar

from django.conf import settings
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts import monitoring
from apps.accounts.models import User
from apps.accounts.permissions import HasPermission, Requirements
from apps.core.http import client_ip
from apps.core.schema import problems
from apps.core.services import get_setting


class MonitoringTicketSerializer(serializers.Serializer[Any]):
    next = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=monitoring.MAX_NEXT_LENGTH,
        help_text="Where to land on the monitoring host (a path, such as /d/iptv-edges).",
    )


class MonitoringTicketLinkSerializer(serializers.Serializer[Any]):
    url = serializers.URLField(
        help_text="Open this URL within a minute; it signs the admin in to monitoring once."
    )


class MonitoringTicketView(APIView):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"POST": monitoring.PERMISSION}

    @extend_schema(
        operation_id="monitoring_ticket",
        summary="A one-time link that signs this admin in to Grafana, Prometheus and Alertmanager",
        request=MonitoringTicketSerializer,
        responses={200: MonitoringTicketLinkSerializer, **problems(400, 401, 403)},
    )
    def post(self, request: Request) -> Response:
        body = MonitoringTicketSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        user = request.user
        if not isinstance(user, User):  # HasPermission admits signed-in staff only
            raise PermissionDenied
        url = monitoring.issue_ticket(
            user, body.validated_data.get("next") or "/", ip=client_ip(request)
        )
        response = Response(MonitoringTicketLinkSerializer({"url": url}).data)
        response["Cache-Control"] = "no-store"
        return response


def _no_referrer(response: HttpResponse) -> HttpResponse:
    response["Referrer-Policy"] = "no-referrer"
    return response


@never_cache
@require_GET
def sso_callback(request: HttpRequest) -> HttpResponse:
    """Swap a ticket for a monitoring session cookie, then land on Grafana."""
    redeemed = monitoring.redeem_ticket(request.GET.get("ticket"))
    if redeemed is None:
        failed = f"{monitoring.admin_origin()}{monitoring.ADMIN_PAGE}?failed=1"
        return _no_referrer(HttpResponseRedirect(failed))
    session, next_path = redeemed
    response = HttpResponseRedirect(next_path)
    response.set_cookie(
        monitoring.cookie_name(),
        session,
        max_age=int(get_setting("security.monitoring_session_hours")) * 3600,
        path="/",
        secure=settings.SESSION_COOKIE_SECURE,
        httponly=True,
        samesite="Lax",
    )
    return _no_referrer(response)


@never_cache
@require_GET
def sso_logout(request: HttpRequest) -> HttpResponse:
    """Grafana's sign-out: end the monitoring session and go back to the admin."""
    monitoring.end_session(request.COOKIES.get(monitoring.cookie_name()))
    response = HttpResponseRedirect(f"{monitoring.admin_origin()}/")
    response.delete_cookie(monitoring.cookie_name(), path="/", samesite="Lax")
    return _no_referrer(response)


@csrf_exempt
@never_cache
def forward_auth(request: HttpRequest) -> HttpResponse:
    """Traefik's question about one request to grafana.<domain>: who is this?

    200 with the identity headers Grafana trusts; otherwise a page navigation is sent
    to the admin to sign in (and come back to the same page), and anything else (API
    calls from an open Grafana tab) gets a plain 401.
    """
    identity = monitoring.authorize(request.COOKIES.get(monitoring.cookie_name()))
    if identity is not None:
        response = HttpResponse(status=200)
        response["X-WEBAUTH-USER"] = identity.login
        response["X-WEBAUTH-NAME"] = identity.name
        response["X-WEBAUTH-ROLE"] = identity.role
        return response
    method = request.META.get("HTTP_X_FORWARDED_METHOD", request.method or "GET").upper()
    navigation = method in {"GET", "HEAD"} and "text/html" in request.META.get("HTTP_ACCEPT", "")
    if not navigation:
        return HttpResponse(status=401)
    target = monitoring.admin_sign_in_url(request.META.get("HTTP_X_FORWARDED_URI", "/"))
    return HttpResponseRedirect(target)
