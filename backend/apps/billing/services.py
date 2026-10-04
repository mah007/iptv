"""Billing services (SPEC §7.6, ADR-0012): plans, invoices, checkout, payments, refunds.

Subscriptions themselves change only in `apps.billing.subscriptions`; a payment
reaches them through `subscriptions.activate(..., payment=...)`.

- **Invoices** are made when a checkout starts (pending, unnumbered) or when a
  payment is recorded, and numbered `PREFIX-YEAR-NNNNNN` only when paid, from a
  per-year counter row locked in the same transaction: numbers have no gaps.
  VAT is carved out of the amount paid (`money.split_vat`).
- **Checkout** creates a pending invoice and payment, then the provider's hosted
  page (Stripe, Moyasar) or the manual payment instructions.
- **Payments** succeed through a provider webhook (`webhooks.py` →
  `apply_result`) or an admin recording a bank transfer or cash; both activate or
  extend the subscription, issue the invoice and notify the customer.
- **Refunds** go through the provider, then update the payment and the invoice;
  the admin may also end the subscription.
"""

import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Final
from urllib.parse import urlsplit
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.audit import services as audit
from apps.billing import money, subscriptions
from apps.billing.models import (
    CURRENT_STATUSES,
    ISSUED_STATUSES,
    PAID_STATUSES,
    EndReason,
    Invoice,
    InvoiceSequence,
    InvoiceStatus,
    Payment,
    PaymentMethod,
    PaymentProviderCode,
    PaymentStatus,
    Plan,
    Subscription,
    SubscriptionSource,
    SubscriptionStatus,
)
from apps.billing.providers import get_provider
from apps.billing.providers.base import (
    CheckoutSession,
    Outcome,
    PaymentResult,
    ProviderError,
)
from apps.catalog.models import Category
from apps.core.errors import ErrorCode, ProblemError, field_error
from apps.core.metrics import PAYMENTS
from apps.core.redaction import redact_value
from apps.core.services import get_setting
from apps.notifications import services as notifications
from apps.playback import entitlements
from apps.xtream_api import cache as xtream_cache
from config.origins import origin

logger = logging.getLogger(__name__)

PLAN_FIELDS: Final = (
    "code",
    "name_en",
    "name_ar",
    "description_en",
    "description_ar",
    "duration_months",
    "duration_days",
    "price",
    "currency",
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
    "sort",
    "active",
)
#: Changing only these does not make a new plan version.
_UNVERSIONED: Final = frozenset({"sort", "active"})


def _problem(field: str, message: str, code: str) -> ProblemError:
    return ProblemError(
        ErrorCode.VALIDATION_ERROR, message, field_errors={field: [field_error(message, code=code)]}
    )


# --- Plans --------------------------------------------------------------------------------


def plan_audit_snapshot(plan: Plan, category_ids: Sequence[UUID | str]) -> dict[str, Any]:
    return {
        **{field: getattr(plan, field) for field in PLAN_FIELDS},
        "version": plan.version,
        "category_ids": sorted(str(pk) for pk in category_ids),
    }


def _categories(category_ids: Sequence[UUID | str]) -> list[Category]:
    wanted = {str(pk) for pk in category_ids}
    found = list(Category.objects.filter(pk__in=wanted))
    missing = wanted - {str(category.pk) for category in found}
    if missing:
        raise ProblemError(
            ErrorCode.VALIDATION_ERROR,
            "Unknown category.",
            field_errors={
                "category_ids": [
                    field_error(f"Unknown category {pk}.", code="unknown_category")
                    for pk in sorted(missing)
                ]
            },
        )
    return found


def _check_plan(plan: Plan) -> None:
    if plan.duration_months == 0 and plan.duration_days == 0:
        raise _problem("duration_days", "A plan lasts at least one day.", "duration_zero")
    if Plan.objects.filter(code=plan.code).exclude(pk=plan.pk).exists():
        raise _problem("code", "Another plan has this code.", "code_taken")


def create_plan(
    values: Mapping[str, Any],
    *,
    category_ids: Sequence[UUID | str] = (),
    actor: User | None,
    ip: str | None = None,
) -> Plan:
    plan = Plan(**{field: values[field] for field in PLAN_FIELDS if field in values})
    if "currency" not in values:
        plan.currency = str(get_setting("billing.currency"))
    _check_plan(plan)
    with transaction.atomic():
        plan.save()
        categories = _categories(category_ids)
        plan.categories.set(categories)
        audit.record(
            "plan.create",
            actor=actor,
            target=plan,
            after=plan_audit_snapshot(plan, [category.pk for category in categories]),
            ip=ip,
        )
    return plan


