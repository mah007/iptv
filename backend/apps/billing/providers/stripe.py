"""Stripe adapter: hosted Checkout Sessions, signed webhooks, refunds (ADR-0012).

Talks to Stripe's REST API directly over httpx (form-encoded requests, bearer
secret key, an Idempotency-Key per request). Test keys (`sk_test_...`) use Stripe's
sandbox; nothing in this module changes between test and live mode.

Webhooks: `Stripe-Signature: t=<unix>,v1=<hex>` where v1 is HMAC-SHA256 of
`"<t>.<raw body>"` under the endpoint's signing secret; events older than
`SIGNATURE_TOLERANCE_S` are refused (replay protection).
Events used:
- `checkout.session.completed` (paid) and `checkout.session.async_payment_succeeded`;
- `checkout.session.async_payment_failed`; `checkout.session.expired`;
- `charge.refunded` (refunds made in Stripe's dashboard).
"""

import hashlib
import hmac
import time
from datetime import timedelta
from typing import Any, Final

import httpx
from django.conf import settings
from django.http import HttpRequest
from django.utils import timezone

from apps.billing.models import Invoice, Payment, PaymentMethod, PaymentProviderCode
from apps.billing.providers.base import (
    CheckoutSession,
    Outcome,
    PaymentProvider,
    PaymentResult,
    ProviderError,
    ProviderEvent,
    RefundResult,
    WebhookError,
    http_client,
    json_body,
    provider_error,
    with_query,
)
from apps.core.services import get_setting

API_BASE: Final = "https://api.stripe.com/v1"
SIGNATURE_TOLERANCE_S: Final = 300
#: Stripe accepts Checkout Sessions that expire 30 minutes to 24 hours from now.
_MIN_EXPIRY = timedelta(minutes=30)
_MAX_EXPIRY = timedelta(hours=23, minutes=55)


def _flatten(prefix: str, value: Any, into: dict[str, str]) -> None:
    """Stripe's form encoding: `a[b][0][c]=value`."""
    if isinstance(value, dict):
        for key, item in value.items():
            _flatten(f"{prefix}[{key}]" if prefix else str(key), item, into)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _flatten(f"{prefix}[{index}]", item, into)
    elif value is not None:
        into[prefix] = str(value).lower() if isinstance(value, bool) else str(value)


def form(data: dict[str, Any]) -> dict[str, str]:
    flat: dict[str, str] = {}
    _flatten("", data, flat)
    return flat


def sign(payload: bytes, secret: str, timestamp: int) -> str:
    message = f"{timestamp}.".encode() + payload
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


