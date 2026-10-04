"""Admin API of the billing app (SPEC §8.3 Plans, Subscriptions, Payments, Invoices; §10).

Views validate with serializers, call the services (which audit every change) and
answer with response serializers. Lists load related rows in fixed queries.
"""

from typing import Any, ClassVar
from uuid import UUID

from django.db.models import Count, Prefetch, Q, QuerySet
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from rest_framework import filters, generics
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.accounts.permissions import HasPermission, Requirements
from apps.billing import invoices, kpis, services, subscriptions
from apps.billing.filters import InvoiceFilter, PaymentFilter, PlanFilter, SubscriptionFilter
from apps.billing.models import (
    CURRENT_STATUSES,
    Invoice,
    Payment,
    Plan,
    Subscription,
    WebhookEvent,
)
from apps.billing.serializers import (
    BillingKpisSerializer,
    ChangePlanSerializer,
    ExtendSerializer,
    InvoiceSerializer,
    ManualPaymentSerializer,
    MigratedSerializer,
    PaymentDetailSerializer,
    PaymentSerializer,
    PlanSerializer,
    PlanWriteSerializer,
    ReasonSerializer,
    RefundSerializer,
    ReorderSerializer,
    SubscriptionCreateSerializer,
    SubscriptionSerializer,
    TrialStartSerializer,
)
from apps.core.http import acting_user, client_ip
from apps.core.schema import problems


class AdminView(APIView):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {}


def customer(pk: UUID) -> User:
    return get_object_or_404(User.objects.filter(is_staff=False), pk=pk)


# --- Plans ----------------------------------------------------------------------------------


def plans() -> QuerySet[Plan]:
    return Plan.objects.prefetch_related("categories").annotate(
        subscribers=Count("subscriptions", filter=Q(subscriptions__status__in=CURRENT_STATUSES))
    )


def plan_detail(pk: UUID) -> Plan:
    return get_object_or_404(plans(), pk=pk)


def _plan_values(data: dict[str, Any]) -> tuple[dict[str, Any], list[Any] | None]:
    values = dict(data)
    category_ids = values.pop("category_ids", None)
    return values, category_ids


@extend_schema_view(
    get=extend_schema(
        operation_id="plans_list",
        summary="Plans, in their order, with their current subscriber counts",
        responses={200: PlanSerializer(many=True), **problems(401, 403)},
    ),
    post=extend_schema(
        operation_id="plans_create",
        summary="Create a plan",
        request=PlanWriteSerializer,
        responses={201: PlanSerializer, **problems(400, 401, 403)},
    ),
)
class PlanListView(generics.ListCreateAPIView[Plan]):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"GET": "plans.view", "POST": "plans.edit"}
    serializer_class = PlanSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = PlanFilter
    pagination_class = None

    def get_queryset(self) -> QuerySet[Plan]:
        return plans().order_by("sort", "created_at")

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        payload = PlanWriteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        values, category_ids = _plan_values(payload.validated_data)
        plan = services.create_plan(
            values,
            category_ids=category_ids or [],
            actor=acting_user(request),
            ip=client_ip(request),
        )
        return Response(PlanSerializer(plan_detail(plan.pk)).data, status=201)


