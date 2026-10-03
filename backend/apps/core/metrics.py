"""Prometheus metrics (SPEC §14), exported on the internal URLconf at /metrics.

Two kinds:
- Event metrics (counters, histograms) live in the process that observes the
  event. Under Gunicorn, prometheus_client's multiprocess mode aggregates them
  across workers (PROMETHEUS_MULTIPROC_DIR, set by config/gunicorn_conf.py).
  Ones observed in Celery workers need a worker exporter, which arrives with the
  monitoring overlay (ADR-0005).
- Snapshot gauges describe current state (subscriptions by status, active
  streams, queue sizes). A periodic job computes them in any process and
  publishes them to redis-cache; /metrics reads the latest snapshot. That keeps
  them correct no matter which process or container computed them.
"""

import os
from collections.abc import Iterable, Mapping, Sequence

import redis
from django.core.cache import cache
from prometheus_client import REGISTRY, CollectorRegistry, Counter, Histogram, multiprocess
from prometheus_client.core import GaugeMetricFamily, Metric
from prometheus_client.registry import Collector

SNAPSHOT_KEY_PREFIX = "metrics:snapshot:"
SNAPSHOT_TTL_S = 900


class SnapshotGauge(Collector):
    """A gauge whose samples are published by a job and read back at scrape time."""

    def __init__(
        self,
        name: str,
        documentation: str,
        labelnames: Sequence[str] = (),
        *,
        ttl_s: int = SNAPSHOT_TTL_S,
    ) -> None:
        self.name = name
        self.documentation = documentation
        self.labelnames = tuple(labelnames)
        self.ttl_s = ttl_s

    @property
    def cache_key(self) -> str:
        return SNAPSHOT_KEY_PREFIX + self.name

    def publish(self, samples: Mapping[tuple[str, ...], float]) -> None:
        """Replace the snapshot. Keys are label values in `labelnames` order."""
        rows: list[tuple[list[str], float]] = []
        for labels, value in samples.items():
            if len(labels) != len(self.labelnames):
                msg = f"{self.name} expects labels {self.labelnames}, got {labels}"
                raise ValueError(msg)
            rows.append(([str(label) for label in labels], float(value)))
        cache.set(self.cache_key, rows, timeout=self.ttl_s)

    def _family(self) -> GaugeMetricFamily:
        return GaugeMetricFamily(self.name, self.documentation, labels=self.labelnames)

    def describe(self) -> Iterable[Metric]:
        return [self._family()]

    def collect(self) -> Iterable[Metric]:
        family = self._family()
        try:
            rows = cache.get(self.cache_key) or []
        except redis.RedisError:
            rows = []
        for labels, value in rows:
            family.add_metric(labels, value)
        return [family]


# --- Event metrics -------------------------------------------------------------------

STREAM_STARTS = Counter("iptv_stream_starts", "Playback start attempts by result.", ("result",))
CONCURRENCY_REJECTIONS = Counter(
    "iptv_concurrency_rejections", "Playback starts refused by the stream limit."
)
KICKS = Counter("iptv_kicks", "Sessions ended by the platform, by reason.", ("reason",))
XTREAM_REQUESTS = Counter(
    "iptv_xtream_requests", "Xtream API requests by action and status.", ("action", "status")
)
XTREAM_LATENCY = Histogram("iptv_xtream_latency_seconds", "Xtream API response time.")
SCAN_FILES = Counter("iptv_scan_files", "Library scan file outcomes.", ("result",))
MATCH_CONFIDENCE = Histogram(
    "iptv_match_confidence",
    "Metadata match confidence of identified files.",
    buckets=(0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95, 1.0),
)
PAYMENTS = Counter("iptv_payments", "Payments by provider and status.", ("provider", "status"))
NOTIFICATIONS = Counter(
    "iptv_notifications", "Notification deliveries by channel and status.", ("channel", "status")
)
METADATA_API_REQUESTS = Counter(
    "iptv_metadata_api_requests",
    "Metadata provider API calls by provider and status.",
    ("provider", "status"),
)

# --- Snapshot gauges -------------------------------------------------------------------

ACTIVE_STREAMS = SnapshotGauge(
    "iptv_active_streams", "Streams playing now.", ("plan", "rendition", "edge")
)
SUBSCRIPTIONS = SnapshotGauge("iptv_subscriptions", "Subscriptions by status.", ("status",))
TRANSCODE_JOBS = SnapshotGauge(
    "iptv_transcode_jobs", "Transcode jobs by status and backend.", ("status", "backend")
)
TRANSCODE_SPEED_RATIO = SnapshotGauge(
    "iptv_transcode_speed_ratio", "Encoding speed of running transcodes relative to real time."
)
REVIEW_QUEUE_OPEN = SnapshotGauge("iptv_review_queue_open", "Open metadata match reviews.")

SNAPSHOT_GAUGES: tuple[SnapshotGauge, ...] = (
    ACTIVE_STREAMS,
    SUBSCRIPTIONS,
    TRANSCODE_JOBS,
    TRANSCODE_SPEED_RATIO,
    REVIEW_QUEUE_OPEN,
)


class _DefaultRegistry(Collector):
    """Everything registered in this process (single-process mode)."""

    def collect(self) -> Iterable[Metric]:
        return REGISTRY.collect()


def metrics_registry() -> CollectorRegistry:
    """What /metrics exports: process metrics (summed over Gunicorn workers) and snapshots."""
    registry = CollectorRegistry(auto_describe=False)
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        multiprocess.MultiProcessCollector(registry)
    else:
        registry.register(_DefaultRegistry())
    for gauge in SNAPSHOT_GAUGES:
        registry.register(gauge)
    return registry