class StripeProvider(PaymentProvider):
    code = PaymentProviderCode.STRIPE
    name = "Stripe"

    def is_configured(self) -> bool:
        return bool(settings.STRIPE_SECRET_KEY and settings.STRIPE_WEBHOOK_SECRET)

    def is_enabled(self) -> bool:
        return self.is_configured() and get_setting("billing.stripe_enabled") is True

    def _post(self, path: str, data: dict[str, Any], *, idempotency_key: str) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {settings.STRIPE_SECRET_KEY}",
            "Idempotency-Key": idempotency_key,
        }
        try:
            with http_client() as client:
                response = client.post(f"{API_BASE}{path}", data=form(data), headers=headers)
        except httpx.HTTPError as exc:
            msg = f"Stripe unreachable: {type(exc).__name__}"
            raise ProviderError(msg) from None
        if response.status_code >= 400:
            raise provider_error(response)
        body: dict[str, Any] = response.json()
        return body

    def create_checkout(
        self, invoice: Invoice, payment: Payment, *, return_url: str
    ) -> CheckoutSession:
        ttl = timedelta(hours=int(get_setting("billing.checkout_ttl_hours")))
        expires = timezone.now() + min(max(ttl, _MIN_EXPIRY), _MAX_EXPIRY)
        line = invoice.lines[0] if invoice.lines else {}
        name = str(line.get(f"description_{invoice.locale}") or line.get("description_en") or "")
        metadata = {"invoice_id": str(invoice.pk), "payment_id": str(payment.pk)}
        data: dict[str, Any] = {
            "mode": "payment",
            "client_reference_id": str(invoice.pk),
            "success_url": with_query(return_url, checkout="success", invoice=str(invoice.pk)),
            "cancel_url": with_query(return_url, checkout="cancelled", invoice=str(invoice.pk)),
            "expires_at": int(expires.timestamp()),
            "locale": "auto",
            "line_items": [
                {
                    "quantity": 1,
                    "price_data": {
                        "currency": invoice.currency.lower(),
                        "unit_amount": invoice.total,
                        "product_data": {"name": name or "Subscription"},
                    },
                }
            ],
            "metadata": metadata,
            "payment_intent_data": {"metadata": metadata},
        }
        if invoice.user.email:
            data["customer_email"] = invoice.user.email
        body = self._post(
            "/checkout/sessions", data, idempotency_key=f"checkout-{payment.idempotency_key}"
        )
        return CheckoutSession(
            provider=self.code, reference=str(body["id"]), redirect_url=str(body["url"])
        )

    def verify_webhook(self, request: HttpRequest) -> ProviderEvent:
        header = request.headers.get("Stripe-Signature", "")
        parts: dict[str, list[str]] = {}
        for item in header.split(","):
            key, _, value = item.strip().partition("=")
            parts.setdefault(key, []).append(value)
        try:
            timestamp = int(parts.get("t", [""])[0])
        except ValueError:
            msg = "The Stripe-Signature header has no timestamp."
            raise WebhookError(msg) from None
        if abs(time.time() - timestamp) > SIGNATURE_TOLERANCE_S:
            msg = "The webhook timestamp is outside the tolerance."
            raise WebhookError(msg)
        expected = sign(request.body, settings.STRIPE_WEBHOOK_SECRET, timestamp)
        if not any(hmac.compare_digest(expected, given) for given in parts.get("v1", [])):
            msg = "The webhook signature does not match."
            raise WebhookError(msg)
        payload = json_body(request)
        event_id, event_type = payload.get("id"), payload.get("type")
        if not isinstance(event_id, str) or not isinstance(event_type, str):
            msg = "The event has no id or type."
            raise WebhookError(msg)
        return ProviderEvent(event_id=event_id, type=event_type, payload=payload)

    def parse_event(self, event: ProviderEvent) -> PaymentResult | None:
        data = event.payload.get("data")
        obj = data.get("object") if isinstance(data, dict) else None
        if not isinstance(obj, dict):
            return None
        if event.type.startswith("checkout.session."):
            return self._session_result(event.type, obj)
        if event.type == "charge.refunded":
            return PaymentResult(
                outcome=Outcome.REFUNDED,
                provider_ref=str(obj.get("payment_intent") or ""),
                refunded_amount=int(obj.get("amount_refunded") or 0),
                currency=str(obj.get("currency") or "").upper(),
                raw=obj,
            )
        return None

    def _session_result(self, event_type: str, obj: dict[str, Any]) -> PaymentResult | None:
        metadata = obj.get("metadata") or {}
        common: dict[str, Any] = {
            "checkout_ref": str(obj.get("id") or ""),
            "provider_ref": str(obj.get("payment_intent") or ""),
            "invoice_id": str(metadata.get("invoice_id") or obj.get("client_reference_id") or ""),
            "amount": obj.get("amount_total"),
            "currency": str(obj.get("currency") or "").upper(),
            "method": PaymentMethod.CARD,
            "raw": obj,
        }
        match event_type:
            case "checkout.session.completed" if obj.get("payment_status") in (
                "paid",
                "no_payment_required",
            ):
                return PaymentResult(outcome=Outcome.SUCCEEDED, **common)
            case "checkout.session.async_payment_succeeded":
                return PaymentResult(outcome=Outcome.SUCCEEDED, **common)
            case "checkout.session.async_payment_failed":
                return PaymentResult(
                    outcome=Outcome.FAILED, failure_reason="The payment failed.", **common
                )
            case "checkout.session.expired":
                return PaymentResult(outcome=Outcome.CANCELLED, **common)
        return None

    def refund(self, payment: Payment, amount: int) -> RefundResult:
        if not payment.provider_ref:
            msg = "The payment has no Stripe PaymentIntent to refund."
            raise ProviderError(msg)
        body = self._post(
            "/refunds",
            {"payment_intent": payment.provider_ref, "amount": amount},
            idempotency_key=f"refund-{payment.pk}-{payment.refunded_amount}-{amount}",
        )
        if body.get("status") in ("failed", "canceled"):
            msg = f"Stripe refused the refund ({body.get('status')})."
            raise ProviderError(msg)
        return RefundResult(
            reference=str(body.get("id") or ""), amount=int(body.get("amount") or amount)
        )