class PlanDetailView(AdminView):
    required_permissions: ClassVar[Requirements] = {
        "GET": "plans.view",
        "PATCH": "plans.edit",
        "DELETE": "plans.edit",
    }

    @extend_schema(
        operation_id="plans_retrieve",
        summary="A plan",
        responses={200: PlanSerializer, **problems(401, 403, 404)},
    )
    def get(self, request: Request, pk: UUID) -> Response:
        return Response(PlanSerializer(plan_detail(pk)).data)

    @extend_schema(
        operation_id="plans_update",
        summary="Change a plan (a new version for new subscriptions)",
        description="Existing subscriptions keep their snapshot; see migrate-subscriptions.",
        request=PlanWriteSerializer(partial=True),
        responses={200: PlanSerializer, **problems(400, 401, 403, 404)},
    )
    def patch(self, request: Request, pk: UUID) -> Response:
        plan = get_object_or_404(Plan, pk=pk)
        payload = PlanWriteSerializer(data=request.data, partial=True)
        payload.is_valid(raise_exception=True)
        values, category_ids = _plan_values(payload.validated_data)
        services.update_plan(
            plan,
            values,
            category_ids=category_ids,
            actor=acting_user(request),
            ip=client_ip(request),
        )
        return Response(PlanSerializer(plan_detail(pk)).data)

    @extend_schema(
        operation_id="plans_destroy",
        summary="Delete a plan nobody bought (else switch it off)",
        responses={204: None, **problems(401, 403, 404, 409)},
    )
    def delete(self, request: Request, pk: UUID) -> Response:
        plan = get_object_or_404(Plan, pk=pk)
        services.delete_plan(plan, actor=acting_user(request), ip=client_ip(request))
        return Response(status=204)


class PlanReorderView(AdminView):
    required_permissions: ClassVar[Requirements] = {"POST": "plans.edit"}

    @extend_schema(
        operation_id="plans_reorder",
        summary="Put the plans in this order",
        request=ReorderSerializer,
        responses={200: PlanSerializer(many=True), **problems(400, 401, 403)},
    )
    def post(self, request: Request) -> Response:
        payload = ReorderSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        services.reorder_plans(
            payload.validated_data["ids"], actor=acting_user(request), ip=client_ip(request)
        )
        return Response(PlanSerializer(plans().order_by("sort", "created_at"), many=True).data)


class PlanMigrateView(AdminView):
    required_permissions: ClassVar[Requirements] = {"POST": "plans.edit"}

    @extend_schema(
        operation_id="plans_migrate_subscriptions",
        summary="Give the plan's current subscriptions its latest version",
        request=None,
        responses={200: MigratedSerializer, **problems(401, 403, 404)},
    )
    def post(self, request: Request, pk: UUID) -> Response:
        plan = get_object_or_404(Plan, pk=pk)
        count = services.migrate_subscriptions(
            plan, actor=acting_user(request), ip=client_ip(request)
        )
        return Response({"migrated": count})


# --- Subscriptions --------------------------------------------------------------------------


def subscriptions_queryset() -> QuerySet[Subscription]:
    return Subscription.objects.select_related("user", "plan", "created_by")


def subscription_detail(pk: UUID) -> Subscription:
    return get_object_or_404(subscriptions_queryset(), pk=pk)


@extend_schema_view(
    get=extend_schema(
        operation_id="subscriptions_list",
        summary="Subscriptions, newest first",
        responses={200: SubscriptionSerializer(many=True), **problems(400, 401, 403)},
    ),
    post=extend_schema(
        operation_id="subscriptions_create",
        summary="Activate or extend a customer's subscription (subscriptions.activate)",
        description="With a current subscription, it is extended from its end and moved to "
        "the plan; otherwise a new one starts at starts_at (default now).",
        request=SubscriptionCreateSerializer,
        responses={201: SubscriptionSerializer, **problems(400, 401, 403, 404, 409)},
    ),
)
class SubscriptionListView(generics.ListCreateAPIView[Subscription]):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {
        "GET": "subscriptions.view",
        "POST": "subscriptions.edit",
    }
    serializer_class = SubscriptionSerializer
    filter_backends = (DjangoFilterBackend, filters.SearchFilter)
    filterset_class = SubscriptionFilter
    search_fields = ("user__name", "user__email", "user__phone", "user__username", "plan__code")

    def get_queryset(self) -> QuerySet[Subscription]:
        return subscriptions_queryset().order_by("-created_at", "-id")

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        payload = SubscriptionCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        subscription = subscriptions.activate(
            customer(data["user_id"]),
            get_object_or_404(Plan, pk=data["plan_id"]),
            source=data["source"],
            actor=acting_user(request),
            starts_at=data["starts_at"],
            ends_at=data["ends_at"],
            ip=client_ip(request),
            note=data["note"],
        )
        return Response(SubscriptionSerializer(subscription_detail(subscription.pk)).data, 201)


