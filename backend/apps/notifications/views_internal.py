"""Alertmanager's webhook (ADR-0018): POST /internal/alerts, internal URLconf only, and
its fallback channels' settings (ADR-0019): GET /internal/alert-fallback.

Alertmanager authenticates with `Authorization: Bearer <ALERT_WEBHOOK_TOKEN>` (the same
value reaches it as a Docker secret). Without a configured token the endpoint does not
exist. The alerts are delivered at once (apps.notifications.alerts); 503 when every
configured channel failed, so Alertmanager tries again.
"""

import base64
import hmac
import json

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from apps.notifications import alerts

MAX_BODY = 1024 * 1024


def _refused(request: HttpRequest) -> HttpResponse | None:
    """404 without a configured token, 401 without the right bearer token."""
    token = str(getattr(settings, "ALERT_WEBHOOK_TOKEN", ""))
    if not token:
        return HttpResponse(status=404)
    supplied = request.META.get("HTTP_AUTHORIZATION", "")
    if not hmac.compare_digest(supplied.encode(), f"Bearer {token}".encode()):
        return HttpResponse(status=401)
    return None


@csrf_exempt
@never_cache
@require_POST
def alert_webhook(request: HttpRequest) -> HttpResponse:
    if (refused := _refused(request)) is not None:
        return refused
    if len(request.body) > MAX_BODY:
        return HttpResponse(status=413)
    try:
        payload = json.loads(request.body)
    except ValueError:
        return HttpResponse(status=400)
    if not isinstance(payload, dict):
        return HttpResponse(status=400)
    outcome = alerts.deliver(payload)
    return JsonResponse(outcome.channels, status=503 if outcome.failed else 200)


@never_cache
@require_GET
def alert_fallback(request: HttpRequest) -> HttpResponse:
    """The fallback receiver's settings for Alertmanager's entrypoint, one per line as
    `name<TAB>base64(value)` (a busybox shell reads it without a JSON parser). It
    carries the SMTP password and the bot token: internal network and bearer token only,
    never cached, never logged."""
    if (refused := _refused(request)) is not None:
        return refused
    values = alerts.fallback_config()
    body = "".join(
        f"{name}\t{base64.b64encode(values[name].encode()).decode()}\n"
        for name in alerts.FALLBACK_FIELDS
    )
    return HttpResponse(body, content_type="text/plain; charset=utf-8")
