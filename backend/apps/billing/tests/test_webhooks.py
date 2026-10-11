"""POST /api/v1/webhooks/{provider} on api.<domain>: signatures, idempotency, audit."""

import json
import time
from collections.abc import Callable
from typing import Any

import pytest
from django.conf import settings
from prometheus_client import REGISTRY
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.billing import services
from apps.billing.models import (
    InvoiceStatus,
    Payment,
    PaymentMethod,
    PaymentStatus,
    Plan,
    Subscription,
    SubscriptionStatus,
    WebhookEvent,
)
from apps.billing.providers.stripe import sign
from apps.billing.tests.conftest import Recorded, configure, fixture_json
from apps.conftest import CustomerFactory

pytestmark = pytest.mark.django_db
type Capture = Callable[..., Any]

API = {"host": settings.API_HOST}
STRIPE_URL = "/api/v1/webhooks/stripe"
MOYASAR_URL = "/api/v1/webhooks/moyasar"


def webhooks(result: str, provider: str = "stripe") -> float:
    """iptv_payment_webhooks_total{provider,result} (ADR-0018)."""
    value = REGISTRY.get_sample_value(
        "iptv_payment_webhooks_total", {"provider": provider, "result": result}
    )
    return value or 0.0


@pytest.fixture
def customer(make_customer: CustomerFactory) -> User:
    return make_customer(devices=1)


def filled(name: str, **values: str) -> dict[str, Any]:
    raw = json.dumps(fixture_json(name))
    for key, value in values.items():
        raw = raw.replace(f"__{key.upper()}__", value)
    data: dict[str, Any] = json.loads(raw)
    return data


def stripe_checkout(customer: User, plan: Plan, recorded: Recorded) -> Payment:
    configure("billing.stripe_enabled", True)
    recorded.add(
        "POST",
        "https://api.stripe.com/v1/checkout/sessions",
        200,
        fixture_json("stripe_checkout_session.json"),
    )
    result = services.checkout(customer, plan, "stripe")
    assert result.payment is not None
    return result.payment


def stripe_post(body: bytes, *, secret: str = "whsec_fixture", at: int | None = None) -> Any:  # noqa: S107
    timestamp = at or int(time.time())
    signature = sign(body, secret, timestamp)
    return APIClient().post(
        STRIPE_URL,
        body,
        content_type="application/json",
        headers={**API, "Stripe-Signature": f"t={timestamp},v1={signature}"},
    )


def completed_event(payment: Payment) -> bytes:
    event = filled(
        "stripe_event_checkout_completed.json",
        checkout=payment.checkout_ref,
        invoice=str(payment.invoice_id),
        payment=str(payment.pk),
    )
    return json.dumps(event).encode()


def test_a_signed_stripe_event_pays_the_checkout_once(
    customer: User, plan: Plan, provider_keys: Any, recorded: Recorded
) -> None:
    payment = stripe_checkout(customer, plan, recorded)
    body = completed_event(payment)
    processed, duplicates = webhooks("processed"), webhooks("duplicate")
    response = stripe_post(body)
    assert response.status_code == 200
    assert response.json() == {"received": True, "duplicate": False}
    assert webhooks("processed") == processed + 1
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.SUCCEEDED
    assert payment.provider_ref == "pi_3QfixturePaymentIntent"
    assert payment.method == PaymentMethod.CARD
    assert Subscription.objects.get(user=customer).status == SubscriptionStatus.ACTIVE
    assert payment.invoice is not None
    assert payment.invoice.status == InvoiceStatus.PAID
    event = WebhookEvent.objects.get()
    assert (event.type, event.payment, event.attempts) == (
        "checkout.session.completed",
        payment,
        1,
    )
    assert event.processed_at is not None
    assert AuditLog.objects.filter(action="payment.webhook").count() == 1
    # Stripe delivers again: acknowledged, nothing applied twice.
    again = stripe_post(body)
    assert again.json() == {"received": True, "duplicate": True}
    assert webhooks("duplicate") == duplicates + 1
    assert Subscription.objects.get(user=customer).ends_at == payment.subscription.ends_at  # type: ignore[union-attr]
    assert WebhookEvent.objects.count() == 1