class SubscriptionDetailView(AdminView):
    required_permissions: ClassVar[Requirements] = {"GET": "subscriptions.view"}

    @extend_schema(
        operation_id="subscriptions_retrieve",
        summary="A subscription",
        responses={200: SubscriptionSerializer, **problems(401, 403, 404)},
    )
    def get(self, request: Request, pk: UUID) -> Response:
        return Response(SubscriptionSerializer(subscription_detail(pk)).data)


class _SubscriptionAction(AdminView):
    required_permissions: ClassVar[Requirements] = {"POST": "subscriptions.edit"}

    def respond(self, subscription: Subscription) -> Response:
        return Response(SubscriptionSerializer(subscription_detail(subscription.pk)).data)


class SubscriptionExtendView(_SubscriptionAction):
    @extend_schema(
        operation_id="subscriptions_extend",
        summary="Add days: from max(now, end); reopens an ended subscription",
        request=ExtendSerializer,
        responses={200: SubscriptionSerializer, **problems(400, 401, 403, 404, 409)},
    )
    def post(self, request: Request, pk: UUID) -> Response:
        payload = ExtendSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        subscription = subscriptions.extend(
            subscription_detail(pk),
            payload.validated_data["days"],
            actor=acting_user(request),
            ip=client_ip(request),
        )
        return self.respond(subscription)


class SubscriptionChangePlanView(_SubscriptionAction):
    @extend_schema(
        operation_id="subscriptions_change_plan",
        summary="Move the subscription to another plan; its dates stay",
        request=ChangePlanSerializer,
        responses={200: SubscriptionSerializer, **problems(400, 401, 403, 404, 409)},
    )
    def post(self, request: Request, pk: UUID) -> Response:
        payload = ChangePlanSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        subscription = subscriptions.change_plan(
            subscription_detail(pk),
            get_object_or_404(Plan, pk=payload.validated_data["plan_id"]),
            actor=acting_user(request),
            ip=client_ip(request),
        )
        return self.respond(subscription)


class SubscriptionCancelView(_SubscriptionAction):
    @extend_schema(
        operation_id="subscriptions_cancel",
        summary="End the subscription now; playback stops",
        request=ReasonSerializer,
        responses={200: SubscriptionSerializer, **problems(400, 401, 403, 404, 409)},
    )
    def post(self, request: Request, pk: UUID) -> Response:
        payload = ReasonSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        subscription = subscriptions.cancel(
            subscription_detail(pk),
            actor=acting_user(request),
            ip=client_ip(request),
            note=payload.validated_data["reason"],
        )
        return self.respond(subscription)


class SubscriptionSuspendView(_SubscriptionAction):
    @extend_schema(
        operation_id="subscriptions_suspend",
        summary="Stop playback and keep the period (resume undoes it)",
        request=ReasonSerializer,
        responses={200: SubscriptionSerializer, **problems(400, 401, 403, 404, 409)},
    )
    def post(self, request: Request, pk: UUID) -> Response:
        payload = ReasonSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        subscription = subscriptions.suspend(
            subscription_detail(pk),
            actor=acting_user(request),
            ip=client_ip(request),
            reason=payload.validated_data["reason"],
        )
        return self.respond(subscription)


class SubscriptionResumeView(_SubscriptionAction):
    @extend_schema(
        operation_id="subscriptions_resume",
        summary="Resume a suspended subscription",
        request=None,
        responses={200: SubscriptionSerializer, **problems(401, 403, 404, 409)},
    )
    def post(self, request: Request, pk: UUID) -> Response:
        subscription = subscriptions.resume(
            subscription_detail(pk), actor=acting_user(request), ip=client_ip(request)
        )
        return self.respond(subscription)


