"""Moyasar adapter (KSA: mada, Apple Pay, STC Pay, cards), ADR-0012.

Checkout is a Moyasar invoice (`POST /v1/invoices`): a hosted payment page the
customer is sent to. Requests use HTTP Basic authentication with the secret key
(`sk_test_...` for the sandbox) as the user name.

Webhooks carry the endpoint's `secret_token` in the JSON body, compared in
constant time with MOYASAR_WEBHOOK_SECRET. Events used: `payment_paid`,
`payment_failed` (Moyasar's reference also spells it `payment_faild`),
`payment_refunded` and `payment_voided`; `data` is the payment, whose
`invoice_id` is our checkout reference.
"""

import hmac
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

API_BASE: Final = "https://api.moyasar.com/v1"
#: Moyasar's smallest invoice: 1.00 in the currency's minor units.
MIN_AMOUNT: Final = 100

_METHODS: Final = {
    "applepay": PaymentMethod.APPLE_PAY,
    "stcpay": PaymentMethod.STC_PAY,
    "creditcard": PaymentMethod.CARD,
}


def payment_method(payment: dict[str, Any]) -> str:
    source = payment.get("source") or {}
    if not isinstance(source, dict):
        return PaymentMethod.OTHER
    if str(source.get("company") or "").lower() == "mada":
        return PaymentMethod.MADA
    return _METHODS.get(str(source.get("type") or "").lower(), PaymentMethod.OTHER)


class MoyasarProvider(PaymentProvider):
    code = PaymentProviderCode.MOYASAR
    name = "Moyasar"

    def is_configured(self) -> bool:
        return bool(settings.MOYASAR_SECRET_KEY and settings.MOYASAR_WEBHOOK_SECRET)

    def is_enabled(self) -> bool:
        return self.is_configured() and get_setting("billing.moyasar_enabled") is True

    def _post(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        try:
            with http_client(auth=(settings.MOYASAR_SECRET_KEY, "")) as client:
                response = client.post(f"{API_BASE}{path}", json=data)
        except httpx.HTTPError as exc:
            msg = f"Moyasar unreachable: {type(exc).__name__}"
            raise ProviderError(msg) from None
        if response.status_code >= 400:
            raise provider_error(response)
        body: dict[str, Any] = response.json()
        return body

    def create_checkout(
        self, invoice: Invoice, payment: Payment, *, return_url: str
    ) -> CheckoutSession:
        if invoice.total < MIN_AMOUNT:
            msg = "Moyasar needs an amount of at least 1.00."
            raise ProviderError(msg)
        line = invoice.lines[0] if invoice.lines else {}
        description = str(
            line.get(f"description_{invoice.locale}") or line.get("description_en") or ""
        )
        ttl = timedelta(hours=int(get_setting("billing.checkout_ttl_hours")))
        body = self._post(
            "/invoices",
            {
                "amount": invoice.total,
                "currency": invoice.currency,
                "description": description or "Subscription",
                "success_url": with_query(return_url, checkout="success", invoice=str(invoice.pk)),
                "back_url": with_query(return_url, checkout="cancelled", invoice=str(invoice.pk)),
                "expired_at": (timezone.now() + ttl).isoformat(),
                "metadata": {"invoice_id": str(invoice.pk), "payment_id": str(payment.pk)},
            },
        )
        return CheckoutSession(
            provider=self.code, reference=str(body["id"]), redirect_url=str(body["url"])
        )

    def verify_webhook(self, request: HttpRequest) -> ProviderEvent:
        payload = json_body(request)
        token = payload.get("secret_token")
        if not isinstance(token, str) or not hmac.compare_digest(
            token.encode(), settings.MOYASAR_WEBHOOK_SECRET.encode()
        ):
            msg = "The webhook secret token does not match."
            raise WebhookError(msg)
        event_id, event_type = payload.get("id"), payload.get("type")
        if not isinstance(event_id, str) or not isinstance(event_type, str):
            msg = "The event has no id or type."
            raise WebhookError(msg)
        return ProviderEvent(event_id=event_id, type=event_type, payload=payload)

    def parse_event(self, event: ProviderEvent) -> PaymentResult | None:
        data = event.payload.get("data")
        if not isinstance(data, dict):
            return None
        metadata = data.get("metadata") or {}
        common: dict[str, Any] = {
            "checkout_ref": str(data.get("invoice_id") or ""),
            "provider_ref": str(data.get("id") or ""),
            "invoice_id": str(metadata.get("invoice_id") or "")
            if isinstance(metadata, dict)
            else "",
            "amount": data.get("amount"),
            "currency": str(data.get("currency") or "").upper(),
            "method": payment_method(data),
            "raw": data,
        }
        match event.type:
            case "payment_paid":
                return PaymentResult(outcome=Outcome.SUCCEEDED, **common)
            case "payment_failed" | "payment_faild":
                source = data.get("source") or {}
                reason = str(source.get("message") or "") if isinstance(source, dict) else ""
                return PaymentResult(
                    outcome=Outcome.FAILED,
                    failure_reason=reason or "The payment failed.",
                    **common,
                )
            case "payment_refunded":
                return PaymentResult(
                    outcome=Outcome.REFUNDED,
                    refunded_amount=int(data.get("refunded") or 0),
                    **common,
                )
            case "payment_voided":
                return PaymentResult(outcome=Outcome.CANCELLED, **common)
        return None

    def refund(self, payment: Payment, amount: int) -> RefundResult:
        if not payment.provider_ref:
            msg = "The payment has no Moyasar payment to refund."
            raise ProviderError(msg)
        body = self._post(f"/payments/{payment.provider_ref}/refund", {"amount": amount})
        refunded = int(body.get("refunded") or 0)
        if refunded < payment.refunded_amount + amount:
            msg = "Moyasar did not confirm the refund."
            raise ProviderError(msg)
        return RefundResult(reference=str(body.get("id") or ""), amount=amount)
