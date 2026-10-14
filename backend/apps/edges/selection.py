"""Which edge a new playback session goes to (ADR-0022). Pure: no I/O.

`choose()` takes the edges as `EdgeView` snapshots and the session's facts, and keeps the
edges that may take it:

1. enabled and not draining;
2. healthy: the local edge's probe passed recently, a standalone edge polled recently and
   holds the current signing key (a CDN zone follows its origin edge);
3. able to serve it: live TV only on the local edge; an S3-origin edge only for renditions
   published there (`published_key`);
4. under `max_utilisation` of its capacity, when it reports its egress;
5. inside its audience (everyone, or the canary customers).

Among those it picks by weighted rendezvous hashing on the session key: the same
session (a seek or a reconnect within the reuse window) lands on the same edge, weights
split new sessions, and losing an edge moves only that edge's sessions. With nothing
eligible the local edge answers, as it did before edges existed.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Final

KIND_LOCAL: Final = "local"
KIND_STANDALONE: Final = "standalone"
KIND_CDN: Final = "cdn"
STORAGE_DISK: Final = "disk"
STORAGE_ORIGIN: Final = "origin"


@dataclass(frozen=True, slots=True)
class EdgeView:
    """What selection knows about one edge (from `services.snapshot()`)."""

    edge_id: str
    kind: str
    storage: str
    base_url: str
    enabled: bool = True
    draining: bool = False
    weight: int = 100
    healthy: bool = True
    #: Key ids the edge has loaded (standalone edges report them).
    kids: frozenset[str] = frozenset()
    capacity_bps: float = 0.0
    egress_bps: float | None = None
    canary: bool = False
    canary_users: frozenset[str] = frozenset()
    #: A CDN zone's origin edge id.
    origin: str = ""


@dataclass(frozen=True, slots=True)
class Request:
    session: str
    user_id: str
    live: bool
    #: The rendition's key on the S3 origin; "" when it isn't published (or is stale).
    published_key: str = ""
    signing_kid: str = ""
    max_utilisation: float = 0.9
    #: CDN zones are offered only when Bunny's token key is configured.
    cdn_ready: bool = False


@dataclass(frozen=True, slots=True)
class Choice:
    edge: EdgeView
    #: The storage the URL must address: the asset key on disk, or the published key.
    storage: str
    #: Why the local edge was used although it wasn't eligible ("" when it was).
    fallback: str = ""
    considered: tuple[str, ...] = field(default=())


def _storage_of(edge: EdgeView, by_id: dict[str, EdgeView]) -> str:
    if edge.kind == KIND_CDN:
        origin = by_id.get(edge.origin)
        return origin.storage if origin is not None else ""
    return edge.storage


def _healthy(edge: EdgeView, request: Request, by_id: dict[str, EdgeView]) -> bool:
    if edge.kind == KIND_CDN:
        origin = by_id.get(edge.origin)
        return (
            request.cdn_ready
            and origin is not None
            and origin.enabled
            and _healthy(origin, request, by_id)
        )
    if not edge.healthy:
        return False
    return edge.kind == KIND_LOCAL or not request.signing_kid or request.signing_kid in edge.kids


def eligible(  # noqa: PLR0911 (one return per rule, in the documented order)
    edge: EdgeView, request: Request, by_id: dict[str, EdgeView]
) -> bool:
    if not edge.enabled or edge.draining or edge.weight <= 0:
        return False
    if request.live and edge.kind != KIND_LOCAL:
        return False
    storage = _storage_of(edge, by_id)
    if storage == STORAGE_ORIGIN and not request.published_key:
        return False
    if storage not in (STORAGE_DISK, STORAGE_ORIGIN):
        return False
    if edge.canary and request.user_id not in edge.canary_users:
        return False
    if (
        edge.egress_bps is not None
        and edge.capacity_bps > 0
        and edge.egress_bps >= edge.capacity_bps * request.max_utilisation
    ):
        return False
    return _healthy(edge, request, by_id)


def _score(session: str, edge: EdgeView) -> float:
    """Weighted rendezvous score: weight / -ln(u), u uniform in (0, 1) per (session, edge)."""
    digest = hashlib.sha256(f"{session}|{edge.edge_id}".encode()).digest()
    u = (int.from_bytes(digest[:8], "big") + 1) / (2**64 + 2)
    return edge.weight / -math.log(u)


def choose(edges: list[EdgeView], request: Request) -> Choice:
    """The edge for this session; the local edge when nothing else is eligible."""
    by_id = {edge.edge_id: edge for edge in edges}
    candidates = [edge for edge in edges if eligible(edge, request, by_id)]
    if candidates:
        best = max(candidates, key=lambda edge: (_score(request.session, edge), edge.edge_id))
        return Choice(
            best,
            _storage_of(best, by_id),
            considered=tuple(sorted(edge.edge_id for edge in candidates)),
        )
    local = next((edge for edge in edges if edge.kind == KIND_LOCAL), None)
    if local is None:
        local = EdgeView(edge_id="local", kind=KIND_LOCAL, storage=STORAGE_DISK, base_url="")
    return Choice(local, STORAGE_DISK, fallback="no_eligible_edge")