def update_plan(
    plan: Plan,
    values: Mapping[str, Any],
    *,
    category_ids: Sequence[UUID | str] | None = None,
    actor: User | None,
    ip: str | None = None,
) -> Plan:
    """Change a plan. Anything but its order or availability makes a new version, which
    new subscriptions get; existing ones keep their snapshot (`migrate_subscriptions`)."""
    with transaction.atomic():
        plan = Plan.objects.select_for_update().get(pk=plan.pk)
        current_ids = list(plan.categories.values_list("pk", flat=True))
        before = plan_audit_snapshot(plan, current_ids)
        changed = [
            field
            for field in PLAN_FIELDS
            if field in values and getattr(plan, field) != values[field]
        ]
        for field in changed:
            setattr(plan, field, values[field])
        new_ids: Sequence[UUID | str] = current_ids
        categories_changed = False
        if category_ids is not None:
            categories = _categories(category_ids)
            new_ids = [category.pk for category in categories]
            categories_changed = {str(pk) for pk in new_ids} != {str(pk) for pk in current_ids}
            if categories_changed:
                plan.categories.set(categories)
        if not changed and not categories_changed:
            return plan
        _check_plan(plan)
        if categories_changed or set(changed) - _UNVERSIONED:
            plan.version += 1
        plan.save()
        after = plan_audit_snapshot(plan, new_ids)
        keys = [key for key in after if before.get(key) != after.get(key)]
        audit.record(
            "plan.update",
            actor=actor,
            target=plan,
            before={key: before.get(key) for key in keys},
            after={key: after.get(key) for key in keys},
            ip=ip,
        )
    return plan


def delete_plan(plan: Plan, *, actor: User | None, ip: str | None = None) -> None:
    """Delete a plan nobody bought; plans with history can only be switched off."""
    with transaction.atomic():
        plan = Plan.objects.select_for_update().get(pk=plan.pk)
        if (
            plan.subscriptions.exists()
            or Payment.objects.filter(plan=plan).exists()
            or Invoice.objects.filter(plan=plan).exists()
        ):
            raise ProblemError(
                ErrorCode.CONFLICT,
                "This plan has subscriptions or payments; switch it off instead of deleting it.",
            )
        audit.record(
            "plan.delete",
            actor=actor,
            target=plan,
            before=plan_audit_snapshot(plan, list(plan.categories.values_list("pk", flat=True))),
            ip=ip,
        )
        plan.delete()


def reorder_plans(plan_ids: Sequence[UUID | str], *, actor: User | None, ip: str | None) -> None:
    """Set `sort` to the order of `plan_ids` (every plan, each once)."""
    with transaction.atomic():
        plans = {str(plan.pk): plan for plan in Plan.objects.select_for_update()}
        wanted = [str(pk) for pk in plan_ids]
        if sorted(wanted) != sorted(plans) or len(set(wanted)) != len(wanted):
            raise _problem("ids", "List every plan exactly once.", "incomplete_order")
        before = {pk: plan.sort for pk, plan in plans.items()}
        for index, pk in enumerate(wanted):
            plans[pk].sort = (index + 1) * 10
        Plan.objects.bulk_update(plans.values(), ["sort"])
        audit.record(
            "plan.reorder",
            actor=actor,
            before={"order": sorted(before, key=lambda pk: before[pk])},
            after={"order": wanted},
            ip=ip,
        )


def migrate_subscriptions(plan: Plan, *, actor: User | None, ip: str | None = None) -> int:
    """Give the plan's current and pending subscriptions its latest version (SPEC §8.3)."""
    with transaction.atomic():
        plan = Plan.objects.select_for_update().get(pk=plan.pk)
        snapshot = subscriptions.snapshot_of(plan)
        rows = Subscription.objects.filter(
            plan=plan, status__in=(*CURRENT_STATUSES, SubscriptionStatus.PENDING)
        )
        user_ids = list(rows.values_list("user_id", flat=True).distinct())
        count = rows.update(plan_snapshot=snapshot, updated_at=timezone.now())
        audit.record(
            "plan.migrate_subscriptions",
            actor=actor,
            target=plan,
            after={"version": plan.version, "subscriptions": count},
            ip=ip,
        )
        for user_id in user_ids:
            entitlements.schedule_refresh(user_id)
        xtream_cache.invalidate_on_commit()
    return count


