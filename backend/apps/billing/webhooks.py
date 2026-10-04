"""Provider webhooks: `POST /api/v1/webhooks/{provider}` on api.<domain> (SPEC §7.6).

1. The adapter verifies the signature (Stripe) or secret token (Moyasar);
   anything else gets 400 WEBHOOK_INVALID, and nothing is stored.
2. The event is stored once per (provider, event id) (`WebhookEvent`): a
   delivery of an event already processed is acknowledged and changes nothing.
3. It is applied in one transaction with the payment it names
   (`services.apply_result`) and audited. If that fails, the error is kept on the
   event and the answer is 500, so the provider delivers it again.
"""

import logging
from dataclasses import dataclass

from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone

from apps.audit import services as audit
from apps.billing import services
from apps.billing.models import WebhookEvent
from apps.billing.providers import get_provider
from apps.billing.providers.base import WebhookError
from apps.core.errors import ErrorCode, ProblemError
from apps.core.redaction import redact_text, redact_value

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WebhookOutcome:
    event: WebhookEvent
    duplicate: bool
    error: str = ""


def receive(provider_code: str, request: HttpRequest) -> WebhookOutcome:
    provider = get_provider(provider_code)
    if provider is None or not provider.is_configured():
        raise ProblemError(ErrorCode.NOT_FOUND)
    try:
        event = provider.verify_webhook(request)
    except WebhookError as exc:
        logger.warning("%s webhook refused: %s", provider_code, exc)
        raise ProblemError(ErrorCode.WEBHOOK_INVALID, str(exc)) from None
    with transaction.atomic():
        row, _created = WebhookEvent.objects.select_for_update().get_or_create(
            provider=provider.code,
            event_id=event.event_id,
            defaults={"type": event.type[:100], "payload": redact_value(event.payload)},
        )
        if row.processed_at is not None:
            return WebhookOutcome(event=row, duplicate=True)
        row.attempts += 1
        error = ""
        try:
            with transaction.atomic():
                result = provider.parse_event(event)
                payment = services.apply_result(provider.code, result) if result else None
                row.payment = payment
                row.processed_at = timezone.now()
                row.error = ""
                audit.record(
                    "payment.webhook",
                    actor=None,
                    target=row,
                    after={
                        "provider": provider.code,
                        "type": row.type,
                        "event_id": row.event_id,
                        "payment_id": str(payment.pk) if payment else None,
                        "outcome": result.outcome if result else None,
                    },
                )
        except Exception as exc:
            logger.exception("%s webhook %s failed", provider.code, row.pk)
            error = redact_text(f"{type(exc).__name__}: {exc}")[:1000]
            row.error = error
        row.save()
    return WebhookOutcome(event=row, duplicate=False, error=error)
