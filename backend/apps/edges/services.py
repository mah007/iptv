"""Edges: secrets, the snapshot playback reads, reports and status (ADR-0022).

- **Secrets.** A standalone edge authenticates to `edges.<domain>` with a bearer secret
  shown once. Postgres keeps its SHA-256; `edges:auth` in redis-state mirrors the hashes
  of enabled edges, so the public stream-auth stays Redis-only.
- **Snapshot.** `snapshot()` is what `start_playback` reads: every edge as an `EdgeView`,
  built from Postgres into redis-cache (`edges:snapshot:v1`) and memoised in-process for
  a couple of seconds. Writers call `changed()` after their commit.
- **Reports.** A standalone edge polls every 15 s (`record_report`); the local edge is
  probed by the worker (`probe_local`). Both store the facts in `Edge.report`, and the
  egress rate comes from successive byte counters.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, cast

import httpx
import redis
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.core.services import get_secret, get_setting
from apps.core.stores import cache_redis, state_redis
from apps.edges import selection
from apps.edges.models import LOCAL_EDGE_ID, Audience, Edge, EdgeKind, EdgeStorage
from apps.edges.selection import EdgeView
from apps.playback import conf as playback_conf
from apps.playback import tokens

logger = logging.getLogger(__name__)

AUTH_KEY: Final = "edges:auth"
SNAPSHOT_KEY: Final = "edges:snapshot:v1"
SNAPSHOT_TTL_S: Final = 300
_MEMO_S: Final = 2.0
#: A standalone edge's bearer secret: 43 characters of base64url (32 random bytes).
SECRET_PATTERN: Final = re.compile(r"[A-Za-z0-9_-]{43}")
#: An edge is "down" after this many stale periods without a report.
DOWN_FACTOR: Final = 5

_memo: tuple[float, list[EdgeView]] | None = None


# --- Secrets ---------------------------------------------------------------------------


def new_secret() -> str:
    return secrets.token_urlsafe(32)


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def secret_matches(secret: str, stored_hash: str) -> bool:
    """Constant-time check of a presented bearer secret against a stored SHA-256."""
    if not stored_hash or not SECRET_PATTERN.fullmatch(secret):
        return False
    return hmac.compare_digest(hash_secret(secret), stored_hash)


def bearer(header: str) -> str:
    """The secret from `Authorization: Bearer <secret>` ("" when absent or malformed)."""
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    value = value.strip()
    return value if SECRET_PATTERN.fullmatch(value) else ""


def rotate_secret(edge: Edge) -> str:
    """A new bearer secret for a standalone edge (the old one stops working at once)."""
    if edge.kind != EdgeKind.STANDALONE:
        msg = "only standalone edges have a secret"
        raise ValueError(msg)
    secret = new_secret()
    edge.secret_hash = hash_secret(secret)
    edge.secret_set_at = timezone.now()
    edge.save(update_fields=["secret_hash", "secret_set_at", "updated_at"])
    changed()
    return secret


def rebuild_auth_mirror() -> int:
    """Rewrite `edges:auth` from Postgres: the hashes of enabled standalone edges."""
    rows = dict(
        Edge.objects.filter(kind=EdgeKind.STANDALONE, enabled=True)
        .exclude(secret_hash="")
        .values_list("edge_id", "secret_hash")
    )
    pipe = state_redis().pipeline(transaction=True)
    pipe.delete(AUTH_KEY)
    if rows:
        pipe.hset(AUTH_KEY, mapping=rows)
    pipe.execute()
    return len(rows)


# --- The snapshot ------------------------------------------------------------------------


def _epoch(value: datetime | None) -> float:
    return value.timestamp() if value is not None else 0.0


def _row(edge: Edge, canary: list[str]) -> dict[str, Any]:
    report = edge.report or {}
    return {
        "edge_id": edge.edge_id,
        "kind": edge.kind,
        "storage": edge.storage,
        "base_url": edge.base_url,
        "enabled": edge.enabled,
        "draining": edge.draining,
        "weight": edge.weight,
        "capacity_bps": float(edge.capacity_mbps) * 1e6,
        "seen": _epoch(edge.last_seen_at),
        "ok": bool(report.get("ok", edge.kind != EdgeKind.LOCAL)),
        "kids": list(report.get("kids") or []),
        "egress_bps": report.get("egress_bps"),
        "active": (report.get("connections") or {}).get("active"),
        "canary": edge.audience == Audience.CANARY,
        "canary_users": canary,
        "origin": edge.origin_edge.edge_id if edge.origin_edge is not None else "",
    }


def build_snapshot() -> list[dict[str, Any]]:
    edges = list(Edge.objects.select_related("origin_edge").prefetch_related("canary_users"))
    return [_row(edge, [str(user.pk) for user in edge.canary_users.all()]) for edge in edges]


def changed() -> None:
    """Forget the cached snapshot (after an edge changed) and refresh the auth mirror."""

    def refresh() -> None:
        global _memo  # noqa: PLW0603 (process-local memo)
        _memo = None
        try:
            cache_redis().delete(SNAPSHOT_KEY)
            rebuild_auth_mirror()
        except redis.RedisError:
            logger.warning("edges: could not refresh the snapshot or the auth mirror")

    transaction.on_commit(refresh)


def _view(row: dict[str, Any], *, now: float, stale_s: float) -> EdgeView:
    fresh = now - float(row.get("seen") or 0) <= stale_s
    egress = row.get("egress_bps")
    return EdgeView(
        edge_id=str(row["edge_id"]),
        kind=str(row["kind"]),
        storage=str(row["storage"]),
        base_url=str(row["base_url"]),
        enabled=bool(row["enabled"]),
        draining=bool(row["draining"]),
        weight=int(row["weight"]),
        healthy=fresh and bool(row.get("ok")),
        kids=frozenset(str(kid) for kid in row.get("kids") or []),
        capacity_bps=float(row.get("capacity_bps") or 0),
        egress_bps=float(egress) if isinstance(egress, int | float) else None,
        canary=bool(row.get("canary")),
        canary_users=frozenset(str(user) for user in row.get("canary_users") or []),
        origin=str(row.get("origin") or ""),
    )


def rows() -> list[dict[str, Any]]:
    """The raw snapshot rows: redis-cache, else Postgres (and cached again)."""
    found: list[dict[str, Any]] | None = None
    try:
        raw = cast("bytes | None", cache_redis().get(SNAPSHOT_KEY))
        if raw:
            found = json.loads(raw)
    except (redis.RedisError, ValueError):
        found = None
    if found is None:
        found = build_snapshot()
        try:
            cache_redis().set(SNAPSHOT_KEY, json.dumps(found), ex=SNAPSHOT_TTL_S)
        except redis.RedisError:
            logger.warning("edges: redis-cache unavailable; the snapshot is not cached")
    return found


def snapshot(*, now: float | None = None) -> list[EdgeView]:
    """Every edge as selection sees it, memoised in-process for a couple of seconds."""
    global _memo  # noqa: PLW0603 (process-local memo)
    clock = time.monotonic()
    if now is None and _memo is not None and clock - _memo[0] < _MEMO_S:
        return _memo[1]
    stale_s = float(cast("int", get_setting("edges.stale_after_s")))
    moment = time.time() if now is None else now
    views = [_view(row, now=moment, stale_s=stale_s) for row in rows()]
    if now is None:
        _memo = (clock, views)
    return views


def reset_memo() -> None:
    global _memo  # noqa: PLW0603
    _memo = None


# --- Choosing and addressing an edge ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Target:
    """Where a playback URL points: the edge, its base URL and the token's title."""

    edge: EdgeView
    base_url: str
    title: str
    fallback: str = ""


