"""Serializers of the billing APIs (admin and customer). Views stay thin; services decide."""

from datetime import datetime
from typing import Any

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.accounts.models import ConcurrencyPolicy, MaxQuality, User
from apps.billing import money
from apps.billing.models import (
    MAX_DURATION_DAYS,
    MAX_DURATION_MONTHS,
    Invoice,
    Payment,
    PaymentMethod,
    PaymentProviderCode,
    Plan,
    Subscription,
    SubscriptionSource,
    WebhookEvent,
    currency_validator,
)
from apps.billing.services import plan_price
from apps.playback.entitlements import grace_until

# --- Shared pieces ----------------------------------------------------------------------------


class CustomerRefSerializer(serializers.ModelSerializer[User]):
    class Meta:
        model = User
        fields = ("id", "username", "name", "email", "phone")
        read_only_fields = fields


class PlanRefSerializer(serializers.ModelSerializer[Plan]):
    class Meta:
        model = Plan
        fields = ("id", "code", "name_en", "name_ar", "is_trial")
        read_only_fields = fields


class PriceSerializer(serializers.Serializer[Any]):
    """What the customer pays for a plan, in minor units, with the VAT split."""

    net = serializers.IntegerField()
    vat = serializers.IntegerField()
    total = serializers.IntegerField()
    vat_rate = serializers.DecimalField(max_digits=5, decimal_places=4)
    display_en = serializers.CharField()
    display_ar = serializers.CharField()


def price_of(plan: Plan) -> dict[str, Any]:
    split = plan_price(plan)
    return {
        "net": split.net,
        "vat": split.vat,
        "total": split.total,
        "vat_rate": split.rate,
        "display_en": money.display(split.total, plan.currency, "en"),
        "display_ar": money.display(split.total, plan.currency, "ar"),
    }


# --- Plans ------------------------------------------------------------------------------------


class PlanSerializer(serializers.ModelSerializer[Plan]):
    category_ids = serializers.SerializerMethodField()
    price_total = serializers.SerializerMethodField()
    subscribers = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Plan
        fields = (
            "id",
            "code",
            "name_en",
            "name_ar",
            "description_en",
            "description_ar",
            "duration_months",
            "duration_days",
            "price",
            "currency",
            "price_total",
            "max_streams",
            "max_devices",
            "max_quality",
            "allow_movies",
            "allow_series",
            "allow_live",
            "allow_download",
            "bandwidth_cap_mbps",
            "concurrency_policy",
            "is_trial",
            "trial_limit_per_phone",
            "category_ids",
            "sort",
            "active",
            "version",
            "subscribers",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    @extend_schema_field(serializers.ListField(child=serializers.UUIDField()))
    def get_category_ids(self, plan: Plan) -> list[str]:
        return sorted(str(category.pk) for category in plan.categories.all())

    @extend_schema_field(PriceSerializer)
    def get_price_total(self, plan: Plan) -> dict[str, Any]:
        return price_of(plan)


class PlanWriteSerializer(serializers.Serializer[Any]):
    code = serializers.SlugField(max_length=50)
    name_en = serializers.CharField(max_length=100)
    name_ar = serializers.CharField(max_length=100)
    description_en = serializers.CharField(allow_blank=True, required=False, default="")
    description_ar = serializers.CharField(allow_blank=True, required=False, default="")
    duration_months = serializers.IntegerField(
        min_value=0, max_value=MAX_DURATION_MONTHS, required=False, default=0
    )
    duration_days = serializers.IntegerField(
        min_value=0, max_value=MAX_DURATION_DAYS, required=False, default=30
    )
    price = serializers.IntegerField(min_value=0, help_text="Minor units: 2900 = 29.00 SAR.")
    currency = serializers.CharField(max_length=3, validators=[currency_validator], required=False)
    max_streams = serializers.IntegerField(min_value=1, max_value=50, required=False, default=1)
    max_devices = serializers.IntegerField(min_value=1, max_value=50, required=False, default=2)
    max_quality = serializers.ChoiceField(
        choices=MaxQuality.choices, required=False, default=MaxQuality.FHD
    )
    allow_movies = serializers.BooleanField(required=False, default=True)
    allow_series = serializers.BooleanField(required=False, default=True)
    allow_live = serializers.BooleanField(required=False, default=True)
    allow_download = serializers.BooleanField(required=False, default=False)
    bandwidth_cap_mbps = serializers.IntegerField(
        min_value=1, max_value=100_000, allow_null=True, required=False, default=None
    )
    concurrency_policy = serializers.ChoiceField(
        choices=ConcurrencyPolicy.choices, required=False, default=ConcurrencyPolicy.REJECT
    )
    is_trial = serializers.BooleanField(required=False, default=False)
    trial_limit_per_phone = serializers.IntegerField(
        min_value=0, max_value=10, allow_null=True, required=False, default=None
    )
    category_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, default=list
    )
    sort = serializers.IntegerField(required=False, default=0)
    active = serializers.BooleanField(required=False, default=True)