class SubscriptionApproveView(_SubscriptionAction):
    @extend_schema(
        operation_id="subscriptions_approve_trial",
        summary="Start a customer's waiting trial request now",
        request=None,
        responses={200: SubscriptionSerializer, **problems(401, 403, 404, 409)},
    )
    def post(self, request: Request, pk: UUID) -> Response:
        subscription = subscriptions.approve_trial(
            subscription_detail(pk), actor=acting_user(request), ip=client_ip(request)
        )
        return self.respond(subscription)


class TrialStartView(AdminView):
    required_permissions: ClassVar[Requirements] = {"POST": "subscriptions.edit"}

    @extend_schema(
        operation_id="subscriptions_start_trial",
        summary="Start a free trial for a customer (within the trial limits)",
        request=TrialStartSerializer,
        responses={201: SubscriptionSerializer, **problems(400, 401, 403, 404, 409)},
    )
    def post(self, request: Request) -> Response:
        payload = TrialStartSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        subscription = subscriptions.request_trial(
            customer(payload.validated_data["user_id"]),
            get_object_or_404(Plan, pk=payload.validated_data["plan_id"]),
            actor=acting_user(request),
            ip=client_ip(request),
        )
        return Response(SubscriptionSerializer(subscription_detail(subscription.pk)).data, 201)


# --- Payments ---------------------------------------------------------------------------------


def payments_queryset() -> QuerySet[Payment]:
    return Payment.objects.select_related("user", "plan", "invoice", "recorded_by")


def payment_detail(pk: UUID) -> Payment:
    return get_object_or_404(
        payments_queryset().prefetch_related(
            Prefetch("webhook_events", queryset=WebhookEvent.objects.order_by("created_at"))
        ),
        pk=pk,
    )


@extend_schema_view(
    get=extend_schema(
        operation_id="payments_list",
        summary="Payments, newest first",
        responses={200: PaymentSerializer(many=True), **problems(400, 401, 403)},
    ),
    post=extend_schema(
        operation_id="payments_record_manual",
        summary="Record a bank transfer or cash payment; activates or extends the subscription",
        request=ManualPaymentSerializer,
        responses={201: PaymentDetailSerializer, **problems(400, 401, 403, 404, 409)},
    ),
)
class PaymentListView(generics.ListCreateAPIView[Payment]):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {
        "GET": "billing.view",
        "POST": "billing.manage",
    }
    serializer_class = PaymentSerializer
    filter_backends = (DjangoFilterBackend, filters.SearchFilter)
    filterset_class = PaymentFilter
    search_fields = (
        "reference",
        "provider_ref",
        "checkout_ref",
        "invoice__number",
        "user__name",
        "user__email",
        "user__username",
    )

    def get_queryset(self) -> QuerySet[Payment]:
        return payments_queryset().order_by("-created_at", "-id")

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        payload = ManualPaymentSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        plan_id, invoice_id = data["plan_id"], data["invoice_id"]
        payment = services.record_manual_payment(
            user=customer(data["user_id"]),
            plan=get_object_or_404(Plan, pk=plan_id) if plan_id else None,
            invoice=get_object_or_404(Invoice, pk=invoice_id) if invoice_id else None,
            amount=data["amount"],
            method=data["method"],
            reference=data["reference"],
            paid_at=data["paid_at"],
            idempotency_key=data["idempotency_key"],
            actor=acting_user(request),
            ip=client_ip(request),
        )
        return Response(PaymentDetailSerializer(payment_detail(payment.pk)).data, status=201)


class PaymentDetailView(AdminView):
    required_permissions: ClassVar[Requirements] = {"GET": "billing.view"}

    @extend_schema(
        operation_id="payments_retrieve",
        summary="A payment with its provider payload and webhook timeline",
        responses={200: PaymentDetailSerializer, **problems(401, 403, 404)},
    )
    def get(self, request: Request, pk: UUID) -> Response:
        return Response(PaymentDetailSerializer(payment_detail(pk)).data)