def base_url_of(edge: EdgeView) -> str:
    if edge.kind == EdgeKind.LOCAL or not edge.base_url:
        return playback_conf.media_base_url()
    return edge.base_url.rstrip("/")


def choose(
    *, session: str, user_id: str, live: bool, storage_key: str, published_key: str
) -> Target:
    """The edge a new session goes to, and the token title that edge understands."""
    try:
        signing_kid = tokens.keyring().current.kid
    except tokens.KeySetError:
        signing_kid = ""
    request = selection.Request(
        session=session,
        user_id=user_id,
        live=live,
        published_key=published_key,
        signing_kid=signing_kid,
        max_utilisation=float(cast("int", get_setting("edges.max_utilisation_pct"))) / 100,
        cdn_ready=bool(get_secret("cdn.bunny_token_key")),
    )
    choice = selection.choose(snapshot(), request)
    title = published_key if choice.storage == EdgeStorage.ORIGIN else storage_key
    return Target(choice.edge, base_url_of(choice.edge), title, choice.fallback)


# --- Reports ----------------------------------------------------------------------------------


_REPORT_INTS = ("active", "reading", "writing", "waiting", "accepts", "handled", "requests")
_KID = re.compile(r"[A-Za-z0-9_-]{1,16}")
_VERSION = re.compile(r"[A-Za-z0-9_.+ -]{0,64}")