def plan_price(plan: Plan) -> money.VatSplit:
    """What the plan costs the customer, VAT included or added per the settings."""
    return money.split_vat(
        plan.price,
        vat_rate(),
        inclusive=get_setting("billing.prices_include_vat") is True,
    )


def vat_rate() -> Decimal:
    return Decimal(str(get_setting("billing.vat_rate"))).quantize(Decimal("0.0001"))


# --- Invoices -------------------------------------------------------------------------------


def _seller() -> dict[str, str]:
    return {
        "name_en": str(get_setting("branding.service_name_en")),
        "name_ar": str(get_setting("branding.service_name_ar")),
        "vat_number": str(get_setting("billing.vat_number")),
        "address": str(get_setting("billing.seller_address")),
        "email": str(get_setting("branding.support_email")),
    }


def _bill_to(user: User) -> dict[str, str]:
    return {
        "name": user.get_full_name(),
        "email": user.email,
        "phone": user.phone,
        "username": user.username,
    }


def _plan_period(plan: Plan) -> str:
    if plan.is_trial:
        return f"{int(get_setting('trials.duration_hours'))}h"
    parts = []
    if plan.duration_months:
        parts.append(f"{plan.duration_months}m")
    if plan.duration_days:
        parts.append(f"{plan.duration_days}d")
    return " ".join(parts)


def new_invoice(user: User, plan: Plan, gross: int, currency: str) -> Invoice:
    """An unsaved pending invoice for `gross` (the amount paid, VAT included)."""
    split = money.split_vat(gross, vat_rate(), inclusive=True)
    period = _plan_period(plan)
    line = {
        "plan_id": str(plan.pk),
        "plan_code": plan.code,
        "description_en": f"{plan.name_en} ({period})",
        "description_ar": f"{plan.name_ar} ({period})",
        "quantity": 1,
        "unit_amount": split.net,
        "amount": split.net,
    }
    return Invoice(
        user=user,
        plan=plan,
        status=InvoiceStatus.PENDING,
        locale=user.locale if user.locale in ("ar", "en") else "ar",
        currency=currency,
        lines=[line],
        subtotal=split.net,
        vat_rate=split.rate,
        vat_amount=split.vat,
        total=split.total,
        prices_include_vat=get_setting("billing.prices_include_vat") is True,
        bill_to=_bill_to(user),
        seller=_seller(),
    )


def issue_invoice(invoice: Invoice, *, now: datetime) -> Invoice:
    """Number a paid invoice. The caller holds a transaction; the year's counter row
    stays locked until it commits, so concurrent payments get consecutive numbers."""
    if invoice.number:
        return invoice
    year = now.year
    InvoiceSequence.objects.get_or_create(year=year)
    sequence = InvoiceSequence.objects.select_for_update().get(year=year)
    sequence.last += 1
    sequence.save(update_fields=["last", "updated_at"])
    prefix = str(get_setting("billing.invoice_prefix"))
    invoice.year = year
    invoice.sequence = sequence.last
    invoice.number = f"{prefix}-{year}-{sequence.last:06d}"
    invoice.status = InvoiceStatus.PAID
    invoice.issued_at = now
    invoice.save()
    return invoice


def invoice_snapshot(invoice: Invoice) -> dict[str, Any]:
    return {
        "number": invoice.number,
        "status": invoice.status,
        "user_id": str(invoice.user_id),
        "total": invoice.total,
        "vat_amount": invoice.vat_amount,
        "currency": invoice.currency,
        "refunded_amount": invoice.refunded_amount,
    }


# --- Checkout --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CheckoutResult:
    invoice: Invoice | None
    payment: Payment | None
    session: CheckoutSession | None
    subscription: Subscription | None = None


def default_return_url() -> str:
    return f"{origin(settings.PUBLIC_SCHEME, settings.APP_HOST, settings.PUBLIC_PORT)}/account"


def check_return_url(url: str) -> str:
    """Only the portal's own pages: a provider must never redirect elsewhere."""
    if not url:
        return default_return_url()
    parts = urlsplit(url)
    portal = urlsplit(default_return_url())
    if (parts.scheme, parts.netloc) != (portal.scheme, portal.netloc):
        raise _problem("return_url", "Use a page of the customer portal.", "foreign_url")
    return url


