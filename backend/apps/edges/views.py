"""The edges host, `edges.<domain>` (ADR-0022): what standalone edges call over TLS.

- `POST /edge/v1/poll`: the control channel. The edge reports its loaded key ids, its
  connections and its byte counter; it gets the media token keys, the S3 origin's
  read-only credentials and the CDN origin secret. Postgres (it records the report).
- `GET /edge/v1/stream-auth`: stream-auth for that edge. Redis only, like the internal
  one: the bearer secret's hash comes from `edges:auth` in redis-state. The edge sends
  the client address as `X-Edge-Client-IP`, because Traefik rewrites `X-Real-IP`.
- `GET /edge/v1/ping`: 204 for a valid enabled edge (the installer's and Test's check).

Every request carries `Authorization: Bearer <secret>` and `X-Edge-Id`. Unknown,
disabled and wrong secrets get the same answer: 401 (403 with `X-Reason` from
stream-auth, which an edge would otherwise pass to the player as a 401). Nothing here is
cached, logged with its secret, or reachable through any other host.
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import re
from typing import Any, cast

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_safe
from redis.exceptions import RedisError

from apps.edges import services
from apps.edges.models import Edge, EdgeKind
from apps.playback import concurrency, tokens
from apps.playback import views as playback_views

_EDGE_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")
_MAX_BODY = 64 * 1024


def _no_store(response: HttpResponse) -> HttpResponse:
    response["Cache-Control"] = "no-store"
    return response


def _unauthorized() -> HttpResponse:
    response = HttpResponse(status=401)
    response["WWW-Authenticate"] = 'Bearer realm="edges"'
    return _no_store(response)


def _edge_id(request: HttpRequest) -> str:
    value = request.headers.get("X-Edge-Id", "")
    return value if _EDGE_ID.fullmatch(value) else ""


def authenticate(request: HttpRequest) -> Edge | None:
    """The enabled standalone edge this request comes from, or None (Postgres)."""
    edge_id = _edge_id(request)
    secret = services.bearer(request.headers.get("Authorization", ""))
    if not edge_id or not secret:
        return None
    edge = Edge.objects.filter(edge_id=edge_id, kind=EdgeKind.STANDALONE, enabled=True).first()
    if edge is None or not services.secret_matches(secret, edge.secret_hash):
        return None
    return edge


@csrf_exempt
@require_POST
def poll(request: HttpRequest) -> HttpResponse:
    edge = authenticate(request)
    if edge is None:
        return _unauthorized()
    if len(request.body) > _MAX_BODY:
        return _no_store(HttpResponse(status=413))
    try:
        body: Any = json.loads(request.body or b"{}")
    except ValueError:
        body = {}
    services.record_report(edge, body)
    try:
        answer = services.poll_answer(edge)
    except tokens.KeySetError:
        return _no_store(HttpResponse(status=503))
    return _no_store(JsonResponse(answer))


@require_safe
def ping(request: HttpRequest) -> HttpResponse:
    if authenticate(request) is None:
        return _unauthorized()
    return _no_store(HttpResponse(status=204))


def _client_ip(request: HttpRequest) -> str:
    value = request.headers.get("X-Edge-Client-IP", "").strip()
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return ""


async def _known(edge_id: str, secret: str) -> bool:
    client, _script = concurrency.async_state()
    stored = cast("bytes | None", await cast("Any", client.hget(services.AUTH_KEY, edge_id)))
    if not stored:
        return False
    return hmac.compare_digest(services.hash_secret(secret).encode(), stored)


@require_safe
async def stream_auth(request: HttpRequest) -> HttpResponse:
    edge_id = _edge_id(request)
    secret = services.bearer(request.headers.get("Authorization", ""))
    try:
        known = bool(edge_id and secret) and await _known(edge_id, secret)
    except RedisError:
        return _no_store(HttpResponse(status=503))
    if not known:
        response = HttpResponse(status=403)
        response["X-Reason"] = "edge_unauthorized"
        return _no_store(response)
    return await playback_views.authorize(request, client_ip=_client_ip(request), edge=edge_id)
