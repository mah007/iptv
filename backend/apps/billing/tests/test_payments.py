"""Plans, invoices, checkout, manual payments and refunds (billing.services)."""

import json
from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.billing import invoices, services, subscriptions
from apps.billing.models import (
    EndReason,
    Invoice,
    InvoiceStatus,
    Payment,
    PaymentMethod,
    PaymentStatus,
    Plan,
    Subscription,
    SubscriptionStatus,
)
from apps.billing.providers.base import Outcome, PaymentResult
from apps.billing.tests.conftest import PlanFactory, Recorded, configure, fixture_json
from apps.catalog.models import Category
from apps.conftest import CustomerFactory
from apps.core.errors import ErrorCode, ProblemError
from apps.notifications.models import NotificationOutbox

pytestmark = pytest.mark.django_db
type Capture = Callable[..., Any]

STRIPE_SESSIONS = "https://api.stripe.com/v1/checkout/sessions"
MOYASAR_INVOICES = "https://api.moyasar.com/v1/invoices"


@pytest.fixture
def customer(make_customer: CustomerFactory) -> User:
    return make_customer(devices=1)


def record(user: User, plan: Plan, **kwargs: Any) -> Payment:
    kwargs.setdefault("actor", None)
    return services.record_manual_payment(user=user, plan=plan, **kwargs)


# --- Plans --------------------------------------------------------------------------------


def test_plan_changes_make_versions_and_are_audited(
    plan: Plan, category: Category, owner: User
) -> None:
    services.update_plan(plan, {"sort": 5, "active": False}, actor=owner)
    plan.refresh_from_db()
    assert plan.version == 1  # order and availability are not versions
    services.update_plan(plan, {"price": 5900}, category_ids=[category.pk], actor=owner)
    plan.refresh_from_db()
    assert plan.version == 2
    entry = AuditLog.objects.filter(action="plan.update").latest("at")
    assert entry.after == {"price": 5900, "version": 2, "category_ids": [str(category.pk)]}
    assert services.update_plan(plan, {"price": 5900}, actor=owner).version == 2  # no change


def test_plan_validation(make_plan: PlanFactory, plan: Plan) -> None:
    with pytest.raises(ProblemError):
        make_plan(code="standard")
    with pytest.raises(ProblemError):
        make_plan(duration_days=0, duration_months=0)
    with pytest.raises(ProblemError):
        services.create_plan(
            {"code": "x", "name_en": "X", "name_ar": "س", "price": 1},
            category_ids=["0190f2c6-0000-7000-8000-000000000000"],
            actor=None,
        )
    assert make_plan(code="no-currency").currency == "SAR"


def test_only_unused_plans_are_deleted(make_plan: PlanFactory, customer: User) -> None:
    unused = make_plan()
    services.delete_plan(unused, actor=None)
    assert not Plan.objects.filter(pk=unused.pk).exists()
    used = make_plan()
    subscriptions.activate(customer, used, source="manual")
    with pytest.raises(ProblemError) as excinfo:
        services.delete_plan(used, actor=None)
    assert excinfo.value.problem_code == ErrorCode.CONFLICT


def test_reorder_needs_every_plan_once(make_plan: PlanFactory) -> None:
    first, second = make_plan(), make_plan()
    services.reorder_plans([second.pk, first.pk], actor=None, ip=None)
    assert list(Plan.objects.order_by("sort")) == [second, first]
    with pytest.raises(ProblemError):
        services.reorder_plans([second.pk], actor=None, ip=None)


def test_migrating_subscriptions_takes_the_latest_version(
    customer: User, plan: Plan, django_capture_on_commit_callbacks: Capture
) -> None:
    subscription = subscriptions.activate(customer, plan, source="manual")
    services.update_plan(plan, {"max_streams": 5}, actor=None)
    subscription.refresh_from_db()
    assert subscription.plan_snapshot["max_streams"] == 2
    with django_capture_on_commit_callbacks(execute=True):
        assert services.migrate_subscriptions(plan, actor=None) == 1
    subscription.refresh_from_db()
    assert subscription.plan_snapshot["max_streams"] == 5
    assert subscription.plan_snapshot["version"] == 2


def test_prices_with_and_without_vat(plan: Plan) -> None:
    assert services.plan_price(plan).total == 4900
    configure("billing.prices_include_vat", False)
    assert services.plan_price(plan).total == 5635


# --- Manual payments and invoices -------------------------------------------------------------


