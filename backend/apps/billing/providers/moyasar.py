"""Moyasar adapter (KSA: mada, Apple Pay, STC Pay, cards), ADR-0012.

Checkout is a Moyasar invoice (`POST /v1/invoices`): a hosted payment page the
customer is sent to. Requests use HTTP Basic authentication with the secret key
(`sk_test_...` for the sandbox) as the user name.

Webhooks carry the endpoint's `secret_token` in the JSON body, compared in
constant time with the `billing.moyasar_webhook_secret` setting. Events used: `payment_paid`,
`payment_failed` (Moyasar's reference also spells it `payment_faild`),
`payment_refunded` and `payment_voided`; `data` is the payment, whose
`invoice_id` is our checkout reference.
"""

import hmac
from dataclasses import replace
from datetime import timedelta
from typing import Any, Final

import httpx
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
from apps.core.integration import TestCode, TestResult, from_http_error, from_status
from apps.core.services import get_secret, get_setting

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


def _secret_key() -> str:
    return get_secret("billing.moyasar_secret_key")


class MoyasarProvider(PaymentProvider):
    code = PaymentProviderCode.MOYASAR
    name = "Moyasar"

    def is_configured(self) -> bool:
        return bool(_secret_key() and get_secret("billing.moyasar_webhook_secret"))

    def is_enabled(self) -> bool:
        return self.is_configured() and get_setting("billing.moyasar_enabled") is True

    def check(self, webhook_url: str) -> TestResult:
        """The admin's Test: is the key accepted, is our webhook registered?"""
        key = _secret_key()
        if not key:
            return TestResult.failed(TestCode.NOT_CONFIGURED, "No Moyasar secret key is set.")
        try:
            with http_client(auth=(key, "")) as client:
                payments = client.get(f"{API_BASE}/payments", params={"page": 1})
                if payments.status_code >= 400:
                    return from_status("Moyasar", payments)
                hooks = client.get(f"{API_BASE}/webhooks")
        except httpx.HTTPError as exc:
            return from_http_error("Moyasar", exc)
        registered = "unknown"
        if hooks.status_code == httpx.codes.OK:
            try:
                body = hooks.json()
            except ValueError:
                body = None
            if not isinstance(body, dict):
                body = {}
            items = body.get("webhooks") or body.get("data") or []
            registered = (
                "yes"
                if any(isinstance(item, dict) and item.get("url") == webhook_url for item in items)
                else "no"
            )
        mode = "live" if key.startswith("sk_live_") else "test"
        return TestResult.passed(
            f"Moyasar accepted the key ({mode} mode).",
            mode=mode,
            webhook_url=webhook_url,
            webhook_registered=registered,
            webhook_secret_set=bool(get_secret("billing.moyasar_webhook_secret")),
        )

    def _post(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        try:
            with http_client(auth=(_secret_key(), "")) as client:
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
            token.encode(), get_secret("billing.moyasar_webhook_secret").encode()
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

    def confirm(self, result: PaymentResult) -> PaymentResult:
        """The payment as Moyasar has it (G-29): its webhook proves itself only with a
        shared token in the body, so its word alone never activates anything."""
        if not result.provider_ref:
            return replace(result, outcome=Outcome.FAILED, failure_reason="No payment to check.")
        payment = self._get(f"/payments/{result.provider_ref}")
        paid = payment.get("status") == "paid"
        metadata = payment.get("metadata") or {}
        invoice_id = str(metadata.get("invoice_id") or "") if isinstance(metadata, dict) else ""
        return replace(
            result,
            outcome=Outcome.SUCCEEDED if paid else Outcome.FAILED,
            amount=payment.get("amount"),
            currency=str(payment.get("currency") or "").upper(),
            invoice_id=invoice_id or result.invoice_id,
            failure_reason="" if paid else "Moyasar does not confirm the payment.",
        )

    def _get(self, path: str) -> dict[str, Any]:
        try:
            with http_client(auth=(_secret_key(), "")) as client:
                response = client.get(f"{API_BASE}{path}")
        except httpx.HTTPError as exc:
            msg = f"Moyasar unreachable: {type(exc).__name__}"
            raise ProviderError(msg) from None
        if response.status_code >= 400:
            raise provider_error(response)
        body: dict[str, Any] = response.json()
        return body

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
