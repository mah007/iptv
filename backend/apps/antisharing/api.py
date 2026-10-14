"""Admin API of anti-sharing and IP geolocation (SPEC §11.1; G-27, ADR-0021).

- GET  customers/{id}/risk                    flag, live assessment and 7 days of sightings
- POST customers/{id}/risk/review             mark the open flag reviewed (with a note)
- POST customers/{id}/risk/warn               ladder step 1: warn the customer
- POST customers/{id}/risk/reset-credentials  ladder step 2: every IPTV login reset (step-up)
- POST customers/{id}/risk/suspend            ladder step 3: suspend (protective: no step-up)
- GET  geoip, POST geoip/update, POST geoip/lookup: the DB-IP Lite databases
"""

from datetime import timedelta
from typing import ClassVar
from uuid import UUID

from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.accounts.permissions import HasPermission, Requirements
from apps.antisharing import services
from apps.antisharing.models import Sighting
from apps.antisharing.serializers import (
    GeoIpQueuedSerializer,
    GeoIpStatusSerializer,
    GeoLookupRequestSerializer,
    GeoLookupSerializer,
    ResetResultSerializer,
    ReviewSerializer,
    RiskFlagSerializer,
    RiskOverviewSerializer,
)
from apps.audit import services as audit
from apps.core import geoip
from apps.core.http import acting_user, client_ip
from apps.core.schema import problems
from apps.core.services import get_setting

SIGHTINGS_SHOWN = 200


class AdminView(APIView):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {}


def _customer(pk: UUID) -> User:
    return get_object_or_404(User.objects.filter(is_staff=False), pk=pk)


class CustomerRiskView(AdminView):
    required_permissions: ClassVar[Requirements] = {"GET": "customers.view"}

    @extend_schema(
        operation_id="customers_risk",
        summary="Sharing signals: the flag, the score now, and where the customer was seen",
        responses={200: RiskOverviewSerializer, **problems(401, 403, 404)},
    )
    def get(self, request: Request, pk: UUID) -> Response:
        user = _customer(pk)
        now = timezone.now()
        sightings = (
            Sighting.objects.filter(user=user, last_seen__gte=now - timedelta(days=7))
            .select_related("device")
            .order_by("-last_seen")[:SIGHTINGS_SHOWN]
        )
        body = {
            "enabled": services.enabled(),
            "geoip_installed": geoip.available("country"),
            "flag": services.open_flag(user) or services.latest_flag(user),
            "assessment": services.assess(user.pk, now).as_dict(),
            "sightings": list(sightings),
            "attribution_url": geoip.ATTRIBUTION_URL,
        }
        return Response(RiskOverviewSerializer(body).data)


class CustomerRiskReviewView(AdminView):
    required_permissions: ClassVar[Requirements] = {"POST": "customers.edit"}

    @extend_schema(
        operation_id="customers_risk_review",
        summary="Mark the open sharing flag reviewed",
        request=ReviewSerializer,
        responses={200: RiskFlagSerializer, **problems(400, 401, 403, 404)},
    )
    def post(self, request: Request, pk: UUID) -> Response:
        payload = ReviewSerializer(data=request.data or {})
        payload.is_valid(raise_exception=True)
        flag = services.mark_reviewed(
            _customer(pk),
            actor=acting_user(request),
            ip=client_ip(request),
            note=payload.validated_data["note"],
        )
        return Response(RiskFlagSerializer(flag).data)


class CustomerRiskWarnView(AdminView):
    required_permissions: ClassVar[Requirements] = {"POST": "customers.edit"}

    @extend_schema(
        operation_id="customers_risk_warn",
        summary="Warn the customer that their account looks shared (an email)",
        request=None,
        responses={204: None, **problems(401, 403, 404)},
    )
    def post(self, request: Request, pk: UUID) -> Response:
        services.warn(_customer(pk), actor=acting_user(request), ip=client_ip(request))
        return Response(status=status.HTTP_204_NO_CONTENT)


class CustomerRiskResetView(AdminView):
    required_permissions: ClassVar[Requirements] = {"POST": "devices.manage"}
    step_up_methods = frozenset({"POST"})

    @extend_schema(
        operation_id="customers_risk_reset_credentials",
        summary="Reset every IPTV app login of the customer (they make new ones)",
        request=None,
        responses={200: ResetResultSerializer, **problems(401, 403, 404)},
    )
    def post(self, request: Request, pk: UUID) -> Response:
        count = services.reset_credentials(
            _customer(pk), actor=acting_user(request), ip=client_ip(request)
        )
        return Response(ResetResultSerializer({"logins": count}).data)


class CustomerRiskSuspendView(AdminView):
    required_permissions: ClassVar[Requirements] = {"POST": "customers.edit"}

    @extend_schema(
        operation_id="customers_risk_suspend",
        summary="Suspend the customer for sharing (playback stops)",
        request=None,
        responses={204: None, **problems(401, 403, 404, 409)},
    )
    def post(self, request: Request, pk: UUID) -> Response:
        services.suspend(_customer(pk), actor=acting_user(request), ip=client_ip(request))
        return Response(status=status.HTTP_204_NO_CONTENT)


# --- IP geolocation -----------------------------------------------------------------------------


class GeoIpView(AdminView):
    required_permissions: ClassVar[Requirements] = {"GET": "settings.view"}

    @extend_schema(
        operation_id="geoip_status",
        summary="The installed DB-IP Lite databases",
        responses={200: GeoIpStatusSerializer, **problems(401, 403)},
    )
    def get(self, request: Request) -> Response:
        body = {**geoip.status(), "auto_update": bool(get_setting("geoip.auto_update"))}
        return Response(GeoIpStatusSerializer(body).data)


class GeoIpUpdateView(AdminView):
    required_permissions: ClassVar[Requirements] = {"POST": "settings.edit"}
    step_up_methods = frozenset({"POST"})

    @extend_schema(
        operation_id="geoip_update",
        summary="Download this month's DB-IP Lite databases now (on the worker)",
        request=None,
        responses={202: GeoIpQueuedSerializer, **problems(401, 403)},
    )
    def post(self, request: Request) -> Response:
        from apps.antisharing.tasks import update_geoip  # noqa: PLC0415

        update_geoip.delay(force=True)
        audit.record("geoip.update", actor=acting_user(request), ip=client_ip(request))
        return Response({"queued": True}, status=status.HTTP_202_ACCEPTED)


class GeoIpLookupView(AdminView):
    required_permissions: ClassVar[Requirements] = {"POST": "settings.view"}

    @extend_schema(
        operation_id="geoip_lookup",
        summary="Test: what the databases say about an address (yours by default)",
        request=GeoLookupRequestSerializer,
        responses={200: GeoLookupSerializer, **problems(400, 401, 403)},
    )
    def post(self, request: Request) -> Response:
        payload = GeoLookupRequestSerializer(data=request.data or {})
        payload.is_valid(raise_exception=True)
        address = payload.validated_data.get("ip") or client_ip(request) or ""
        found = geoip.lookup(address)
        body = {
            "ip": address,
            "country": found.country or "",
            "asn": found.asn,
            "asn_org": found.asn_org,
            "local": found.local,
        }
        return Response(GeoLookupSerializer(body).data)
