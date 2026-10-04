"""Billing (SPEC §6 billing, §7.6): plans, subscriptions, payments, invoices and
payment-provider webhook events (ADR-0012).

Money is stored in integer minor units with its ISO 4217 currency. A subscription
keeps a snapshot of its plan, so editing a plan never changes what existing
subscribers have. Subscriptions change only through `apps.billing.subscriptions`.
"""

from typing import Any

from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField, RangeOperators
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.db.models import Func, Q

from apps.accounts.models import LIMIT_MAX, ConcurrencyPolicy, MaxQuality
from apps.core.models import BaseModel

currency_validator = RegexValidator(r"^[A-Z]{3}$", "Enter an ISO 4217 currency code, e.g. SAR.")

#: Longest plan period an admin may define.
MAX_DURATION_MONTHS = 120
MAX_DURATION_DAYS = 3660


class TsTzRange(Func):
    """`tstzrange(lower, upper)`: the half-open period a subscription covers."""

    function = "TSTZRANGE"
    output_field = DateTimeRangeField()


# --- Plans -------------------------------------------------------------------------------


class Plan(BaseModel):
    """What a customer buys (SPEC §6 billing): limits, content and a price.

    `price` is in minor units of `currency` (2900 SAR = 29.00 SAR). The period is
    `duration_months` calendar months plus `duration_days` days on the customer's
    wall clock (`apps.billing.renewal`). Trial plans last `trials.duration_hours`
    instead. `version` goes up on every change; subscriptions keep the snapshot of
    the version they bought. No categories means every category.
    """

    code = models.SlugField(max_length=50, unique=True)
    name_en = models.CharField(max_length=100)
    name_ar = models.CharField(max_length=100)
    description_en = models.TextField(blank=True)
    description_ar = models.TextField(blank=True)
    duration_months = models.PositiveSmallIntegerField(
        default=0, validators=[MaxValueValidator(MAX_DURATION_MONTHS)]
    )
    duration_days = models.PositiveSmallIntegerField(
        default=30, validators=[MaxValueValidator(MAX_DURATION_DAYS)]
    )
    price = models.PositiveIntegerField(default=0)
    currency = models.CharField(max_length=3, default="SAR", validators=[currency_validator])
    max_streams = models.PositiveSmallIntegerField(
        default=1, validators=[MinValueValidator(1), MaxValueValidator(LIMIT_MAX)]
    )
    max_devices = models.PositiveSmallIntegerField(
        default=2, validators=[MinValueValidator(1), MaxValueValidator(LIMIT_MAX)]
    )
    max_quality = models.PositiveSmallIntegerField(
        choices=MaxQuality.choices, default=MaxQuality.FHD
    )
    allow_movies = models.BooleanField(default=True)
    allow_series = models.BooleanField(default=True)
    allow_live = models.BooleanField(default=True)
    allow_download = models.BooleanField(default=False)
    bandwidth_cap_mbps = models.PositiveIntegerField(null=True, blank=True)
    concurrency_policy = models.CharField(
        max_length=16, choices=ConcurrencyPolicy.choices, default=ConcurrencyPolicy.REJECT
    )
    is_trial = models.BooleanField(default=False)
    # Trials allowed per phone number or email; None follows `trials.limit_per_phone`.
    trial_limit_per_phone = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MaxValueValidator(10)]
    )
    categories = models.ManyToManyField(
        "catalog.Category", blank=True, related_name="plans", db_table="billing_plan_category"
    )
    sort = models.IntegerField(default=0)
    active = models.BooleanField(default=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ("sort", "created_at")
        constraints = (
            models.CheckConstraint(
                condition=Q(duration_months__gt=0) | Q(duration_days__gt=0),
                name="billing_plan_duration_positive",
            ),
            models.CheckConstraint(
                condition=Q(max_streams__gte=1, max_streams__lte=LIMIT_MAX),
                name="billing_plan_max_streams_range",
            ),
            models.CheckConstraint(
                condition=Q(max_devices__gte=1, max_devices__lte=LIMIT_MAX),
                name="billing_plan_max_devices_range",
            ),
            models.CheckConstraint(
                condition=Q(max_quality__in=MaxQuality.values),
                name="billing_plan_max_quality_valid",
            ),
        )

    def __str__(self) -> str:
        return self.code


# --- Subscriptions ---------------------------------------------------------------------------


class SubscriptionStatus(models.TextChoices):
    # Starts later, or a trial request waiting for an admin.
    PENDING = "pending", "Pending"
    ACTIVE = "active", "Active"
    # Ended, still playing for `grace_days`.
    GRACE = "grace", "Grace"
    EXPIRED = "expired", "Expired"
    SUSPENDED = "suspended", "Suspended"
    CANCELLED = "cancelled", "Cancelled"


#: Statuses that hold the customer's current period: at most one per customer.
CURRENT_STATUSES: tuple[str, ...] = (
    SubscriptionStatus.ACTIVE,
    SubscriptionStatus.GRACE,
    SubscriptionStatus.SUSPENDED,
)
ENDED_STATUSES: tuple[str, ...] = (SubscriptionStatus.EXPIRED, SubscriptionStatus.CANCELLED)


class SubscriptionSource(models.TextChoices):
    MANUAL = "manual", "Manual (admin)"
    PAYMENT = "payment", "Payment"
    TRIAL = "trial", "Trial"
    IMPORT = "import", "Import"


class EndReason(models.TextChoices):
    EXPIRED = "expired", "Expired"
    CANCELLED = "cancelled", "Cancelled"
    REFUNDED = "refunded", "Refunded"
    REJECTED = "rejected", "Trial request rejected"
    MERGED = "merged", "Merged into the current subscription"


class Subscription(BaseModel):
    """One continuous period of service for a customer (SPEC §6 billing, §7.6).

    Renewals extend the current row (`subscriptions.activate`), so a customer has a
    new row only after a gap. `plan_snapshot` holds the limits and content bought;
    the entitlement reads it, never the live plan. The database refuses two
    current (active, grace or suspended) subscriptions of one customer whose
    periods overlap (btree_gist exclusion constraint).
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="subscriptions"
    )
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")
    plan_snapshot = models.JSONField()
    status = models.CharField(
        max_length=16, choices=SubscriptionStatus.choices, default=SubscriptionStatus.PENDING
    )
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    grace_days = models.PositiveSmallIntegerField(default=0)
    source = models.CharField(max_length=16, choices=SubscriptionSource.choices)
    auto_renew = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    # The phone number (or email) a trial was granted to; counts towards the limit.
    trial_identity = models.CharField(max_length=254, blank=True)
    reminded_7d_at = models.DateTimeField(null=True, blank=True)
    reminded_1d_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    end_reason = models.CharField(max_length=16, choices=EndReason.choices, blank=True)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ("-starts_at",)
        constraints = (
            ExclusionConstraint(
                name="billing_subscription_no_overlap",
                expressions=[
                    (TsTzRange("starts_at", "ends_at"), RangeOperators.OVERLAPS),
                    ("user", RangeOperators.EQUAL),
                ],
                condition=Q(status__in=CURRENT_STATUSES),
            ),
            models.CheckConstraint(
                condition=Q(ends_at__gt=models.F("starts_at")),
                name="billing_subscription_ends_after_start",
            ),
        )
        indexes = (
            # SPEC §6: the partial index the state jobs and dashboards scan.
            models.Index(
                fields=("status",),
                condition=Q(status__in=("active", "grace")),
                name="billing_sub_current_status",
            ),
            models.Index(fields=("user", "-ends_at"), name="billing_sub_user_ends"),
            models.Index(fields=("status", "ends_at"), name="billing_sub_status_ends"),
            models.Index(fields=("trial_identity",), name="billing_sub_trial_identity"),
        )

    def __str__(self) -> str:
        return f"subscription:{self.pk}"


# --- Invoices --------------------------------------------------------------------------------


class InvoiceStatus(models.TextChoices):
    # A checkout waiting for its payment; not numbered yet.
    PENDING = "pending", "Awaiting payment"
    PAID = "paid", "Paid"
    # A checkout that was abandoned or replaced; never numbered.
    VOID = "void", "Void"
    REFUNDED = "refunded", "Refunded"


#: Statuses of issued (numbered) invoices.
ISSUED_STATUSES: tuple[str, ...] = (InvoiceStatus.PAID, InvoiceStatus.REFUNDED)


class Invoice(BaseModel):
    """A tax invoice (SPEC §6, §7.6). Numbered only when paid, so numbers have no gaps.

    `number` is `<prefix>-<year>-<sequence>`, sequential per year
    (`InvoiceSequence`). Amounts are minor units: `subtotal` (net) + `vat_amount`
    = `total`. `bill_to` and `seller` are snapshots taken when the invoice was made.
    """

    number = models.CharField(max_length=32, null=True, blank=True, unique=True)
    year = models.PositiveSmallIntegerField(null=True, blank=True)
    sequence = models.PositiveIntegerField(null=True, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="invoices"
    )
    plan = models.ForeignKey(
        Plan, null=True, blank=True, on_delete=models.PROTECT, related_name="invoices"
    )
    subscription = models.ForeignKey(
        Subscription, null=True, blank=True, on_delete=models.PROTECT, related_name="invoices"
    )
    status = models.CharField(
        max_length=16, choices=InvoiceStatus.choices, default=InvoiceStatus.PENDING
    )
    locale = models.CharField(max_length=2, default="ar")
    currency = models.CharField(max_length=3, validators=[currency_validator])
    lines = models.JSONField(default=list)
    subtotal = models.PositiveIntegerField()
    vat_rate = models.DecimalField(max_digits=5, decimal_places=4)
    vat_amount = models.PositiveIntegerField()
    total = models.PositiveIntegerField()
    prices_include_vat = models.BooleanField(default=True)
    refunded_amount = models.PositiveIntegerField(default=0)
    bill_to = models.JSONField(default=dict)
    seller = models.JSONField(default=dict)
    issued_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = (
            models.UniqueConstraint(
                fields=("year", "sequence"),
                condition=Q(sequence__isnull=False),
                name="billing_invoice_year_sequence_unique",
            ),
            models.CheckConstraint(
                condition=Q(total=models.F("subtotal") + models.F("vat_amount")),
                name="billing_invoice_totals_add_up",
            ),
            models.CheckConstraint(
                condition=Q(refunded_amount__lte=models.F("total")),
                name="billing_invoice_refund_within_total",
            ),
            models.CheckConstraint(
                condition=(
                    Q(status__in=ISSUED_STATUSES, number__isnull=False, issued_at__isnull=False)
                    | (~Q(status__in=ISSUED_STATUSES) & Q(number__isnull=True))
                ),
                name="billing_invoice_numbered_when_issued",
            ),
        )
        indexes = (
            models.Index(fields=("user", "-created_at"), name="billing_invoice_user_created"),
            models.Index(fields=("status", "-created_at"), name="billing_invoice_status_created"),
        )

    def __str__(self) -> str:
        return self.number or f"invoice:{self.pk}"


class InvoiceSequence(BaseModel):
    """The last invoice number used in a year; locked while an invoice is numbered."""

    year = models.PositiveSmallIntegerField(unique=True)
    last = models.PositiveIntegerField(default=0)

    def __str__(self) -> str:
        return f"{self.year}:{self.last}"


# --- Payments --------------------------------------------------------------------------------


class PaymentProviderCode(models.TextChoices):
    MANUAL = "manual", "Manual (bank transfer or cash)"
    STRIPE = "stripe", "Stripe"
    MOYASAR = "moyasar", "Moyasar"


class PaymentStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"
    PARTIALLY_REFUNDED = "partially_refunded", "Partially refunded"
    REFUNDED = "refunded", "Refunded"


#: Payments that brought money in (and may be refunded).
PAID_STATUSES: tuple[str, ...] = (
    PaymentStatus.SUCCEEDED,
    PaymentStatus.PARTIALLY_REFUNDED,
    PaymentStatus.REFUNDED,
)


class PaymentMethod(models.TextChoices):
    BANK_TRANSFER = "bank_transfer", "Bank transfer"
    CASH = "cash", "Cash"
    CARD = "card", "Card"
    MADA = "mada", "mada"
    APPLE_PAY = "apple_pay", "Apple Pay"
    STC_PAY = "stc_pay", "STC Pay"
    OTHER = "other", "Other"


class Payment(BaseModel):
    """Money for a plan (SPEC §6 billing): recorded by an admin, or a provider checkout.

    `checkout_ref` is the provider's checkout (a Stripe Checkout Session, a Moyasar
    invoice); `provider_ref` is the provider's payment once paid (a Stripe
    PaymentIntent, a Moyasar payment), which refunds use. `raw` keeps the
    provider's last payload, redacted. `idempotency_key` makes recording and
    checkout safe to retry.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="payments"
    )
    plan = models.ForeignKey(
        Plan, null=True, blank=True, on_delete=models.PROTECT, related_name="payments"
    )
    subscription = models.ForeignKey(
        Subscription, null=True, blank=True, on_delete=models.PROTECT, related_name="payments"
    )
    invoice = models.ForeignKey(
        Invoice, null=True, blank=True, on_delete=models.PROTECT, related_name="payments"
    )
    provider = models.CharField(max_length=16, choices=PaymentProviderCode.choices)
    checkout_ref = models.CharField(max_length=255, blank=True)
    provider_ref = models.CharField(max_length=255, blank=True)
    method = models.CharField(
        max_length=16, choices=PaymentMethod.choices, default=PaymentMethod.OTHER
    )
    amount = models.PositiveIntegerField()
    currency = models.CharField(max_length=3, validators=[currency_validator])
    status = models.CharField(
        max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.PENDING
    )
    refunded_amount = models.PositiveIntegerField(default=0)
    idempotency_key = models.CharField(max_length=64, unique=True)
    reference = models.CharField(max_length=100, blank=True)
    raw = models.JSONField(default=dict, blank=True)
    failure_reason = models.CharField(max_length=200, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        ordering = ("-created_at",)
        constraints = (
            models.UniqueConstraint(
                fields=("provider", "provider_ref"),
                condition=~Q(provider_ref=""),
                name="billing_payment_provider_ref_unique",
            ),
            models.CheckConstraint(
                condition=Q(refunded_amount__lte=models.F("amount")),
                name="billing_payment_refund_within_amount",
            ),
        )
        indexes = (
            models.Index(fields=("user", "-created_at"), name="billing_payment_user_created"),
            models.Index(fields=("status", "-paid_at"), name="billing_payment_status_paid"),
            models.Index(fields=("provider", "checkout_ref"), name="billing_payment_checkout"),
        )

    def __str__(self) -> str:
        return f"payment:{self.pk}"


class WebhookEvent(BaseModel):
    """A provider webhook, stored once per (provider, event_id) (SPEC §6, §7.6).

    Deliveries of the same event are answered from this row: processed events are
    acknowledged without being applied again. `error` holds the last failure, and
    the provider's retry processes the event again.
    """

    provider = models.CharField(max_length=16, choices=PaymentProviderCode.choices)
    event_id = models.CharField(max_length=255)
    type = models.CharField(max_length=100)
    payload = models.JSONField(default=dict)
    payment = models.ForeignKey(
        Payment, null=True, blank=True, on_delete=models.SET_NULL, related_name="webhook_events"
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    processed_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = (
            models.UniqueConstraint(
                fields=("provider", "event_id"), name="billing_webhook_provider_event_unique"
            ),
        )

    def __str__(self) -> str:
        return f"{self.provider}:{self.event_id}"


def plan_snapshot(plan: Plan, category_ids: list[str]) -> dict[str, Any]:
    """What a subscription keeps of its plan: everything the entitlement and invoices need."""
    return {
        "v": 1,
        "plan_id": str(plan.pk),
        "code": plan.code,
        "version": plan.version,
        "name_en": plan.name_en,
        "name_ar": plan.name_ar,
        "price": plan.price,
        "currency": plan.currency,
        "duration_months": plan.duration_months,
        "duration_days": plan.duration_days,
        "max_streams": plan.max_streams,
        "max_devices": plan.max_devices,
        "max_quality": plan.max_quality,
        "concurrency_policy": plan.concurrency_policy,
        "allow_movies": plan.allow_movies,
        "allow_series": plan.allow_series,
        "allow_live": plan.allow_live,
        "allow_download": plan.allow_download,
        "bandwidth_cap_mbps": plan.bandwidth_cap_mbps,
        "is_trial": plan.is_trial,
        "category_ids": sorted(category_ids),
    }