def _void_pending(user: User, plan: Plan, now: datetime) -> None:
    """A new checkout replaces the customer's unpaid ones for the same plan."""
    stale = Invoice.objects.filter(user=user, plan=plan, status=InvoiceStatus.PENDING)
    Payment.objects.filter(invoice__in=stale, status=PaymentStatus.PENDING).update(
        status=PaymentStatus.CANCELLED, updated_at=now
    )
    stale.update(status=InvoiceStatus.VOID, updated_at=now)


def checkout(
    user: User,
    plan: Plan,
    provider_code: str,
    *,
    return_url: str = "",
    ip: str | None = None,
) -> CheckoutResult:
    """Start paying for `plan`: a pending invoice and payment, then where to pay.

    Trial plans need no payment: they go to `subscriptions.request_trial`.
    """
    if not plan.active:
        raise _problem("plan_id", "This plan is not available.", "plan_unavailable")
    if plan.is_trial:
        subscription = subscriptions.request_trial(user, plan, ip=ip)
        return CheckoutResult(invoice=None, payment=None, session=None, subscription=subscription)
    if plan.price <= 0:
        raise _problem("plan_id", "Free plans are given by an admin.", "plan_free")
    provider = get_provider(provider_code)
    if provider is None or not provider.is_enabled():
        raise ProblemError(
            ErrorCode.PAYMENT_PROVIDER_UNAVAILABLE, "This payment method is not available."
        )
    url = check_return_url(return_url)
    now = timezone.now()
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=user.pk)
        _void_pending(user, plan, now)
        invoice = new_invoice(user, plan, plan_price(plan).total, plan.currency)
        invoice.save()
        payment = Payment.objects.create(
            user=user,
            plan=plan,
            invoice=invoice,
            provider=provider.code,
            amount=invoice.total,
            currency=invoice.currency,
            idempotency_key=uuid.uuid4().hex,
        )
    try:
        session = provider.create_checkout(invoice, payment, return_url=url)
    except ProviderError as exc:
        logger.warning("checkout with %s failed: %s", provider.code, exc)
        Payment.objects.filter(pk=payment.pk).update(
            status=PaymentStatus.FAILED, failure_reason=str(exc)[:200], updated_at=timezone.now()
        )
        Invoice.objects.filter(pk=invoice.pk).update(
            status=InvoiceStatus.VOID, updated_at=timezone.now()
        )
        PAYMENTS.labels(provider.code, PaymentStatus.FAILED).inc()
        raise ProblemError(
            ErrorCode.PAYMENT_PROVIDER_ERROR, "The payment provider is not answering; try again."
        ) from None
    with transaction.atomic():
        payment.checkout_ref = session.reference
        payment.save(update_fields=["checkout_ref", "updated_at"])
        audit.record(
            "payment.checkout",
            actor=user,
            target=payment,
            after={
                "provider": provider.code,
                "plan": plan.code,
                "amount": payment.amount,
                "currency": payment.currency,
                "invoice_id": str(invoice.pk),
            },
            ip=ip,
        )
    return CheckoutResult(invoice=invoice, payment=payment, session=session)


def expire_checkouts(*, now: datetime | None = None) -> int:
    """Void checkouts left unpaid for `billing.checkout_ttl_hours` (beat, hourly)."""
    moment = now or timezone.now()
    cutoff = moment - timedelta(hours=int(get_setting("billing.checkout_ttl_hours")))
    with transaction.atomic():
        stale = Invoice.objects.select_for_update(skip_locked=True).filter(
            status=InvoiceStatus.PENDING, created_at__lt=cutoff
        )
        ids = list(stale.values_list("pk", flat=True))
        Payment.objects.filter(invoice__in=ids, status=PaymentStatus.PENDING).update(
            status=PaymentStatus.CANCELLED, updated_at=moment
        )
        count = Invoice.objects.filter(pk__in=ids).update(
            status=InvoiceStatus.VOID, updated_at=moment
        )
    return count


# --- Payments ----------------------------------------------------------------------------------


def payment_snapshot(payment: Payment) -> dict[str, Any]:
    return {
        "user_id": str(payment.user_id),
        "provider": payment.provider,
        "method": payment.method,
        "amount": payment.amount,
        "currency": payment.currency,
        "status": payment.status,
        "refunded_amount": payment.refunded_amount,
        "reference": payment.reference,
        "invoice_id": str(payment.invoice_id) if payment.invoice_id else None,
        "subscription_id": str(payment.subscription_id) if payment.subscription_id else None,
    }