def test_a_manual_payment_activates_issues_an_invoice_and_notifies(
    customer: User, plan: Plan, owner: User
) -> None:
    payment = record(customer, plan, reference="TRX-1", method=PaymentMethod.CASH, actor=owner)
    assert payment.status == PaymentStatus.SUCCEEDED
    assert payment.amount == 4900
    assert payment.recorded_by == owner
    subscription = payment.subscription
    assert subscription is not None
    assert subscription.status == SubscriptionStatus.ACTIVE
    invoice = payment.invoice
    assert invoice is not None
    assert invoice.status == InvoiceStatus.PAID
    year = timezone.now().year
    assert invoice.number == f"INV-{year}-000001"
    assert (invoice.subtotal, invoice.vat_amount, invoice.total) == (4261, 639, 4900)
    assert invoice.vat_rate == Decimal("0.1500")
    assert invoice.subscription == subscription
    assert invoice.bill_to["username"] == customer.username
    assert NotificationOutbox.objects.filter(template_key="payment_succeeded").count() == 1
    assert not NotificationOutbox.objects.filter(template_key="subscription_renewed").exists()
    assert AuditLog.objects.filter(action="payment.record", actor=owner).exists()


def test_numbers_are_sequential_per_year(make_customer: CustomerFactory, plan: Plan) -> None:
    numbers = [record(make_customer(), plan).invoice.number for _ in range(3)]  # type: ignore[union-attr]
    year = timezone.now().year
    assert numbers == [f"INV-{year}-00000{n}" for n in (1, 2, 3)]
    configure("billing.invoice_prefix", "SIPTV")
    assert record(make_customer(), plan).invoice.number == f"SIPTV-{year}-000004"  # type: ignore[union-attr]


def test_a_second_payment_extends_the_same_subscription(customer: User, plan: Plan) -> None:
    first = record(customer, plan)
    second = record(customer, plan)
    assert first.subscription == second.subscription
    assert Subscription.objects.count() == 1


def test_idempotent_recording(customer: User, plan: Plan, make_customer: CustomerFactory) -> None:
    first = record(customer, plan, idempotency_key="form-123")
    assert record(customer, plan, idempotency_key="form-123") == first
    assert Payment.objects.count() == 1
    with pytest.raises(ProblemError):
        record(make_customer(), plan, idempotency_key="form-123")


def test_a_discounted_amount_is_invoiced_as_received(customer: User, plan: Plan) -> None:
    payment = record(customer, plan, amount=3000)
    invoice = payment.invoice
    assert invoice is not None
    assert (invoice.total, invoice.subtotal + invoice.vat_amount) == (3000, 3000)
    with pytest.raises(ProblemError):
        record(customer, plan, amount=0)


def test_paying_a_pending_manual_checkout(customer: User, plan: Plan) -> None:
    result = services.checkout(customer, plan, "manual")
    assert result.invoice is not None
    assert result.invoice.status == InvoiceStatus.PENDING
    assert result.invoice.number is None
    payment = services.record_manual_payment(user=customer, invoice=result.invoice, actor=None)
    result.invoice.refresh_from_db()
    assert result.invoice.status == InvoiceStatus.PAID
    assert payment.invoice == result.invoice
    pending = result.payment
    assert pending is not None
    pending.refresh_from_db()
    assert pending.status == PaymentStatus.CANCELLED
    with pytest.raises(ProblemError):  # paid already
        services.record_manual_payment(user=customer, invoice=result.invoice, actor=None)
    with pytest.raises(ProblemError):
        services.record_manual_payment(user=customer, actor=None)  # no plan or invoice


def test_payments_are_for_customers(staff_user: User, plan: Plan) -> None:
    with pytest.raises(ProblemError):
        record(staff_user, plan)


def test_invoice_document_in_both_languages(customer: User, plan: Plan) -> None:
    configure("billing.vat_number", "300123456700003")
    invoice = record(customer, plan, reference="TRX-77").invoice
    assert invoice is not None
    arabic = invoices.render(invoice)
    assert 'dir="rtl"' in arabic
    assert "فاتورة ضريبية" in arabic
    assert invoice.number in arabic  # type: ignore[operator]
    assert "300123456700003" in arabic
    assert "49.00 ر.س" in arabic
    assert "6.39 ر.س" in arabic
    assert "TRX-77" in arabic
    english = invoices.render(invoice, locale="en")
    assert 'dir="ltr"' in english
    assert "Tax invoice" in english
    assert "42.61 SAR" in english
    assert "VAT (15%)" in english
    assert invoices.filename(invoice) == f"{invoice.number}.html"


