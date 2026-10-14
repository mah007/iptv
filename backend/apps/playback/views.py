"""`GET /internal/stream-auth`: the edge's heartbeat and kick check (ADR-0007 §4).

The edge calls it, at most once a minute per token, for requests whose token it
has already verified; the reply decides whether bytes flow:

- 204: allowed. The session's slot and record are refreshed (`concurrency.heartbeat`).
- 403 with `X-Reason` (`[a-z_]{1,32}`): refused, e.g. `kicked`, `stream_limit`,
  `replaced`, `session_ended`, `access_ended`, or a token verdict (`expired`,
  `ip_mismatch`, `invalid`). The edge shows the reason to the player.
- 503: Redis or the key file is unusable; the edge fails closed.

Redis only: this view never touches Postgres (a test proves it), never logs the
token and only ever names a session by its 16-hex prefix. It is routed on the
internal URLconf alone, so Traefik never exposes it.
"""

import ipaddress
import re
import time

import structlog
from django.http import HttpRequest, HttpResponse
from django.views.decorators.http import require_safe
from redis.exceptions import RedisError

from apps.antisharing import streams
from apps.playback import concurrency, edge_auth, tokens
from apps.playback.tokens import Verdict

logger = structlog.get_logger(__name__)

# What players are told about a token the backend refuses (ADR-0007 §2).
_PUBLIC_VERDICTS = {Verdict.EXPIRED: "expired", Verdict.IP_MISMATCH: "ip_mismatch"}
_EDGE_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")
_MAX_BYTES = 1 << 53


def _clock() -> float:
    return time.time()


def _deny(reason: str) -> HttpResponse:
    response = HttpResponse(status=403)
    response["X-Reason"] = reason
    response["Cache-Control"] = "no-store"
    return response


def _unavailable() -> HttpResponse:
    response = HttpResponse(status=503)
    response["Cache-Control"] = "no-store"
    return response


def split_original_uri(uri: str) -> tuple[str, str] | None:
    """(token, tail) from the edge's X-Original-URI `/v/<token>/<tail>[?query]`."""
    path = uri.split("?", 1)[0]
    if not path.startswith("/v/"):
        return None
    token, slash, tail = path[3:].partition("/")
    if not token or not slash:
        return None
    return token, tail


def _client_ip(request: HttpRequest) -> str:
    value = request.headers.get("X-Real-IP", "").strip()
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return ""


def _edge_id(request: HttpRequest) -> str:
    value = request.headers.get("X-Edge-Id", "")
    return value if _EDGE_ID.fullmatch(value) else ""


def _bytes(request: HttpRequest) -> int:
    """X-Bytes, if an edge ever sends it (ADR-0007: today the access log has them)."""
    value = request.headers.get("X-Bytes", "")
    if not value.isdigit():
        return 0
    return min(int(value), _MAX_BYTES)


#: An edge Test (apps.edges.probe) marks its synthetic session here for a minute, so the
#: probe URL passes stream-auth without a playback session, slot or record.
PROBE_PREFIX = "edges:probe:"


async def _is_probe(session: str) -> bool:
    client, _script = concurrency.async_state()
    return bool(await client.exists(PROBE_PREFIX + session))


async def authorize(  # noqa: PLR0911 (one answer per verdict)
    request: HttpRequest, *, client_ip: str, edge: str
) -> HttpResponse:
    """stream-auth's decision for the token in X-Original-URI (shared by the internal
    endpoint and the edges host's, which have already established who `edge` is)."""
    target = split_original_uri(request.headers.get("X-Original-URI", ""))
    if target is None:
        return _deny("invalid")
    token, tail = target
    try:
        keyset = tokens.keyring()
    except tokens.KeySetError as exc:
        logger.error("stream_auth.keys_unusable", error=str(exc))
        return _unavailable()
    now = _clock()
    try:
        claims = tokens.verify(keyset, token, now=int(now), client_ip=client_ip or None, tail=tail)
    except tokens.TokenError as exc:
        logger.info("stream_auth.token_refused", verdict=exc.reason.value)
        return _deny(_PUBLIC_VERDICTS.get(exc.reason, "invalid"))
    try:
        result = await concurrency.heartbeat(
            claims.session,
            now=now,
            ip=client_ip,
            edge=edge,
            bytes_sent=_bytes(request),
        )
        if (
            not result.allowed
            and result.reason == "session_ended"
            and await _is_probe(claims.session)
        ):
            return HttpResponse(status=204)
    except RedisError:
        logger.exception("stream_auth.redis_unavailable")
        return _unavailable()
    if not result.allowed:
        logger.info(
            "stream_auth.refused",
            reason=result.reason,
            session=claims.session[: tokens.SESSION_LOG_PREFIX],
        )
        return _deny(result.reason)
    # The networks a session's segments come from: an anti-sharing signal (G-18).
    await streams.note_network(claims.session, client_ip)
    return HttpResponse(status=204)


@require_safe
async def stream_auth(request: HttpRequest) -> HttpResponse:
    """The internal endpoint: the local edge and the live relay, on the internal network.

    Callers prove they are one of them with the shared `X-Edge-Auth` secret (G-16)."""
    if not edge_auth.authenticated(request):
        logger.warning("stream_auth.caller_refused")
        return _deny("invalid")
    return await authorize(request, client_ip=_client_ip(request), edge=_edge_id(request))