def _notify_paid(payment: Payment, invoice: Invoice, subscription: Subscription) -> None:
    user = payment.user
    locale = user.locale if user.locale in ("ar", "en") else "ar"
    notifications.enqueue(
        user,
        "payment_succeeded",
        {
            "amount": money.display(payment.amount, payment.currency, locale),
            "plan_name": str(subscription.plan_snapshot.get(f"name_{locale}") or ""),
            "invoice_number": invoice.number or "",
            "ends_at": notifications.local_time(subscription.ends_at, user),
        },
        dedupe_key=f"payment_succeeded:{payment.pk}",
    )


def _complete(
    payment: Payment, *, actor: User | None, ip: str | None, now: datetime
) -> Subscription:
    """A payment that succeeded: issue its invoice and activate or extend the subscription."""
    invoice = payment.invoice
    plan = payment.plan
    if invoice is None or plan is None:
        msg = f"payment {payment.pk} has no invoice or plan"
        raise ValueError(msg)
    issue_invoice(invoice, now=now)
    subscription = subscriptions.activate(
        payment.user,
        plan,
        source=SubscriptionSource.PAYMENT,
        actor=actor,
        payment=payment,
        ip=ip,
    )
    if invoice.subscription_id != subscription.pk:
        invoice.subscription = subscription
        invoice.save(update_fields=["subscription", "updated_at"])
    _notify_paid(payment, invoice, subscription)
    PAYMENTS.labels(payment.provider, PaymentStatus.SUCCEEDED).inc()
    return subscription


def record_manual_payment(  # noqa: PLR0913 (keyword-only fields of one payment)
    *,
    user: User,
    plan: Plan | None = None,
    invoice: Invoice | None = None,
    amount: int | None = None,
    method: str = PaymentMethod.BANK_TRANSFER,
    reference: str = "",
    paid_at: datetime | None = None,
    idempotency_key: str = "",
    actor: User | None,
    ip: str | None = None,
) -> Payment:
    """An admin records money received by bank transfer or cash; the subscription is
    activated or extended, and the invoice issued.

    Pays the customer's pending `invoice` (a manual checkout) when given, else a new
    invoice for `plan`. `amount` defaults to the price; a different amount (a
    discount) is invoiced as received. Replaying an `idempotency_key` returns the
    payment recorded the first time.
    """
    if user.is_staff:
        raise _problem("user_id", "Payments are for customers.", "not_customer")
    key = idempotency_key or uuid.uuid4().hex
    existing = Payment.objects.filter(idempotency_key=key).first()
    if existing is not None:
        if existing.user_id != user.pk:
            raise _problem("idempotency_key", "This key belongs to another payment.", "key_used")
        return existing
    moment = timezone.now()
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=user.pk)
        if invoice is not None:
            invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
            if invoice.user_id != user.pk or invoice.status not in (
                InvoiceStatus.PENDING,
                InvoiceStatus.VOID,
            ):
                raise _problem("invoice_id", "Not an unpaid invoice of this customer.", "not_open")
            plan = invoice.plan
            if amount is not None and amount != invoice.total:
                raise _problem("amount", "Must match the invoice total.", "amount_mismatch")
            amount = invoice.total
            invoice.payments.filter(status=PaymentStatus.PENDING).update(
                status=PaymentStatus.CANCELLED, updated_at=moment
            )
        if plan is None:
            raise _problem("plan_id", "Choose the plan the payment is for.", "required")
        gross = amount if amount is not None else plan_price(plan).total
        if gross <= 0:
            raise _problem("amount", "Must be more than zero.", "min_value")
        if invoice is None:
            invoice = new_invoice(user, plan, gross, plan.currency)
            invoice.save()
        try:
            with transaction.atomic():
                payment = Payment.objects.create(
                    user=user,
                    plan=plan,
                    invoice=invoice,
                    provider=PaymentProviderCode.MANUAL,
                    method=method,
                    amount=gross,
                    currency=invoice.currency,
                    status=PaymentStatus.SUCCEEDED,
                    idempotency_key=key,
                    reference=reference.strip()[:100],
                    paid_at=paid_at or moment,
                    recorded_by=actor,
                )
        except IntegrityError:
            raise _problem("idempotency_key", "Already recorded.", "key_used") from None
        _complete(payment, actor=actor, ip=ip, now=moment)
        payment.refresh_from_db()
        audit.record(
            "payment.record", actor=actor, target=payment, after=payment_snapshot(payment), ip=ip
        )
    return payment