# --- Checkout ------------------------------------------------------------------------------


def test_manual_checkout_gives_instructions(customer: User, plan: Plan) -> None:
    result = services.checkout(customer, plan, "manual")
    assert result.session is not None
    assert result.session.redirect_url is None
    assert result.session.reference.startswith("P-")
    assert "transfer" in result.session.instructions["en"].lower()
    assert AuditLog.objects.filter(action="payment.checkout", actor=customer).exists()


def test_a_new_checkout_voids_the_previous_one(customer: User, plan: Plan) -> None:
    first = services.checkout(customer, plan, "manual")
    second = services.checkout(customer, plan, "manual")
    assert first.invoice is not None
    assert second.invoice is not None
    first.invoice.refresh_from_db()
    assert first.invoice.status == InvoiceStatus.VOID
    assert second.invoice.status == InvoiceStatus.PENDING


def test_checkout_refusals(customer: User, plan: Plan, make_plan: PlanFactory) -> None:
    with pytest.raises(ProblemError) as excinfo:
        services.checkout(customer, plan, "stripe")  # no keys
    assert excinfo.value.problem_code == ErrorCode.PAYMENT_PROVIDER_UNAVAILABLE
    with pytest.raises(ProblemError):
        services.checkout(customer, plan, "paypal")
    with pytest.raises(ProblemError):
        services.checkout(customer, make_plan(active=False), "manual")
    with pytest.raises(ProblemError):
        services.checkout(customer, make_plan(price=0), "manual")
    with pytest.raises(ProblemError):
        services.checkout(customer, plan, "manual", return_url="https://evil.example.com/")


def test_checkout_of_a_trial_plan_requests_the_trial(customer: User, trial_plan: Plan) -> None:
    result = services.checkout(customer, trial_plan, "manual")
    assert result.invoice is None
    assert result.subscription is not None
    assert result.subscription.status == SubscriptionStatus.PENDING


def test_stripe_checkout(
    customer: User, plan: Plan, provider_keys: Any, recorded: Recorded
) -> None:
    configure("billing.stripe_enabled", True)
    recorded.add("POST", STRIPE_SESSIONS, 200, fixture_json("stripe_checkout_session.json"))
    result = services.checkout(
        customer, plan, "stripe", return_url=services.default_return_url() + "?tab=billing"
    )
    assert result.session is not None
    assert result.session.redirect_url is not None
    assert result.session.redirect_url.startswith("https://checkout.stripe.com/")
    assert result.payment is not None
    assert result.payment.checkout_ref == "cs_test_a1B2c3D4e5F6g7H8i9J0"
    request = recorded.requests[0]
    assert request.headers["Authorization"] == "Bearer sk_test_fixture"
    assert request.headers["Idempotency-Key"].startswith("checkout-")
    form = dict(item.split("=", 1) for item in request.content.decode().split("&"))
    assert form["line_items%5B0%5D%5Bprice_data%5D%5Bunit_amount%5D"] == "4900"
    assert form["line_items%5B0%5D%5Bprice_data%5D%5Bcurrency%5D"] == "sar"
    assert form["metadata%5Binvoice_id%5D"] == str(result.invoice.pk)  # type: ignore[union-attr]


def test_a_provider_failure_voids_the_checkout(
    customer: User, plan: Plan, provider_keys: Any, recorded: Recorded
) -> None:
    configure("billing.stripe_enabled", True)
    recorded.add("POST", STRIPE_SESSIONS, 401, fixture_json("stripe_error.json"))
    with pytest.raises(ProblemError) as excinfo:
        services.checkout(customer, plan, "stripe")
    assert excinfo.value.problem_code == ErrorCode.PAYMENT_PROVIDER_ERROR
    assert Payment.objects.get().status == PaymentStatus.FAILED
    assert Invoice.objects.get().status == InvoiceStatus.VOID
    assert "sk_test_fixture" not in Payment.objects.get().failure_reason