@pytest.mark.parametrize(
    ("header", "secret", "age"),
    [
        ("missing", "whsec_fixture", 0),
        ("ok", "whsec_wrong", 0),
        ("ok", "whsec_fixture", 600),  # replayed after the tolerance
        ("no-timestamp", "whsec_fixture", 0),
    ],
)
def test_unsigned_or_stale_stripe_events_are_refused(  # noqa: PLR0917 (fixtures)
    customer: User,
    plan: Plan,
    provider_keys: Any,
    recorded: Recorded,
    header: str,
    secret: str,
    age: int,
) -> None:
    payment = stripe_checkout(customer, plan, recorded)
    body = completed_event(payment)
    if header == "missing":
        response = APIClient().post(STRIPE_URL, body, content_type="application/json", headers=API)
    elif header == "no-timestamp":
        response = APIClient().post(
            STRIPE_URL,
            body,
            content_type="application/json",
            headers={**API, "Stripe-Signature": "v1=abc"},
        )
    else:
        response = stripe_post(body, secret=secret, at=int(time.time()) - age)
    assert response.status_code == 400
    assert response.json()["code"] == "WEBHOOK_INVALID"
    assert not WebhookEvent.objects.exists()
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.PENDING


def test_bodies_must_be_json_events(provider_keys: Any) -> None:
    invalid = webhooks("invalid")
    assert stripe_post(b"not json").json()["code"] == "WEBHOOK_INVALID"
    assert stripe_post(b"[1]").json()["code"] == "WEBHOOK_INVALID"
    assert stripe_post(b'{"type": "x"}').json()["code"] == "WEBHOOK_INVALID"
    assert webhooks("invalid") == invalid + 3


def test_unknown_or_unconfigured_providers_are_not_found(db: None) -> None:
    client = APIClient()
    assert client.post("/api/v1/webhooks/paypal", {}, format="json", headers=API).status_code == 404
    # Stripe without keys.
    assert client.post(STRIPE_URL, {}, format="json", headers=API).status_code == 404
    # Manual payments have no webhooks.
    assert client.post("/api/v1/webhooks/manual", {}, format="json", headers=API).status_code == 400


def test_webhooks_live_on_the_api_host_only(provider_keys: Any) -> None:
    response = APIClient().post(STRIPE_URL, {}, format="json", headers={"host": settings.APP_HOST})
    assert response.status_code == 404


def test_events_we_do_not_use_are_recorded_and_acknowledged(provider_keys: Any) -> None:
    body = json.dumps({"id": "evt_other", "type": "customer.created", "data": {"object": {}}})
    assert stripe_post(body.encode()).status_code == 200
    event = WebhookEvent.objects.get()
    assert event.processed_at is not None
    assert event.payment is None