def _find_payment(provider: str, result: PaymentResult) -> Payment | None:
    rows = Payment.objects.select_for_update(of=("self",)).select_related("user", "plan", "invoice")
    if result.checkout_ref:
        found = rows.filter(provider=provider, checkout_ref=result.checkout_ref).first()
        if found is not None:
            return found
    if result.provider_ref:
        found = rows.filter(provider=provider, provider_ref=result.provider_ref).first()
        if found is not None:
            return found
    if result.invoice_id:
        try:
            invoice_id = UUID(result.invoice_id)
        except ValueError:
            return None
        return rows.filter(provider=provider, invoice_id=invoice_id).order_by("-created_at").first()
    return None


def apply_result(provider: str, result: PaymentResult) -> Payment | None:
    """What a verified provider event does to our payment; None when it names none.

    Idempotent: an outcome the payment already has changes nothing. Call inside a
    transaction (the webhook handler's).
    """
    payment = _find_payment(provider, result)
    if payment is None:
        logger.warning("%s event for an unknown payment", provider)
        return None
    raw = redact_value(result.raw)
    now = timezone.now()
    match result.outcome:
        case Outcome.SUCCEEDED:
            _succeed(payment, result, raw, now)
        case Outcome.FAILED:
            _fail(payment, result, raw)
        case Outcome.CANCELLED:
            if payment.status == PaymentStatus.PENDING:
                payment.status = PaymentStatus.CANCELLED
                payment.raw = raw
                payment.save(update_fields=["status", "raw", "updated_at"])
                if payment.invoice_id is not None:
                    Invoice.objects.filter(
                        pk=payment.invoice_id, status=InvoiceStatus.PENDING
                    ).update(status=InvoiceStatus.VOID, updated_at=now)
        case Outcome.REFUNDED:
            _record_provider_refund(payment, result, raw)
    return payment


def _succeed(payment: Payment, result: PaymentResult, raw: dict[str, Any], now: datetime) -> None:
    if payment.status in PAID_STATUSES:
        return
    if (result.amount is not None and int(result.amount) != payment.amount) or (
        result.currency and result.currency != payment.currency
    ):
        payment.status = PaymentStatus.FAILED
        payment.failure_reason = (
            f"Paid {result.amount} {result.currency}, expected {payment.amount} {payment.currency}."
        )
        payment.raw = raw
        payment.save(update_fields=["status", "failure_reason", "raw", "updated_at"])
        audit.record(
            "payment.amount_mismatch", actor=None, target=payment, after=payment_snapshot(payment)
        )
        logger.error("payment %s: amount or currency mismatch", payment.pk)
        return
    payment.status = PaymentStatus.SUCCEEDED
    payment.paid_at = now
    payment.raw = raw
    payment.failure_reason = ""
    if result.provider_ref:
        payment.provider_ref = result.provider_ref
    if result.method:
        payment.method = result.method
    payment.save()
    _complete(payment, actor=None, ip=None, now=now)
    audit.record("payment.succeed", actor=None, target=payment, after=payment_snapshot(payment))


def _fail(payment: Payment, result: PaymentResult, raw: dict[str, Any]) -> None:
    if payment.status != PaymentStatus.PENDING:
        return
    payment.status = PaymentStatus.FAILED
    payment.failure_reason = result.failure_reason[:200]
    payment.raw = raw
    payment.save(update_fields=["status", "failure_reason", "raw", "updated_at"])
    audit.record("payment.fail", actor=None, target=payment, after=payment_snapshot(payment))
    PAYMENTS.labels(payment.provider, PaymentStatus.FAILED).inc()
    user = payment.user
    locale = user.locale if user.locale in ("ar", "en") else "ar"
    plan = payment.plan
    notifications.enqueue(
        user,
        "payment_failed",
        {
            "amount": money.display(payment.amount, payment.currency, locale),
            "plan_name": (plan.name_ar if locale == "ar" else plan.name_en) if plan else "",
            "reason": payment.failure_reason,
        },
        dedupe_key=f"payment_failed:{payment.pk}",
    )


