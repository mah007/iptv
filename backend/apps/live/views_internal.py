"""MediaMTX's sign-ins (`authMethod: http`, ADR-0023): POST /internal/mediamtx-auth.

MediaMTX asks this endpoint about every publish, read and API call, with a JSON body
(`user`, `password`, `ip`, `action`, `path`, `protocol`, ...); a 2xx answer allows it.
It authenticates with HTTP Basic auth, user `mediamtx` and MEDIAMTX_AUTH_TOKEN (the
userinfo of its `authHTTPAddress`); without a configured token the endpoint does not
exist (404).

- `publish`: the ingest publish user (`live.ingest_publish_*`). Publishing from
  MediaMTX's own loopback is allowed: its `runOnInit`/`runOnDemand` hooks.
- `read`, `playback`, `api`: the ingest read user (`live.ingest_read_*`), or the user
  of a MediaMTX integration (Live TV → Integrations), which syncs channels and reads;
  from private addresses only (the Docker networks), as before ADR-0023.
- Anything else (`metrics`, `pprof`) is refused.

No user and no password answers 401: RTSP clients send credentials only once asked.
Wrong ones answer 403 and count against the client's address; past
`live.ingest_auth_max_failures` the address is refused for
`live.ingest_auth_block_minutes`. Credentials are never logged.
"""

import base64
import hmac
import ipaddress
import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Final, cast

import redis
import structlog
from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.core.metrics import LIVE_INGEST_AUTH
from apps.core.services import get_secret, get_setting
from apps.core.stores import state_redis
from apps.live.integrations import credentials
from apps.live.models import IntegrationKind, LiveIntegration

logger = structlog.get_logger(__name__)

MAX_BODY: Final = 16 * 1024
FAIL_PREFIX: Final = "mtx:fail:"
SERVICE_USER: Final = "mediamtx"
READ_ACTIONS: Final = frozenset({"read", "playback", "api"})
KNOWN_ACTIONS: Final = frozenset({"publish", "read", "playback", "api", "metrics", "pprof"})


@dataclass(frozen=True, slots=True)
class Decision:
    status: int
    result: str  # allowed | loopback | challenged | denied | throttled


ALLOWED: Final = Decision(200, "allowed")
LOOPBACK: Final = Decision(200, "loopback")
CHALLENGED: Final = Decision(401, "challenged")
DENIED: Final = Decision(403, "denied")
THROTTLED: Final = Decision(403, "throttled")


def _service_authenticated(request: HttpRequest, token: str) -> bool:
    expected = "Basic " + base64.b64encode(f"{SERVICE_USER}:{token}".encode()).decode()
    supplied = request.META.get("HTTP_AUTHORIZATION", "")
    return hmac.compare_digest(supplied.encode(), expected.encode())


def _loopback(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return False


#: Where readers may come from: the Docker networks (as the internal users allowed).
READER_NETWORKS: Final = tuple(
    ipaddress.ip_network(network)
    for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
)


def _private(ip: str) -> bool:
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return address.is_loopback or any(
        address.version == network.version and address in network for network in READER_NETWORKS
    )


def _same(supplied: str, expected: str) -> bool:
    return hmac.compare_digest(supplied.encode(), expected.encode())


def _pairs(action: str) -> Iterator[tuple[str, str]]:
    """The (user, password) pairs that may perform `action`; unset passwords never match."""
    if action == "publish":
        yield (
            str(get_setting("live.ingest_publish_user")),
            get_secret("live.ingest_publish_password"),
        )
        return
    yield str(get_setting("live.ingest_read_user")), get_secret("live.ingest_read_password")
    for integration in LiveIntegration.objects.filter(kind=IntegrationKind.MEDIAMTX):
        secrets = credentials(integration)
        yield secrets.get("username", ""), secrets.get("password", "")


def _matches(action: str, user: str, password: str) -> bool:
    found = False
    for expected_user, expected_password in _pairs(action):
        if expected_user and expected_password:
            # Every pair is compared: the time doesn't tell which one matched.
            found = (_same(user, expected_user) & _same(password, expected_password)) | found
    return found


def _fail_key(ip: str) -> str:
    return f"{FAIL_PREFIX}{ip}"


def _blocked(ip: str) -> bool:
    try:
        count = cast("bytes | str | None", state_redis().get(_fail_key(ip)))
    except redis.RedisError:
        return False
    return count is not None and int(count) >= int(get_setting("live.ingest_auth_max_failures"))


def _count_failure(ip: str) -> None:
    window = int(get_setting("live.ingest_auth_block_minutes")) * 60
    try:
        pipe = state_redis().pipeline()
        pipe.incr(_fail_key(ip))
        pipe.expire(_fail_key(ip), window, nx=True)
        pipe.execute()
    except redis.RedisError:
        logger.warning("live.ingest_auth_counter_unavailable")


def decide(payload: dict[str, Any]) -> Decision:
    """MediaMTX's question, answered (see the module docstring)."""
    action = str(payload.get("action") or "")
    user = str(payload.get("user") or "")
    password = str(payload.get("password") or "")
    ip = str(payload.get("ip") or "")
    if action == "publish" and _loopback(ip):
        return LOOPBACK
    if action != "publish" and (action not in READ_ACTIONS or not _private(ip)):
        return DENIED  # metrics, pprof, anything unknown; reading from outside
    if _blocked(ip):
        return THROTTLED
    if not user and not password:
        return CHALLENGED
    if _matches(action, user, password):
        return ALLOWED
    _count_failure(ip)
    logger.info("live.ingest_auth_refused", action=action, ip=ip, path=payload.get("path"))
    return DENIED


@csrf_exempt
@never_cache
@require_POST
def mediamtx_auth(request: HttpRequest) -> HttpResponse:
    token = str(getattr(settings, "MEDIAMTX_AUTH_TOKEN", ""))
    if not token:
        return HttpResponse(status=404)
    if not _service_authenticated(request, token):
        return HttpResponse(status=401)
    if len(request.body) > MAX_BODY:
        return HttpResponse(status=413)
    try:
        payload = json.loads(request.body)
    except ValueError:
        return HttpResponse(status=400)
    if not isinstance(payload, dict):
        return HttpResponse(status=400)
    decision = decide(payload)
    action = str(payload.get("action") or "")
    LIVE_INGEST_AUTH.labels(
        action=action if action in KNOWN_ACTIONS else "other", result=decision.result
    ).inc()
    return HttpResponse(status=decision.status)
