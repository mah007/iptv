"""Manual payments: bank transfer or cash, confirmed by an admin (ADR-0012).

Checkout answers with the payment instructions (`billing.manual_instructions_*`)
and the reference to quote; an admin records the payment when the money arrives
(`services.record_manual_payment`). There are no webhooks, and refunds are paid
back outside the system and only recorded here.
"""

from django.http import HttpRequest

from apps.billing.models import Invoice, Payment, PaymentProviderCode
from apps.billing.providers.base import (
    CheckoutSession,
    PaymentProvider,
    PaymentResult,
    ProviderEvent,
    RefundResult,
    WebhookError,
)
from apps.core.services import get_setting


def payment_reference(invoice: Invoice) -> str:
    """What the customer quotes with a transfer: short, unambiguous, unique enough."""
    return f"P-{invoice.pk.hex[-8:].upper()}"


class ManualProvider(PaymentProvider):
    code = PaymentProviderCode.MANUAL
    name = "Bank transfer or cash"

    def is_configured(self) -> bool:
        return True

    def is_enabled(self) -> bool:
        return True

    def create_checkout(
        self, invoice: Invoice, payment: Payment, *, return_url: str
    ) -> CheckoutSession:
        return CheckoutSession(
            provider=self.code,
            reference=payment_reference(invoice),
            instructions={
                "en": str(get_setting("billing.manual_instructions_en")),
                "ar": str(get_setting("billing.manual_instructions_ar")),
            },
        )

    def verify_webhook(self, request: HttpRequest) -> ProviderEvent:
        msg = "Manual payments have no webhooks."
        raise WebhookError(msg)

    def parse_event(self, event: ProviderEvent) -> PaymentResult | None:
        return None

    def refund(self, payment: Payment, amount: int) -> RefundResult:
        return RefundResult(reference="", amount=amount)