def test_a_failure_is_kept_and_the_retry_applies_it(
    customer: User,
    plan: Plan,
    provider_keys: Any,
    recorded: Recorded,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payment = stripe_checkout(customer, plan, recorded)
    body = completed_event(payment)

    def broken(provider: str, result: Any) -> None:
        raise RuntimeError("database hiccup with password=hunter2")

    original = services.apply_result
    monkeypatch.setattr(services, "apply_result", broken)
    failed = webhooks("failed")
    response = stripe_post(body)
    assert response.status_code == 500
    assert webhooks("failed") == failed + 1
    event = WebhookEvent.objects.get()
    assert event.processed_at is None
    assert "hiccup" in event.error
    assert "hunter2" not in event.error
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.PENDING
    monkeypatch.setattr(services, "apply_result", original)
    assert stripe_post(body).json()["duplicate"] is False
    event.refresh_from_db()
    assert (event.attempts, event.error) == (2, "")
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.SUCCEEDED


def test_async_and_expiry_events(
    customer: User, plan: Plan, provider_keys: Any, recorded: Recorded
) -> None:
    payment = stripe_checkout(customer, plan, recorded)
    event = filled(
        "stripe_event_checkout_completed.json",
        checkout=payment.checkout_ref,
        invoice=str(payment.invoice_id),
        payment=str(payment.pk),
    )
    event["data"]["object"]["payment_status"] = "unpaid"  # e.g. a bank debit still running
    assert stripe_post(json.dumps(event).encode()).status_code == 200
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.PENDING
    event["id"] = "evt_failed"
    event["type"] = "checkout.session.async_payment_failed"
    stripe_post(json.dumps(event).encode())
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.FAILED
    second = stripe_checkout(customer, plan, recorded)
    event = filled(
        "stripe_event_checkout_completed.json",
        checkout=second.checkout_ref,
        invoice=str(second.invoice_id),
        payment=str(second.pk),
    )
    event["id"], event["type"] = "evt_expired", "checkout.session.expired"
    stripe_post(json.dumps(event).encode())
    second.refresh_from_db()
    assert second.status == PaymentStatus.CANCELLED


def test_stripe_dashboard_refunds(customer: User, plan: Plan, provider_keys: Any) -> None:
    payment = services.record_manual_payment(user=customer, plan=plan, actor=None)
    Payment.objects.filter(pk=payment.pk).update(provider="stripe", provider_ref="pi_dash")
    event = {
        "id": "evt_refund",
        "type": "charge.refunded",
        "data": {
            "object": {"payment_intent": "pi_dash", "amount_refunded": 4900, "currency": "sar"}
        },
    }
    assert stripe_post(json.dumps(event).encode()).status_code == 200
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.REFUNDED


def moyasar_checkout(customer: User, plan: Plan, recorded: Recorded) -> Payment:
    configure("billing.moyasar_enabled", True)
    recorded.add(
        "POST", "https://api.moyasar.com/v1/invoices", 201, fixture_json("moyasar_invoice.json")
    )
    result = services.checkout(customer, plan, "moyasar")
    assert result.payment is not None
    return result.payment


def test_a_moyasar_mada_payment(
    customer: User, plan: Plan, provider_keys: Any, recorded: Recorded
) -> None:
    payment = moyasar_checkout(customer, plan, recorded)
    event = filled("moyasar_event_payment_paid.json", checkout=payment.checkout_ref)
    response = APIClient().post(MOYASAR_URL, event, format="json", headers=API)
    assert response.status_code == 200
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.SUCCEEDED
    assert payment.method == PaymentMethod.MADA
    assert payment.raw["source"]["token"] == "***"  # noqa: S105 (redacted, even when empty)
    stored = WebhookEvent.objects.get()
    assert stored.payload["secret_token"] == "***"  # noqa: S105 (never stored)
    assert Subscription.objects.get(user=customer).status == SubscriptionStatus.ACTIVE


def test_moyasar_needs_the_secret_token(
    customer: User, plan: Plan, provider_keys: Any, recorded: Recorded
) -> None:
    payment = moyasar_checkout(customer, plan, recorded)
    event = filled("moyasar_event_payment_paid.json", checkout=payment.checkout_ref)
    event["secret_token"] = "guess"  # noqa: S105
    response = APIClient().post(MOYASAR_URL, event, format="json", headers=API)
    assert response.status_code == 400
    del event["secret_token"]
    assert APIClient().post(MOYASAR_URL, event, format="json", headers=API).status_code == 400
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.PENDING


@pytest.mark.parametrize(
    ("event_type", "expected"),
    [
        ("payment_failed", PaymentStatus.FAILED),
        ("payment_faild", PaymentStatus.FAILED),  # as Moyasar's reference spells it
        ("payment_voided", PaymentStatus.CANCELLED),
        ("payment_authorized", PaymentStatus.PENDING),  # not used: recorded only
    ],
)
def test_moyasar_other_events(  # noqa: PLR0917 (fixtures)
    customer: User,
    plan: Plan,
    provider_keys: Any,
    recorded: Recorded,
    event_type: str,
    expected: str,
) -> None:
    payment = moyasar_checkout(customer, plan, recorded)
    event = filled("moyasar_event_payment_paid.json", checkout=payment.checkout_ref)
    event["type"] = event_type
    event["data"]["status"] = "failed"
    event["data"]["source"]["message"] = "INSUFFICIENT FUNDS"
    assert APIClient().post(MOYASAR_URL, event, format="json", headers=API).status_code == 200
    payment.refresh_from_db()
    assert payment.status == expected
    if expected == PaymentStatus.FAILED:
        assert payment.failure_reason == "INSUFFICIENT FUNDS"


def test_moyasar_refund_events(
    customer: User, plan: Plan, provider_keys: Any, recorded: Recorded
) -> None:
    payment = moyasar_checkout(customer, plan, recorded)
    event = filled("moyasar_event_payment_paid.json", checkout=payment.checkout_ref)
    APIClient().post(MOYASAR_URL, event, format="json", headers=API)
    event["id"], event["type"] = "refund-1", "payment_refunded"
    event["data"]["refunded"] = 1500
    APIClient().post(MOYASAR_URL, event, format="json", headers=API)
    payment.refresh_from_db()
    assert (payment.status, payment.refunded_amount) == (PaymentStatus.PARTIALLY_REFUNDED, 1500)
