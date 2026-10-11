"""Edge metrics, read from the snapshot when /metrics is scraped (ADR-0022, ADR-0018).

- `iptv_edges_up{edge}`: 1 while an enabled edge reports in time (CDN zones follow their
  origin); disabled edges are left out, so EdgeDown never fires for them;
- `iptv_edges_connections{edge}`: its active connections (nginx stub_status);
- `iptv_edges_egress_bits{edge}`: bits per second it sends, from its byte counters;
- `iptv_edges_capacity_bits{edge}`: the capacity the admin set, for EdgeOverCapacity;
- `iptv_edges_keys_current{edge}`: 1 when a standalone edge holds the signing key.
"""

from collections.abc import Iterable

import redis
from prometheus_client.core import GaugeMetricFamily, Metric
from prometheus_client.registry import Collector

from apps.edges.selection import KIND_CDN, KIND_STANDALONE


class EdgeCollector(Collector):
    def collect(self) -> Iterable[Metric]:
        from apps.edges import services  # noqa: PLC0415
        from apps.playback import tokens  # noqa: PLC0415

        up = GaugeMetricFamily("iptv_edges_up", "Enabled edges reporting in time.", labels=["edge"])
        connections = GaugeMetricFamily(
            "iptv_edges_connections", "Active connections per edge.", labels=["edge"]
        )
        egress = GaugeMetricFamily(
            "iptv_edges_egress_bits", "Bits per second each edge sends.", labels=["edge"]
        )
        capacity = GaugeMetricFamily(
            "iptv_edges_capacity_bits", "Each edge's capacity, in bits per second.", labels=["edge"]
        )
        keys = GaugeMetricFamily(
            "iptv_edges_keys_current",
            "1 when a standalone edge holds the current signing key.",
            labels=["edge"],
        )
        try:
            views = services.snapshot()
            raw = {row["edge_id"]: row for row in services.rows()}
        except (redis.RedisError, Exception):
            return [up, connections, egress, capacity, keys]
        try:
            kid = tokens.keyring().current.kid
        except tokens.KeySetError:
            kid = ""
        by_id = {view.edge_id: view for view in views}
        for view in views:
            if not view.enabled:
                continue
            target = by_id.get(view.origin) if view.kind == KIND_CDN else view
            up.add_metric([view.edge_id], 1.0 if target is not None and target.healthy else 0.0)
            if view.kind == KIND_CDN:
                continue
            capacity.add_metric([view.edge_id], view.capacity_bps)
            if view.egress_bps is not None:
                egress.add_metric([view.edge_id], view.egress_bps)
            active = raw.get(view.edge_id, {}).get("active")
            if isinstance(active, int):
                connections.add_metric([view.edge_id], float(active))
            if view.kind == KIND_STANDALONE:
                keys.add_metric([view.edge_id], 1.0 if kid and kid in view.kids else 0.0)
        return [up, connections, egress, capacity, keys]
