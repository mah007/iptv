"""The backup agent's API (ADR-0020), internal URLconf only (never routed by Traefik).

- POST /internal/backups/agent/poll       status in; configuration and the next job out
- POST /internal/backups/agent/jobs/<id>  a job's result

The agent authenticates with `Authorization: Bearer <BACKUP_AGENT_TOKEN>` (it reads
the same value from a Docker secret). Without a configured token the endpoints do not
exist. The answers hold the S3 credentials, so they are never cached.
"""

import hmac
import json
from typing import Any
from uuid import UUID

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.ops import backups
from apps.ops.models import BackupRun

MAX_BODY = 512 * 1024


def _authorized(request: HttpRequest) -> HttpResponse | None:
    token = str(getattr(settings, "BACKUP_AGENT_TOKEN", ""))
    if not token:
        return HttpResponse(status=404)
    supplied = request.META.get("HTTP_AUTHORIZATION", "")
    if not hmac.compare_digest(supplied.encode(), f"Bearer {token}".encode()):
        return HttpResponse(status=401)
    if len(request.body) > MAX_BODY:
        return HttpResponse(status=413)
    return None


def _body(request: HttpRequest) -> dict[str, Any] | None:
    try:
        payload = json.loads(request.body or b"{}")
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


@csrf_exempt
@never_cache
@require_POST
def agent_poll(request: HttpRequest) -> HttpResponse:
    if (refused := _authorized(request)) is not None:
        return refused
    payload = _body(request)
    if payload is None:
        return HttpResponse(status=400)
    status = payload.get("status")
    backups.record_agent_status(status if isinstance(status, dict) else {})
    answer: dict[str, Any] = {"config": backups.agent_config(), "job": None}
    run = backups.claim_next()
    if run is not None:
        answer["job"] = backups.job_payload(run)
    return JsonResponse(answer)


@csrf_exempt
@never_cache
@require_POST
def agent_job(request: HttpRequest, run_id: UUID) -> HttpResponse:
    if (refused := _authorized(request)) is not None:
        return refused
    payload = _body(request)
    if payload is None:
        return HttpResponse(status=400)
    run = BackupRun.objects.filter(pk=run_id).first()
    if run is None:
        return HttpResponse(status=404)
    run = backups.record_result(run, payload)
    return JsonResponse({"status": run.status})
