"""Alertmanager's webhook (ADR-0018): POST /internal/alerts, internal URLconf only.

Alertmanager authenticates with `Authorization: Bearer <ALERT_WEBHOOK_TOKEN>` (the same
value reaches it as a Docker secret). Without a configured token the endpoint does not
exist. The alerts are delivered at once (apps.notifications.alerts); 503 when every
configured channel failed, so Alertmanager tries again.
"""

import hmac
import json

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.notifications import alerts

MAX_BODY = 1024 * 1024


@csrf_exempt
@never_cache
@require_POST
def alert_webhook(request: HttpRequest) -> HttpResponse:
    token = str(getattr(settings, "ALERT_WEBHOOK_TOKEN", ""))
    if not token:
        return HttpResponse(status=404)
    supplied = request.META.get("HTTP_AUTHORIZATION", "")
    if not hmac.compare_digest(supplied.encode(), f"Bearer {token}".encode()):
        return HttpResponse(status=401)
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
