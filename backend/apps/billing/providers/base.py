"""The payment provider interface (SPEC §7.6, ADR-0012).

Every adapter implements:
- `create_checkout(invoice, payment, return_url)` -> `CheckoutSession`: where the
  customer pays (a hosted page), or manual payment instructions;
- `verify_webhook(request)` -> `ProviderEvent`: the signature-checked event, or
  `WebhookError`;
- `parse_event(event)` -> `PaymentResult | None`: what the event means for one of
  our payments (None: nothing to do);
- `refund(payment, amount)` -> `RefundResult`;
- `cancel_recurring(subscription)`: checkouts are one-off payments (renewals are
  new checkouts), so no adapter holds a recurring agreement to cancel.

Adapters talk HTTP through `http_client()`; tests install a recorded transport
with `use_transport`, so they never reach the network.
"""

import json
from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final
from urllib.parse import urlencode

import httpx
from django.http import HttpRequest

from apps.billing.models import Invoice, Payment, Subscription

HTTP_TIMEOUT_S: Final = 15.0
_transport: httpx.BaseTransport | None = None


class WebhookError(Exception):
    """A webhook that is not authentic or cannot be read; answered with 400."""


class ProviderError(Exception):
    """The provider refused a request or could not be reached."""


@contextmanager
def use_transport(transport: httpx.BaseTransport | None) -> Iterator[None]:
    """Route provider HTTP calls through `transport` (tests: recorded responses)."""
    global _transport  # noqa: PLW0603 (process-wide test hook)
    previous, _transport = _transport, transport
    try:
        yield
    finally:
        _transport = previous


def http_client(**kwargs: Any) -> httpx.Client:
    return httpx.Client(timeout=HTTP_TIMEOUT_S, transport=_transport, **kwargs)


@dataclass(frozen=True, slots=True)
class CheckoutSession:
    provider: str
    # The provider's checkout (Stripe Checkout Session, Moyasar invoice); "" for manual.
    reference: str
    redirect_url: str | None = None
    # Manual payments: how to pay, per locale.
    instructions: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderEvent:
    event_id: str
    type: str
    payload: dict[str, Any]


class Outcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


@dataclass(frozen=True, slots=True)
class PaymentResult:
    outcome: Outcome
    checkout_ref: str = ""
    provider_ref: str = ""
    # Our invoice id, when the provider echoes our metadata.
    invoice_id: str = ""
    amount: int | None = None
    currency: str = ""
    method: str = ""
    failure_reason: str = ""
    refunded_amount: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RefundResult:
    reference: str
    amount: int


class PaymentProvider(ABC):
    code: str
    name: str

    @abstractmethod
    def is_configured(self) -> bool:
        """The keys it needs are set."""

    @abstractmethod
    def is_enabled(self) -> bool:
        """Configured and switched on in the settings: offered at checkout."""

    @abstractmethod
    def create_checkout(
        self, invoice: Invoice, payment: Payment, *, return_url: str
    ) -> CheckoutSession: ...

    @abstractmethod
    def verify_webhook(self, request: HttpRequest) -> ProviderEvent: ...

    @abstractmethod
    def parse_event(self, event: ProviderEvent) -> PaymentResult | None: ...

    @abstractmethod
    def refund(self, payment: Payment, amount: int) -> RefundResult: ...

    def cancel_recurring(self, subscription: Subscription) -> None:
        """Nothing to cancel: every checkout is a one-off payment."""
        return


def json_body(request: HttpRequest) -> dict[str, Any]:
    try:
        payload = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        msg = "The body is not JSON."
        raise WebhookError(msg) from None
    if not isinstance(payload, dict):
        msg = "The body is not a JSON object."
        raise WebhookError(msg)
    return payload


def with_query(url: str, **params: str) -> str:
    """`url` with extra query parameters."""
    separator = "&" if "?" in url else "?"
    return url + separator + urlencode(params)


def provider_error(response: httpx.Response) -> ProviderError:
    """A ProviderError naming the status and the provider's own message, never our keys."""
    message = ""
    try:
        body = response.json()
        error = body.get("error") if isinstance(body, dict) else None
        if isinstance(error, dict):
            message = str(error.get("message") or error.get("type") or "")
        elif isinstance(body, dict):
            message = str(body.get("message") or body.get("type") or "")
    except ValueError:
        message = ""
    return ProviderError(f"HTTP {response.status_code}: {message}"[:300])