class ReorderSerializer(serializers.Serializer[Any]):
    ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=False)


class MigratedSerializer(serializers.Serializer[Any]):
    migrated = serializers.IntegerField()


# --- Subscriptions ---------------------------------------------------------------------------


class SubscriptionSerializer(serializers.ModelSerializer[Subscription]):
    user = CustomerRefSerializer(read_only=True)
    plan = PlanRefSerializer(read_only=True)
    grace_until = serializers.SerializerMethodField()
    created_by = serializers.CharField(
        source="created_by.username", read_only=True, default=None, allow_null=True
    )

    class Meta:
        model = Subscription
        fields = (
            "id",
            "user",
            "plan",
            "plan_snapshot",
            "status",
            "starts_at",
            "ends_at",
            "grace_days",
            "grace_until",
            "source",
            "auto_renew",
            "trial_identity",
            "reminded_7d_at",
            "reminded_1d_at",
            "ended_at",
            "end_reason",
            "note",
            "created_by",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_grace_until(self, subscription: Subscription) -> datetime | None:
        return grace_until(subscription)


class SubscriptionCreateSerializer(serializers.Serializer[Any]):
    user_id = serializers.UUIDField()
    plan_id = serializers.UUIDField()
    # A field named like Field.source: DRF's metaclass takes it out of the attributes.
    source = serializers.ChoiceField(  # type: ignore[assignment]
        choices=[SubscriptionSource.MANUAL, SubscriptionSource.IMPORT],
        required=False,
        default=SubscriptionSource.MANUAL,
    )
    starts_at = serializers.DateTimeField(required=False, allow_null=True, default=None)
    ends_at = serializers.DateTimeField(required=False, allow_null=True, default=None)
    note = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")


class ExtendSerializer(serializers.Serializer[Any]):
    days = serializers.IntegerField(min_value=1, max_value=MAX_DURATION_DAYS)


class ChangePlanSerializer(serializers.Serializer[Any]):
    plan_id = serializers.UUIDField()


class ReasonSerializer(serializers.Serializer[Any]):
    reason = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")


class TrialStartSerializer(serializers.Serializer[Any]):
    user_id = serializers.UUIDField()
    plan_id = serializers.UUIDField()


# --- Invoices and payments ------------------------------------------------------------------


class InvoiceSerializer(serializers.ModelSerializer[Invoice]):
    user = CustomerRefSerializer(read_only=True)
    plan = PlanRefSerializer(read_only=True)

    class Meta:
        model = Invoice
        fields = (
            "id",
            "number",
            "status",
            "user",
            "plan",
            "subscription_id",
            "locale",
            "currency",
            "lines",
            "subtotal",
            "vat_rate",
            "vat_amount",
            "total",
            "prices_include_vat",
            "refunded_amount",
            "bill_to",
            "seller",
            "issued_at",
            "created_at",
        )
        read_only_fields = fields


class PaymentSerializer(serializers.ModelSerializer[Payment]):
    user = CustomerRefSerializer(read_only=True)
    plan = PlanRefSerializer(read_only=True, allow_null=True)
    invoice_number = serializers.CharField(source="invoice.number", read_only=True, default=None)
    recorded_by = serializers.CharField(
        source="recorded_by.username", read_only=True, default=None, allow_null=True
    )

    class Meta:
        model = Payment
        fields: tuple[str, ...] = (
            "id",
            "user",
            "plan",
            "subscription_id",
            "invoice_id",
            "invoice_number",
            "provider",
            "method",
            "checkout_ref",
            "provider_ref",
            "amount",
            "currency",
            "status",
            "refunded_amount",
            "reference",
            "failure_reason",
            "paid_at",
            "recorded_by",
            "created_at",
        )
        read_only_fields = fields


class WebhookEventSerializer(serializers.ModelSerializer[WebhookEvent]):
    class Meta:
        model = WebhookEvent
        fields = (
            "id",
            "provider",
            "event_id",
            "type",
            "attempts",
            "processed_at",
            "error",
            "payload",
            "created_at",
        )
        read_only_fields = fields


class PaymentDetailSerializer(PaymentSerializer):
    raw = serializers.JSONField(read_only=True)
    webhook_events = WebhookEventSerializer(many=True, read_only=True)

    class Meta(PaymentSerializer.Meta):
        fields = (*PaymentSerializer.Meta.fields, "raw", "webhook_events")
        read_only_fields = fields


class ManualPaymentSerializer(serializers.Serializer[Any]):
    user_id = serializers.UUIDField()
    plan_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    invoice_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    amount = serializers.IntegerField(
        min_value=1,
        required=False,
        allow_null=True,
        default=None,
        help_text="Minor units received; default: the plan's price (VAT included).",
    )
    method = serializers.ChoiceField(
        choices=[PaymentMethod.BANK_TRANSFER, PaymentMethod.CASH, PaymentMethod.OTHER],
        required=False,
        default=PaymentMethod.BANK_TRANSFER,
    )
    reference = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    paid_at = serializers.DateTimeField(required=False, allow_null=True, default=None)
    idempotency_key = serializers.CharField(
        max_length=64,
        required=False,
        allow_blank=True,
        default="",
        help_text="Send the same key again to get the first recording back (double clicks).",
    )

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if attrs.get("plan_id") is None and attrs.get("invoice_id") is None:
            raise serializers.ValidationError(
                {"plan_id": ["Choose a plan or an unpaid invoice."]}, code="required"
            )
        return attrs


class RefundSerializer(serializers.Serializer[Any]):
    amount = serializers.IntegerField(
        min_value=1,
        required=False,
        allow_null=True,
        default=None,
        help_text="Minor units; default: everything not refunded yet.",
    )
    reason = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    cancel_subscription = serializers.BooleanField(required=False, default=False)


# --- KPIs -----------------------------------------------------------------------------------


class AmountSerializer(serializers.Serializer[Any]):
    currency = serializers.CharField()
    amount = serializers.IntegerField()


class PlanCountSerializer(serializers.Serializer[Any]):
    code = serializers.CharField()
    name_en = serializers.CharField()
    name_ar = serializers.CharField()
    subscribers = serializers.IntegerField()


class BillingKpisSerializer(serializers.Serializer[Any]):
    active_subscribers = serializers.IntegerField()
    grace = serializers.IntegerField()
    suspended = serializers.IntegerField()
    trials_active = serializers.IntegerField()
    trial_requests = serializers.IntegerField()
    expiring_7d = serializers.IntegerField()
    new_subscriptions_30d = serializers.IntegerField()
    churned_30d = serializers.IntegerField()
    mrr = AmountSerializer(many=True)
    mrr_net = AmountSerializer(many=True)
    revenue_mtd = AmountSerializer(many=True)
    revenue_last_month = AmountSerializer(many=True)
    by_plan = PlanCountSerializer(many=True)
    as_of = serializers.DateTimeField()


# --- Customer API --------------------------------------------------------------------------


class PublicPlanSerializer(serializers.ModelSerializer[Plan]):
    price = serializers.SerializerMethodField()
    category_ids = serializers.SerializerMethodField()

    class Meta:
        model = Plan
        fields = (
            "id",
            "code",
            "name_en",
            "name_ar",
            "description_en",
            "description_ar",
            "duration_months",
            "duration_days",
            "currency",
            "price",
            "max_streams",
            "max_devices",
            "max_quality",
            "allow_movies",
            "allow_series",
            "allow_live",
            "allow_download",
            "is_trial",
            "category_ids",
        )
        read_only_fields = fields

    @extend_schema_field(PriceSerializer)
    def get_price(self, plan: Plan) -> dict[str, Any]:
        return price_of(plan)

    @extend_schema_field(serializers.ListField(child=serializers.UUIDField()))
    def get_category_ids(self, plan: Plan) -> list[str]:
        return sorted(str(category.pk) for category in plan.categories.all())


class CheckoutRequestSerializer(serializers.Serializer[Any]):
    plan_id = serializers.UUIDField()
    provider = serializers.ChoiceField(
        choices=PaymentProviderCode.choices, required=False, default=PaymentProviderCode.MANUAL
    )
    return_url = serializers.URLField(required=False, allow_blank=True, default="")


class CustomerInvoiceSerializer(serializers.ModelSerializer[Invoice]):
    plan = PlanRefSerializer(read_only=True)

    class Meta:
        model = Invoice
        fields = (
            "id",
            "number",
            "status",
            "plan",
            "currency",
            "subtotal",
            "vat_rate",
            "vat_amount",
            "total",
            "refunded_amount",
            "lines",
            "issued_at",
            "created_at",
        )
        read_only_fields = fields


class CustomerSubscriptionSerializer(serializers.ModelSerializer[Subscription]):
    plan = PlanRefSerializer(read_only=True)
    grace_until = serializers.SerializerMethodField()
    max_streams = serializers.IntegerField(source="plan_snapshot.max_streams", read_only=True)
    max_devices = serializers.IntegerField(source="plan_snapshot.max_devices", read_only=True)
    max_quality = serializers.IntegerField(source="plan_snapshot.max_quality", read_only=True)

    class Meta:
        model = Subscription
        fields = (
            "id",
            "plan",
            "status",
            "starts_at",
            "ends_at",
            "grace_until",
            "source",
            "max_streams",
            "max_devices",
            "max_quality",
            "ended_at",
            "end_reason",
        )
        read_only_fields = fields

    def get_grace_until(self, subscription: Subscription) -> datetime | None:
        return grace_until(subscription)


class MySubscriptionSerializer(serializers.Serializer[Any]):
    """The customer's subscription: the current one, a waiting one, or the last that ended."""

    subscription = CustomerSubscriptionSerializer(allow_null=True)
    pending = CustomerSubscriptionSerializer(allow_null=True)


class CheckoutResponseSerializer(serializers.Serializer[Any]):
    kind = serializers.ChoiceField(choices=["redirect", "manual", "trial"])
    provider = serializers.CharField(allow_null=True)
    invoice = CustomerInvoiceSerializer(allow_null=True)
    redirect_url = serializers.URLField(allow_null=True)
    reference = serializers.CharField(allow_blank=True)
    instructions = serializers.DictField(child=serializers.CharField())
    subscription = CustomerSubscriptionSerializer(allow_null=True)


class ProviderSerializer(serializers.Serializer[Any]):
    code = serializers.ChoiceField(choices=PaymentProviderCode.choices)
    name = serializers.CharField()