def clean_report(body: object) -> dict[str, Any]:
    """The facts an edge may report, validated and bounded (anything else is dropped)."""
    data: dict[str, Any] = body if isinstance(body, dict) else {}
    raw_connections = data.get("connections")
    connections: dict[str, Any] = raw_connections if isinstance(raw_connections, dict) else {}
    report: dict[str, Any] = {
        "connections": {
            name: int(connections[name])
            for name in _REPORT_INTS
            if isinstance(connections.get(name), int) and 0 <= connections[name] < 2**53
        },
        "kids": sorted(
            {kid for kid in data.get("kids") or [] if isinstance(kid, str) and _KID.fullmatch(kid)}
        )[:4],
    }
    tx = data.get("tx_bytes")
    if isinstance(tx, int) and 0 <= tx < 2**63:
        report["tx_bytes"] = tx
    version = data.get("version")
    if isinstance(version, str) and _VERSION.fullmatch(version):
        report["version"] = version
    origin = data.get("origin_url")
    if isinstance(origin, str) and len(origin) <= 255:
        report["origin_url"] = origin
    report["ok"] = bool(report["kids"])
    return report


def _egress(previous: dict[str, Any], current: dict[str, Any], now: float) -> float | None:
    before, at = previous.get("tx_bytes"), previous.get("at")
    after = current.get("tx_bytes")
    if not isinstance(before, int) or not isinstance(at, int | float) or not isinstance(after, int):
        return None
    elapsed = now - float(at)
    if elapsed <= 0 or after < before:  # first report, clock jump or a restart
        return None
    return (after - before) * 8 / elapsed


def record_report(edge: Edge, body: object, *, now: float | None = None) -> dict[str, Any]:
    """Store a standalone edge's poll; returns the stored report."""
    moment = time.time() if now is None else now
    report = clean_report(body)
    report["at"] = moment
    report["egress_bps"] = _egress(edge.report or {}, report, moment)
    edge.report = report
    edge.last_seen_at = datetime.fromtimestamp(moment, tz=UTC)
    edge.save(update_fields=["report", "last_seen_at", "updated_at"])
    changed()
    return report


def _stub_status(text: str) -> dict[str, int]:
    """nginx's stub_status page into numbers."""
    numbers = [int(value) for value in re.findall(r"\d+", text)]
    if len(numbers) < 7:
        return {}
    names = ("active", "accepts", "handled", "requests", "reading", "writing", "waiting")
    return dict(zip(names, numbers[:7], strict=True))


def probe_local(client: httpx.Client | None = None, *, now: float | None = None) -> dict[str, Any]:
    """The local edge's health and connections, as a report (the worker runs it)."""
    moment = time.time() if now is None else now
    own = client is None
    http = client or httpx.Client(timeout=3.0)
    report: dict[str, Any] = {"ok": False, "at": moment, "connections": {}}
    try:
        health = http.get(settings.EDGE_LOCAL_HEALTH_URL)
        report["ok"] = health.status_code == 200
        status = http.get(settings.EDGE_LOCAL_STATUS_URL)
        if status.status_code == 200:
            report["connections"] = _stub_status(status.text)
    except httpx.HTTPError as exc:
        report["error"] = type(exc).__name__
    finally:
        if own:
            http.close()
    edge = Edge.objects.filter(kind=EdgeKind.LOCAL).first()
    if edge is not None:
        edge.report = report
        if report["ok"]:
            edge.last_seen_at = datetime.fromtimestamp(moment, tz=UTC)
        edge.save(update_fields=["report", "last_seen_at", "updated_at"])
        changed()
    return report


# --- Status for the admin -------------------------------------------------------------------------


class Status:
    DISABLED = "disabled"
    DRAINING = "draining"
    HEALTHY = "healthy"
    KEYS_OUTDATED = "keys_outdated"
    STALE = "stale"
    DOWN = "down"
    NEVER = "never_seen"
    WAITING = "waiting_for_origin"