class PaymentRefundView(AdminView):
    required_permissions: ClassVar[Requirements] = {"POST": "billing.refund"}

    @extend_schema(
        operation_id="payments_refund",
        summary="Refund all or part of a payment through its provider",
        request=RefundSerializer,
        responses={200: PaymentDetailSerializer, **problems(400, 401, 403, 404, 409)},
    )
    def post(self, request: Request, pk: UUID) -> Response:
        payload = RefundSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        services.refund(
            get_object_or_404(Payment, pk=pk),
            amount=data["amount"],
            reason=data["reason"],
            cancel_subscription=data["cancel_subscription"],
            actor=acting_user(request),
            ip=client_ip(request),
        )
        return Response(PaymentDetailSerializer(payment_detail(pk)).data)


# --- Invoices ---------------------------------------------------------------------------------


def invoices_queryset() -> QuerySet[Invoice]:
    return Invoice.objects.select_related("user", "plan")


@extend_schema_view(
    get=extend_schema(
        operation_id="invoices_list",
        summary="Invoices, newest first",
        responses={200: InvoiceSerializer(many=True), **problems(400, 401, 403)},
    )
)
class InvoiceListView(generics.ListAPIView[Invoice]):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"GET": "billing.view"}
    serializer_class = InvoiceSerializer
    filter_backends = (DjangoFilterBackend, filters.SearchFilter)
    filterset_class = InvoiceFilter
    search_fields = ("number", "user__name", "user__email", "user__username")

    def get_queryset(self) -> QuerySet[Invoice]:
        return invoices_queryset().order_by("-created_at", "-id")


class InvoiceDetailView(AdminView):
    required_permissions: ClassVar[Requirements] = {"GET": "billing.view"}

    @extend_schema(
        operation_id="invoices_retrieve",
        summary="An invoice",
        responses={200: InvoiceSerializer, **problems(401, 403, 404)},
    )
    def get(self, request: Request, pk: UUID) -> Response:
        return Response(InvoiceSerializer(get_object_or_404(invoices_queryset(), pk=pk)).data)


LOCALE_PARAMETER = OpenApiParameter(
    "locale", str, enum=["ar", "en"], required=False, description="Default: the invoice's."
)


def invoice_document(invoice: Invoice, request: Request) -> HttpResponse:
    """The invoice page, to show, print or save; never cached by shared caches."""
    locale = request.query_params.get("locale")
    html = invoices.render(invoice, locale=locale)
    response = HttpResponse(html, content_type="text/html; charset=utf-8")
    response["Content-Disposition"] = f'inline; filename="{invoices.filename(invoice)}"'
    response["Cache-Control"] = "private, no-store"
    response["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'self'"
    )
    return response


class InvoiceDocumentView(AdminView):
    required_permissions: ClassVar[Requirements] = {"GET": "billing.view"}

    @extend_schema(
        operation_id="invoices_document",
        summary="The invoice as a print-ready HTML page (Arabic or English)",
        parameters=[LOCALE_PARAMETER],
        responses={
            (200, "text/html"): OpenApiResponse(OpenApiTypes.STR, description="The page."),
            **problems(401, 403, 404),
        },
    )
    def get(self, request: Request, pk: UUID) -> HttpResponse:
        invoice = get_object_or_404(invoices_queryset().prefetch_related("payments"), pk=pk)
        return invoice_document(invoice, request)


# --- Dashboard ---------------------------------------------------------------------------------


class BillingKpisView(AdminView):
    required_permissions: ClassVar[Requirements] = {"GET": "billing.view"}

    @extend_schema(
        operation_id="dashboard_billing",
        summary="Billing KPIs: subscribers, MRR, revenue (cached 30 s)",
        responses={200: BillingKpisSerializer, **problems(401, 403)},
    )
    def get(self, request: Request) -> Response:
        return Response(BillingKpisSerializer(kpis.kpis()).data)
