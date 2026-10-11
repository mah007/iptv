"""An edge's Test (ADR-0022): the whole media path, end to end.

1. `GET <edge>/healthz`: the edge answers and holds a usable key set.
2. A signed URL for the probe object (`edgeprobe/probe.txt`), whose synthetic session
   stream-auth allows for a minute (`edges:probe:<session>`): the token, stream-auth over
   the edge's channel (TLS and its bearer for a standalone edge), and the storage
   (the media volume, a mirror, or the S3 origin through the slice cache with the
   edges' read-only credentials). The body must be the probe's exact bytes.
3. For a standalone edge: its last report is recent and holds the signing key.

The probe object is written to the media volume by the worker's local probe, and to the
bucket by the publisher. Nothing a test sends or receives is logged or returned, apart
from statuses and a short reason.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Final
from urllib.parse import urlsplit

import httpx
from django.conf import settings

from apps.core.stores import state_redis
from apps.edges import services
from apps.edges.models import Edge, EdgeKind, EdgeStorage
from apps.edges.selection import EdgeView
from apps.live import egress
from apps.playback import tokens
from apps.playback.views import PROBE_PREFIX

PROBE_TITLE: Final = "edgeprobe"
PROBE_RENDITION: Final = "probe"
PROBE_FILE: Final = "probe.txt"
PROBE_BODY: Final = b"smart-iptv edge probe\n"
TIMEOUT_S: Final = 10.0
_transport: httpx.BaseTransport | None = None


@dataclass(frozen=True, slots=True)
class Step:
    name: str
    ok: bool
    code: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ProbeResult:
    ok: bool
    steps: list[Step] = field(default_factory=list)


def use_transport(transport: httpx.BaseTransport | None) -> None:
    """Tests: send the probe's requests through this transport."""
    global _transport  # noqa: PLW0603
    _transport = transport


def _client(edge: Edge) -> httpx.Client:
    options: dict[str, object] = {"timeout": TIMEOUT_S, "follow_redirects": False}
    if _transport is not None:
        options["transport"] = _transport
        return httpx.Client(**options)  # type: ignore[arg-type]
    if edge.kind == EdgeKind.LOCAL:
        return httpx.Client(**options)  # type: ignore[arg-type]
    host = urlsplit(edge.base_url).hostname or ""
    allowed = (host,) if settings.DEBUG else ()
    return egress.guarded_client(allowed=allowed, timeout=TIMEOUT_S, follow_redirects=False)


def _base(edge: Edge) -> str:
    if edge.kind == EdgeKind.LOCAL:
        return settings.EDGE_LOCAL_HEALTH_URL.removesuffix("/healthz")
    return edge.base_url.rstrip("/")


def _probe_url(edge: Edge, keyset: tokens.KeySet, session: str, exp: int) -> str:
    token = tokens.sign(
        keyset, session=session, title=PROBE_TITLE, rendition=PROBE_RENDITION, exp=exp
    )
    if edge.kind != EdgeKind.CDN:
        return f"{_base(edge)}/v/{token}/{PROBE_FILE}"
    target = services.Target(
        EdgeView(edge.edge_id, edge.kind, edge.storage, edge.base_url),
        edge.base_url.rstrip("/"),
        PROBE_TITLE,
    )
    return services.media_url(
        target, token=token, rendition=PROBE_RENDITION, entry=PROBE_FILE, exp=exp
    )


def _fetch(client: httpx.Client, name: str, url: str, expect: bytes | None) -> Step:
    try:
        response = client.get(url)
    except egress.UnsafeDestination as exc:
        return Step(name, False, "refused_destination", str(exc))
    except httpx.TimeoutException:
        return Step(name, False, "timeout")
    except httpx.HTTPError as exc:
        return Step(name, False, "unreachable", type(exc).__name__)
    if response.status_code != 200:
        reason = response.headers.get("X-Reason", "")[:32]
        return Step(name, False, f"http_{response.status_code}", reason)
    if expect is not None and response.content != expect:
        return Step(name, False, "wrong_content")
    return Step(name, True, "ok")


def run(edge: Edge) -> ProbeResult:
    steps: list[Step] = []
    if edge.kind == EdgeKind.CDN and edge.origin_edge is None:
        return ProbeResult(False, [Step("setup", False, "no_origin_edge")])
    if edge.kind != EdgeKind.LOCAL and not edge.base_url:
        return ProbeResult(False, [Step("setup", False, "no_address")])
    try:
        keyset = tokens.keyring()
    except tokens.KeySetError:
        return ProbeResult(False, [Step("setup", False, "keys_unusable")])
    session = secrets.token_hex(16)
    exp = int(time.time()) + 300
    state_redis().set(PROBE_PREFIX + session, "1", ex=60)
    try:
        with _client(edge) as client:
            if edge.kind != EdgeKind.CDN:
                steps.append(_fetch(client, "health", f"{_base(edge)}/healthz", None))
            steps.append(
                _fetch(client, "media", _probe_url(edge, keyset, session, exp), PROBE_BODY)
            )
    finally:
        state_redis().delete(PROBE_PREFIX + session)
    if edge.kind == EdgeKind.STANDALONE:
        status = services.status_of(edge, signing_kid=keyset.current.kid)
        steps.append(Step("report", status == services.Status.HEALTHY, status))
    if edge.kind == EdgeKind.STANDALONE and edge.storage == EdgeStorage.ORIGIN:
        configured = services.origin_document() is not None
        steps.append(
            Step("origin_credentials", configured, "ok" if configured else "not_configured")
        )
    return ProbeResult(all(step.ok for step in steps), steps)