def status_of(  # noqa: PLR0911 (one return per status)
    edge: Edge, *, now: float | None = None, signing_kid: str | None = None
) -> str:
    moment = time.time() if now is None else now
    stale_s = float(cast("int", get_setting("edges.stale_after_s")))
    if not edge.enabled:
        return Status.DISABLED
    if edge.kind == EdgeKind.CDN:
        origin = edge.origin_edge
        if origin is None:
            return Status.WAITING
        return Status.DRAINING if edge.draining else status_of(origin, now=moment)
    if edge.last_seen_at is None:
        return Status.NEVER
    age = moment - edge.last_seen_at.timestamp()
    if age > stale_s * DOWN_FACTOR:
        return Status.DOWN
    if age > stale_s or not (edge.report or {}).get("ok", False):
        return Status.STALE
    if edge.kind == EdgeKind.STANDALONE:
        kid = signing_kid if signing_kid is not None else _signing_kid()
        if kid and kid not in ((edge.report or {}).get("kids") or []):
            return Status.KEYS_OUTDATED
    return Status.DRAINING if edge.draining else Status.HEALTHY


def _signing_kid() -> str:
    try:
        return tokens.keyring().current.kid
    except tokens.KeySetError:
        return ""


# --- What a standalone edge receives ---------------------------------------------------


def _encode(secret: bytes) -> str:
    return base64.urlsafe_b64encode(secret).rstrip(b"=").decode()


def keyset_document() -> dict[str, Any]:
    """The media token keys as the edge's key file holds them (current and previous)."""
    keyset = tokens.keyring()
    previous = keyset.previous
    return {
        "current": {"kid": keyset.current.kid, "secret": _encode(keyset.current.secret)},
        "previous": None
        if previous is None
        else {"kid": previous.kid, "secret": _encode(previous.secret)},
    }


def origin_document() -> dict[str, str] | None:
    """The S3 origin's read-only credentials for edges, or None when not configured."""
    access, secret = (
        get_secret("storage.s3_edge_access_key"),
        get_secret("storage.s3_edge_secret_key"),
    )
    if not access or not secret:
        return None
    region = str(get_setting("storage.s3_region")) or "us-east-1"
    return {"access_key": access, "secret_key": secret, "region": region}


def origin_url() -> str:
    """The URL objects are read from: <endpoint>/<bucket>/<prefix> (path style) or
    https://<bucket>.<host>/<prefix> (virtual-hosted)."""
    endpoint = str(get_setting("storage.s3_endpoint")).rstrip("/")
    bucket = str(get_setting("storage.s3_bucket"))
    prefix = str(get_setting("storage.s3_prefix")).strip("/")
    if not endpoint or not bucket:
        return ""
    if bool(get_setting("storage.s3_path_style")):
        base = f"{endpoint}/{bucket}"
    else:
        scheme, _, host = endpoint.partition("://")
        base = f"{scheme}://{bucket}.{host}"
    return f"{base}/{prefix}" if prefix else base


def poll_answer(edge: Edge) -> dict[str, Any]:
    """The control channel's answer: keys, origin credentials and the CDN origin secret."""
    return {
        "edge_id": edge.edge_id,
        "keys": keyset_document(),
        "origin": origin_document() if edge.storage == EdgeStorage.ORIGIN else None,
        "origin_url": origin_url() if edge.storage == EdgeStorage.ORIGIN else "",
        "cdn_origin_secret": get_secret("cdn.origin_secret") or None,
    }


def local_edge() -> Edge | None:
    return Edge.objects.filter(edge_id=LOCAL_EDGE_ID).first()


# --- Playback URLs ------------------------------------------------------------------------------


def media_url(  # noqa: PLR0913 (keyword-only facts of one URL)
    target: Target,
    *,
    token: str,
    rendition: str,
    entry: str,
    exp: int,
    client_ip: str | None = None,
) -> str:
    """The playback URL on the chosen edge.

    Our edges: `<base>/v/<token>/<entry>` (ADR-0007). A CDN zone: Bunny's signed URL for
    `/<title>/<entry>`, scoped to the presentation's folder for HLS (playlists, segments,
    subtitles and thumbnails) and to the file itself for a progressive rendition; Bunny
    pulls it from its origin edge's `/c/` location (ADR-0022).
    """
    if target.edge.kind != EdgeKind.CDN:
        return f"{target.base_url}/v/{token}/{entry}"
    from apps.edges import bunny  # noqa: PLC0415

    path = f"/{target.title}/{entry}"
    directory = f"/{target.title}/{rendition}/" if entry.startswith(f"{rendition}/") else None
    ip = client_ip if get_setting("cdn.bunny_ip_binding") else None
    return bunny.signed_url(
        target.base_url,
        path,
        key=get_secret("cdn.bunny_token_key"),
        expires=exp,
        directory=directory,
        ip=ip,
    )
