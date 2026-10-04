"""Customer billing API (SPEC §10 Billing, Me): plans, checkout, my subscription and
invoices on api.<domain> (bearer tokens) and app.<domain> (the portal's session),
plus provider webhooks on api.<domain> (ADR-0012).

Customer authentication is the accounts app's (ADR-0013): `CUSTOMER_AUTHENTICATION`
and `IsCustomer`. Plans are public: the portal's pricing page shows them before
sign-in.
"""

from typing import Any
from uuid import UUID

from django.db.models import QuerySet
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import generics
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.customer_auth import CUSTOMER_AUTHENTICATION, IsCustomer, customer_of
from apps.billing import services, webhooks
from apps.billing.api import LOCALE_PARAMETER, invoice_document
from apps.billing.models import (
    Invoice,
    Plan,
    Subscription,
    SubscriptionStatus,
)
from apps.billing.providers import enabled_providers
from apps.billing.serializers import (
    CheckoutRequestSerializer,
    CheckoutResponseSerializer,
    CustomerInvoiceSerializer,
    MySubscriptionSerializer,
    ProviderSerializer,
    PublicPlanSerializer,
)
from apps.core.http import client_ip
from apps.core.schema import problems
from apps.playback.entitlements import governing_subscription


class CustomerView(APIView):
    authentication_classes = CUSTOMER_AUTHENTICATION
    permission_classes = (IsCustomer,)


class PublicPlanListView(APIView):
    authentication_classes = ()
    permission_classes = (AllowAny,)

    @extend_schema(
        operation_id="billing_plans",
        summary="Plans on sale, with prices including VAT",
        responses={200: PublicPlanSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        rows = Plan.objects.filter(active=True).prefetch_related("categories")
        return Response(PublicPlanSerializer(rows.order_by("sort", "created_at"), many=True).data)


class ProviderListView(CustomerView):
    @extend_schema(
        operation_id="billing_providers",
        summary="Payment methods offered at checkout",
        responses={200: ProviderSerializer(many=True), **problems(401)},
    )
    def get(self, request: Request) -> Response:
        rows = [{"code": provider.code, "name": provider.name} for provider in enabled_providers()]
        return Response(ProviderSerializer(rows, many=True).data)


class CheckoutView(CustomerView):
    @extend_schema(
        operation_id="billing_checkout",
        summary="Start paying for a plan (or start a free trial)",
        description="Creates a pending invoice and the provider's checkout: `redirect` sends "
        "the customer to `redirect_url`; `manual` shows bank transfer instructions with the "
        "reference to quote; `trial` started (or requested) a free trial.",
        request=CheckoutRequestSerializer,
        responses={201: CheckoutResponseSerializer, **problems(400, 401, 404, 409)},
    )
    def post(self, request: Request) -> Response:
        payload = CheckoutRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        result = services.checkout(
            customer_of(request),
            get_object_or_404(Plan, pk=data["plan_id"], active=True),
            data["provider"],
            return_url=data["return_url"],
            ip=client_ip(request),
        )
        session = result.session
        body: dict[str, Any] = {
            "kind": "trial"
            if result.subscription is not None
            else ("redirect" if session and session.redirect_url else "manual"),
            "provider": session.provider if session else None,
            "invoice": result.invoice,
            "redirect_url": session.redirect_url if session else None,
            "reference": session.reference if session else "",
            "instructions": session.instructions if session else {},
            "subscription": result.subscription,
        }
        return Response(CheckoutResponseSerializer(body).data, status=201)


class MySubscriptionView(CustomerView):
    @extend_schema(
        operation_id="me_subscription",
        summary="My subscription: the current one (or the last that ended) and any waiting one",
        responses={200: MySubscriptionSerializer, **problems(401)},
    )
    def get(self, request: Request) -> Response:
        rows = list(
            Subscription.objects.filter(user=customer_of(request))
            .select_related("plan")
            .order_by("-ends_at")
        )
        pending = next((row for row in rows if row.status == SubscriptionStatus.PENDING), None)
        body = {"subscription": governing_subscription(rows), "pending": pending}
        return Response(MySubscriptionSerializer(body).data)


class MyInvoiceListView(generics.ListAPIView[Invoice]):
    authentication_classes = CUSTOMER_AUTHENTICATION
    permission_classes = (IsCustomer,)
    serializer_class = CustomerInvoiceSerializer
    filter_backends = ()

    @extend_schema(
        operation_id="me_invoices",
        summary="My invoices, newest first (paid, refunded and awaiting payment)",
        responses={200: CustomerInvoiceSerializer(many=True), **problems(401)},
    )
    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return super().get(request, *args, **kwargs)

    def get_queryset(self) -> QuerySet[Invoice]:
        return (
            Invoice.objects.filter(user=customer_of(self.request))
            .exclude(status="void")
            .select_related("plan")
            .order_by("-created_at", "-id")
        )


class MyInvoiceDocumentView(CustomerView):
    @extend_schema(
        operation_id="me_invoice_document",
        summary="One of my invoices as a print-ready HTML page",
        parameters=[LOCALE_PARAMETER],
        responses={
            (200, "text/html"): OpenApiResponse(OpenApiTypes.STR, description="The page."),
            **problems(401, 404),
        },
    )
    def get(self, request: Request, pk: UUID) -> HttpResponse:
        invoice = get_object_or_404(
            Invoice.objects.select_related("user", "plan").prefetch_related("payments"),
            pk=pk,
            user=customer_of(request),
        )
        return invoice_document(invoice, request)


class WebhookView(APIView):
    """Provider webhooks: no session, no CSRF; the adapter verifies the signature."""

    authentication_classes = ()
    permission_classes = (AllowAny,)

    @extend_schema(
        operation_id="billing_webhook",
        summary="A payment provider's event (Stripe, Moyasar)",
        request=OpenApiTypes.OBJECT,
        responses={200: OpenApiTypes.OBJECT, **problems(400, 404)},
    )
    def post(self, request: Request, provider: str) -> Response:
        outcome = webhooks.receive(provider, request._request)
        if outcome.error:
            return Response({"received": True, "processed": False}, status=500)
        return Response({"received": True, "duplicate": outcome.duplicate})