def _set_refunded(payment: Payment, refunded: int) -> None:
    payment.refunded_amount = min(refunded, payment.amount)
    payment.status = (
        PaymentStatus.REFUNDED
        if payment.refunded_amount >= payment.amount
        else PaymentStatus.PARTIALLY_REFUNDED
    )
    payment.save(update_fields=["refunded_amount", "status", "raw", "updated_at"])
    invoice = payment.invoice
    if invoice is not None and invoice.status in ISSUED_STATUSES:
        invoice.refunded_amount = min(
            invoice.total,
            sum(
                row.refunded_amount
                for row in Payment.objects.filter(invoice=invoice, status__in=PAID_STATUSES)
            ),
        )
        if invoice.refunded_amount >= invoice.total:
            invoice.status = InvoiceStatus.REFUNDED
        invoice.save(update_fields=["refunded_amount", "status", "updated_at"])


def _record_provider_refund(payment: Payment, result: PaymentResult, raw: dict[str, Any]) -> None:
    """A refund made in the provider's dashboard: mirror it (never shrink ours)."""
    if payment.status not in PAID_STATUSES or result.refunded_amount is None:
        return
    if result.refunded_amount <= payment.refunded_amount:
        return
    before = payment_snapshot(payment)
    payment.raw = raw
    _set_refunded(payment, result.refunded_amount)
    audit.record(
        "payment.refund",
        actor=None,
        target=payment,
        before=before,
        after={**payment_snapshot(payment), "source": "provider"},
    )
    PAYMENTS.labels(payment.provider, payment.status).inc()


def refund(  # noqa: PLR0913 (keyword-only options of one refund)
    payment: Payment,
    *,
    amount: int | None = None,
    reason: str = "",
    cancel_subscription: bool = False,
    actor: User | None,
    ip: str | None = None,
) -> Payment:
    """Refund all (default) or part of a payment through its provider.

    The payment row stays locked during the provider call, so two refunds never
    overlap. With `cancel_subscription`, the subscription it paid for ends now.
    """
    with transaction.atomic():
        payment = (
            Payment.objects.select_for_update(of=("self",))
            .select_related("user", "invoice", "subscription")
            .get(pk=payment.pk)
        )
        if payment.status not in (PaymentStatus.SUCCEEDED, PaymentStatus.PARTIALLY_REFUNDED):
            raise ProblemError(ErrorCode.CONFLICT, "Only paid payments can be refunded.")
        refundable = payment.amount - payment.refunded_amount
        value = refundable if amount is None else amount
        if not 0 < value <= refundable:
            raise _problem("amount", f"Refund between 1 and {refundable}.", "out_of_range")
        provider = get_provider(payment.provider)
        if provider is None:
            raise ProblemError(ErrorCode.PAYMENT_PROVIDER_UNAVAILABLE, "Unknown payment provider.")
        before = payment_snapshot(payment)
        try:
            result = provider.refund(payment, value)
        except ProviderError as exc:
            logger.warning("refund of payment %s failed: %s", payment.pk, exc)
            raise ProblemError(
                ErrorCode.PAYMENT_PROVIDER_ERROR, f"The provider refused the refund: {exc}"
            ) from None
        _set_refunded(payment, payment.refunded_amount + result.amount)
        audit.record(
            "payment.refund",
            actor=actor,
            target=payment,
            before=before,
            after={
                **payment_snapshot(payment),
                "refund": result.amount,
                "refund_reference": result.reference,
                "reason": reason,
            },
            ip=ip,
        )
        PAYMENTS.labels(payment.provider, payment.status).inc()
        _notify_refund(payment, result.amount)
        if cancel_subscription and payment.subscription is not None:
            subscription = payment.subscription
            if subscription.status in CURRENT_STATUSES:
                subscriptions.cancel(
                    subscription, reason=EndReason.REFUNDED, actor=actor, ip=ip, note=reason
                )
    return payment


def _notify_refund(payment: Payment, amount: int) -> None:
    user = payment.user
    locale = user.locale if user.locale in ("ar", "en") else "ar"
    notifications.enqueue(
        user,
        "payment_refunded",
        {
            "amount": money.display(amount, payment.currency, locale),
            "invoice_number": payment.invoice.number if payment.invoice else "",
        },
        dedupe_key=f"payment_refunded:{payment.pk}:{payment.refunded_amount}",
    )
