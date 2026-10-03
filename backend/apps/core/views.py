from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from apps.core.health import run_checks


@require_GET
def service_info(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"service": "smart-iptv", "api": "/api/v1"})


@never_cache
@require_GET
def public_health(request: HttpRequest) -> JsonResponse:
    """Public liveness. It deliberately reveals nothing about internals."""
    return JsonResponse({"status": "ok"})


@never_cache
@require_GET
def live(request: HttpRequest) -> JsonResponse:
    """Internal liveness for container healthchecks: the process is serving."""
    return JsonResponse({"status": "ok"})


@never_cache
@require_GET
def ready(request: HttpRequest) -> JsonResponse:
    """Internal readiness: every dependency answers. 503 while any is down."""
    results = run_checks()
    healthy = all(result.ok for result in results.values())
    checks = {
        name: {
            "ok": result.ok,
            "latency_ms": round(result.latency_ms, 1),
            **({"error": result.error} if result.error else {}),
        }
        for name, result in results.items()
    }
    payload = {
        "status": "ok" if healthy else "unavailable",
        "version": settings.APP_VERSION,
        "checks": checks,
    }
    return JsonResponse(payload, status=200 if healthy else 503)