def test_moyasar_checkout(
    customer: User, plan: Plan, provider_keys: Any, recorded: Recorded
) -> None:
    configure("billing.moyasar_enabled", True)
    recorded.add("POST", MOYASAR_INVOICES, 201, fixture_json("moyasar_invoice.json"))
    result = services.checkout(customer, plan, "moyasar")
    assert result.session is not None
    assert result.session.redirect_url == fixture_json("moyasar_invoice.json")["url"]
    body = json.loads(recorded.requests[0].content)
    assert body["amount"] == 4900
    assert body["currency"] == "SAR"
    assert body["metadata"]["invoice_id"] == str(result.invoice.pk)  # type: ignore[union-attr]
    assert recorded.requests[0].headers["Authorization"].startswith("Basic ")


def test_stale_checkouts_are_voided(customer: User, plan: Plan) -> None:
    result = services.checkout(customer, plan, "manual")
    assert services.expire_checkouts(now=timezone.now() + timedelta(hours=23)) == 0
    assert services.expire_checkouts(now=timezone.now() + timedelta(hours=25)) == 1
    assert result.payment is not None
    result.payment.refresh_from_db()
    assert result.payment.status == PaymentStatus.CANCELLED


# --- Provider results ---------------------------------------------------------------------------


def _stripe_checkout(customer: User, plan: Plan, recorded: Recorded) -> Payment:
    configure("billing.stripe_enabled", True)
    recorded.add("POST", STRIPE_SESSIONS, 200, fixture_json("stripe_checkout_session.json"))
    result = services.checkout(customer, plan, "stripe")
    assert result.payment is not None
    return result.payment


def test_a_successful_result_completes_once(
    customer: User, plan: Plan, provider_keys: Any, recorded: Recorded
) -> None:
    payment = _stripe_checkout(customer, plan, recorded)
    result = PaymentResult(
        outcome=Outcome.SUCCEEDED,
        checkout_ref=payment.checkout_ref,
        provider_ref="pi_1",
        amount=4900,
        currency="SAR",
        method=PaymentMethod.CARD,
        raw={"id": payment.checkout_ref, "client_secret": "cs_secret_123"},
    )
    services.apply_result("stripe", result)
    services.apply_result("stripe", result)
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.SUCCEEDED
    assert payment.provider_ref == "pi_1"
    assert payment.raw["client_secret"] == "***"  # noqa: S105
    assert Subscription.objects.get().status == SubscriptionStatus.ACTIVE
    assert Invoice.objects.get().status == InvoiceStatus.PAID
    assert NotificationOutbox.objects.filter(template_key="payment_succeeded").count() == 1


def test_an_amount_mismatch_never_activates(
    customer: User, plan: Plan, provider_keys: Any, recorded: Recorded
) -> None:
    payment = _stripe_checkout(customer, plan, recorded)
    services.apply_result(
        "stripe",
        PaymentResult(
            Outcome.SUCCEEDED, checkout_ref=payment.checkout_ref, amount=100, currency="SAR"
        ),
    )
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.FAILED
    assert "expected 4900" in payment.failure_reason
    assert not Subscription.objects.exists()


def test_failed_and_cancelled_results(
    customer: User, plan: Plan, provider_keys: Any, recorded: Recorded
) -> None:
    payment = _stripe_checkout(customer, plan, recorded)
    services.apply_result(
        "stripe",
        PaymentResult(Outcome.FAILED, checkout_ref=payment.checkout_ref, failure_reason="Declined"),
    )
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.FAILED
    failed = NotificationOutbox.objects.get(template_key="payment_failed")
    assert failed.payload["reason"] == "Declined"
    second = _stripe_checkout(customer, plan, recorded)
    services.apply_result(
        "stripe", PaymentResult(Outcome.CANCELLED, checkout_ref=second.checkout_ref)
    )
    second.refresh_from_db()
    assert second.status == PaymentStatus.CANCELLED
    assert second.invoice is not None
    second.invoice.refresh_from_db()
    assert second.invoice.status == InvoiceStatus.VOID


def test_results_for_unknown_payments_are_ignored() -> None:
    assert (
        services.apply_result("stripe", PaymentResult(Outcome.SUCCEEDED, checkout_ref="cs_x"))
        is None
    )
    assert (
        services.apply_result("stripe", PaymentResult(Outcome.SUCCEEDED, invoice_id="nope")) is None
    )


def test_a_late_payment_of_a_voided_checkout_still_counts(customer: User, plan: Plan) -> None:
    first = services.checkout(customer, plan, "manual")
    services.checkout(customer, plan, "manual")  # voids the first
    assert first.payment is not None
    Payment.objects.filter(pk=first.payment.pk).update(checkout_ref="moy-1", provider="moyasar")
    services.apply_result(
        "moyasar",
        PaymentResult(Outcome.SUCCEEDED, checkout_ref="moy-1", amount=4900, currency="SAR"),
    )
    invoice = Invoice.objects.get(pk=first.invoice.pk)  # type: ignore[union-attr]
    assert invoice.status == InvoiceStatus.PAID
    assert invoice.number is not None


