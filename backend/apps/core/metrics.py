"""Prometheus metrics (SPEC §14), exported on the internal URLconf at /metrics.

Three kinds:
- Event metrics (counters, histograms) live in the process that observes the
  event. Under Gunicorn, prometheus_client's multiprocess mode aggregates them
  across workers (PROMETHEUS_MULTIPROC_DIR, set by config/gunicorn_conf.py).
  Celery workers serve theirs on their own internal port (apps.core.worker_metrics,
  ADR-0018), so Prometheus scrapes web, worker and transcoder and sums them.
- Snapshot gauges describe current state (subscriptions by status, active
  streams, queue sizes). A periodic job computes them in any process and
  publishes them to redis-cache; /metrics reads the latest snapshot. That keeps
  them correct no matter which process or container computed them.
- Scrape-time collectors (`register_scrape_collector`) read a little live state
  from Redis while /metrics is served, such as heartbeat ages and queue lengths,
  which must stay visible when the workers that would publish them are down.
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
# Observed by RequestLogMiddleware on the tv host; `action` comes from a fixed set
# (apps.core.middleware.xtream_action), so the label stays bounded.
XTREAM_REQUESTS = Counter(
    "iptv_xtream_requests", "Xtream API requests by action and status.", ("action", "status")
)
XTREAM_LATENCY = Histogram(
    "iptv_xtream_latency_seconds",
    "Xtream API response time by action.",
    ("action",),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.15, 0.25, 0.3, 0.5, 1.0, 2.5, 5.0),
)
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
# Payment provider webhooks (apps.billing.webhooks): processed, duplicate, invalid
# (signature or token refused) or failed (stored with an error; the provider retries).
PAYMENT_WEBHOOKS = Counter(
    "iptv_payment_webhooks", "Payment webhooks by provider and result.", ("provider", "result")
)
# Celery tasks, observed in the worker processes (apps.core.worker_metrics). Only our own
# `apps.*` task names are used as labels; anything else is counted as "other".
CELERY_TASKS = Counter(
    "iptv_celery_tasks", "Finished Celery tasks by task name and state.", ("task", "state")
)
CELERY_TASK_DURATION = Histogram(
    "iptv_celery_task_duration_seconds",
    "Celery task run time by task name.",
    ("task",),
    buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 15.0, 60.0, 300.0, 900.0, 3600.0, 14400.0),
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
    "iptv_transcode_speed_ratio",
    "Mean encoding speed of running transcodes relative to real time, by backend.",
    ("backend",),
)
REVIEW_QUEUE_OPEN = SnapshotGauge("iptv_review_queue_open", "Open metadata match reviews.")
# Published by apps.dashboard.metrics.publish (ADR-0018).
TRANSCODE_OLDEST_JOB_AGE = SnapshotGauge(
    "iptv_transcode_oldest_job_age_seconds",
    "Age of the oldest unfinished transcode job: queued since creation, running since start.",
    ("status",),
)
CUSTOMERS = SnapshotGauge("iptv_customers", "Customers by access status.", ("status",))
DEVICES = SnapshotGauge("iptv_devices", "Customer devices that are not revoked.", ("state",))
TRIALS_ACTIVE = SnapshotGauge("iptv_trials_active", "Trial subscriptions running now.")
MRR = SnapshotGauge(
    "iptv_mrr_minor", "Monthly recurring revenue in minor units, by currency.", ("currency",)
)
REVENUE_MONTH = SnapshotGauge(
    "iptv_revenue_month_minor",
    "Revenue received this calendar month (Riyadh), net of refunds, in minor units.",
    ("currency",),
)
CATALOG_TITLES = SnapshotGauge(
    "iptv_catalog_titles", "Movies and series by status.", ("kind", "status")
)
PAYMENT_WEBHOOKS_FAILING = SnapshotGauge(
    "iptv_payment_webhooks_failing",
    "Stored payment webhook events whose processing failed and has not succeeded since.",
    ("provider",),
)
NOTIFICATION_OUTBOX = SnapshotGauge(
    "iptv_notification_outbox",
    "Notification outbox rows by channel and status.",
    ("channel", "status"),
)
# Set by `manage.py fire_drill` for the drill's length (its own TTL), not by the job.
FIRE_DRILL = SnapshotGauge("iptv_fire_drill", "1 while an alerting fire drill runs.")

SNAPSHOT_GAUGES: tuple[SnapshotGauge, ...] = (
    ACTIVE_STREAMS,
    SUBSCRIPTIONS,
    TRANSCODE_JOBS,
    TRANSCODE_SPEED_RATIO,
    REVIEW_QUEUE_OPEN,
    TRANSCODE_OLDEST_JOB_AGE,
    CUSTOMERS,
    DEVICES,
    TRIALS_ACTIVE,
    MRR,
    REVENUE_MONTH,
    CATALOG_TITLES,
    PAYMENT_WEBHOOKS_FAILING,
    NOTIFICATION_OUTBOX,
    FIRE_DRILL,
)

# --- Scrape-time collectors ----------------------------------------------------------

_SCRAPE_COLLECTORS: list[Collector] = []


def register_scrape_collector(collector: Collector) -> None:
    """Add a collector that /metrics runs on every scrape (from an AppConfig.ready)."""
    if collector not in _SCRAPE_COLLECTORS:
        _SCRAPE_COLLECTORS.append(collector)


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
    for collector in _SCRAPE_COLLECTORS:
        registry.register(collector)
    return registry