def test_provider_dashboard_refunds_are_mirrored(customer: User, plan: Plan) -> None:
    payment = record(customer, plan)
    Payment.objects.filter(pk=payment.pk).update(provider="stripe", provider_ref="pi_9")
    services.apply_result(
        "stripe", PaymentResult(Outcome.REFUNDED, provider_ref="pi_9", refunded_amount=1000)
    )
    payment.refresh_from_db()
    assert (payment.status, payment.refunded_amount) == (PaymentStatus.PARTIALLY_REFUNDED, 1000)
    services.apply_result(
        "stripe", PaymentResult(Outcome.REFUNDED, provider_ref="pi_9", refunded_amount=500)
    )
    payment.refresh_from_db()
    assert payment.refunded_amount == 1000  # never shrinks


# --- Refunds ----------------------------------------------------------------------------------


def test_manual_refunds_partial_then_full(customer: User, plan: Plan, owner: User) -> None:
    payment = record(customer, plan)
    services.refund(payment, amount=900, reason="goodwill", actor=owner)
    payment.refresh_from_db()
    assert (payment.status, payment.refunded_amount) == (PaymentStatus.PARTIALLY_REFUNDED, 900)
    invoice = payment.invoice
    assert invoice is not None
    invoice.refresh_from_db()
    assert (invoice.status, invoice.refunded_amount) == (InvoiceStatus.PAID, 900)
    with pytest.raises(ProblemError):
        services.refund(payment, amount=5000, actor=owner)
    services.refund(payment, actor=owner)  # the rest
    payment.refresh_from_db()
    invoice.refresh_from_db()
    assert payment.status == PaymentStatus.REFUNDED
    assert invoice.status == InvoiceStatus.REFUNDED
    assert NotificationOutbox.objects.filter(template_key="payment_refunded").count() == 2
    entry = AuditLog.objects.filter(action="payment.refund").latest("at")
    assert (entry.after or {})["refund"] == 4000
    with pytest.raises(ProblemError) as excinfo:
        services.refund(payment, actor=owner)
    assert excinfo.value.problem_code == ErrorCode.CONFLICT


def test_a_refund_may_end_the_subscription(
    customer: User, plan: Plan, django_capture_on_commit_callbacks: Capture
) -> None:
    payment = record(customer, plan)
    with django_capture_on_commit_callbacks(execute=True):
        services.refund(payment, cancel_subscription=True, actor=None)
    subscription = Subscription.objects.get()
    assert subscription.status == SubscriptionStatus.CANCELLED
    assert subscription.end_reason == EndReason.REFUNDED


def test_stripe_refund(customer: User, plan: Plan, provider_keys: Any, recorded: Recorded) -> None:
    payment = record(customer, plan)
    Payment.objects.filter(pk=payment.pk).update(
        provider="stripe", provider_ref="pi_3QfixturePaymentIntent"
    )
    recorded.add(
        "POST", "https://api.stripe.com/v1/refunds", 200, fixture_json("stripe_refund.json")
    )
    services.refund(payment, actor=None)
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.REFUNDED
    assert b"payment_intent=pi_3QfixturePaymentIntent" in recorded.requests[0].content


def test_moyasar_refund_and_provider_errors(
    customer: User, plan: Plan, provider_keys: Any, recorded: Recorded
) -> None:
    payment = record(customer, plan)
    paid_id = fixture_json("moyasar_refund.json")["id"]
    Payment.objects.filter(pk=payment.pk).update(provider="moyasar", provider_ref=paid_id)
    recorded.add(
        "POST",
        f"https://api.moyasar.com/v1/payments/{paid_id}/refund",
        200,
        fixture_json("moyasar_refund.json"),
    )
    services.refund(payment, amount=2000, actor=None)
    payment.refresh_from_db()
    assert payment.refunded_amount == 2000
    # The provider confirms less than asked: refused, nothing recorded.
    with pytest.raises(ProblemError) as excinfo:
        services.refund(payment, amount=1000, actor=None)
    assert excinfo.value.problem_code == ErrorCode.PAYMENT_PROVIDER_ERROR
    payment.refresh_from_db()
    assert payment.refunded_amount == 2000
