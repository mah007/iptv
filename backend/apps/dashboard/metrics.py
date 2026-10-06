"""State and business metrics for Prometheus (SPEC §14, ADR-0018).

- `publish()` runs every minute on a worker (`apps.dashboard.tasks.publish_metrics`)
  and publishes snapshot gauges to redis-cache: customers, devices, trials, MRR and
  revenue, titles, the review queue, transcode jobs (counts, speed, oldest age),
  failing payment webhooks and the notification outbox. A gauge disappears 15 min
  after the job stops, which the alerts on the job's own health cover.
- `LiveStateCollector` runs while web serves /metrics and reads Redis only: the age
  of the beat and watcher heartbeats and the length of every Celery queue. Those
  must stay visible precisely when the workers that would publish them are down.
"""

import logging
import time
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import cast

import redis
from django.conf import settings
from django.db.models import Avg, Count, Min, Q
from django.utils import timezone
from prometheus_client.core import GaugeMetricFamily, Metric
from prometheus_client.registry import Collector

from apps.billing import kpis as billing_kpis
from apps.billing.models import WebhookEvent
from apps.catalog.models import MatchReview, Movie, ReviewStatus, Series
from apps.core import metrics
from apps.core.stores import state_redis
from apps.core.tasks import HEARTBEAT_KEY as BEAT_HEARTBEAT_KEY
from apps.dashboard import health
from apps.dashboard.services import compute_kpis
from apps.library.watcher import HEARTBEAT_KEY as WATCHER_HEARTBEAT_KEY
from apps.media.models import JobStatus, TranscodeJob
from apps.notifications.models import NotificationOutbox, OutboxStatus

logger = logging.getLogger(__name__)

FAILED_WINDOW = timedelta(hours=24)
HEARTBEATS = {"beat": BEAT_HEARTBEAT_KEY, "watcher": WATCHER_HEARTBEAT_KEY}


def _business(now: datetime) -> None:
    kpis = compute_kpis(now=now)
    metrics.CUSTOMERS.publish(
        {
            ("active",): kpis.customers_active,
            ("expired",): kpis.customers_expired,
            ("suspended",): kpis.customers_suspended,
            ("expiring_7d",): kpis.expiring_7d,
        }
    )
    metrics.DEVICES.publish(
        {
            ("active",): kpis.devices_total - kpis.devices_blocked,
            ("blocked",): kpis.devices_blocked,
        }
    )
    billing = billing_kpis.compute(now=now)
    metrics.TRIALS_ACTIVE.publish({(): billing.trials_active})
    metrics.MRR.publish({(item.currency,): item.amount for item in billing.mrr})
    metrics.REVENUE_MONTH.publish({(item.currency,): item.amount for item in billing.revenue_mtd})


def _catalog() -> None:
    titles: dict[tuple[str, ...], float] = {}
    for kind, model in (("movie", Movie), ("series", Series)):
        for row in model.objects.values("status").annotate(total=Count("pk")).order_by():
            titles[(kind, str(row["status"]))] = row["total"]
    metrics.CATALOG_TITLES.publish(titles)
    open_reviews = MatchReview.objects.filter(status=ReviewStatus.OPEN).count()
    metrics.REVIEW_QUEUE_OPEN.publish({(): open_reviews})


def _transcoding(now: datetime) -> None:
    counts = TranscodeJob.objects.values("status", "backend").annotate(total=Count("pk")).order_by()
    metrics.TRANSCODE_JOBS.publish(
        {(str(row["status"]), str(row["backend"])): row["total"] for row in counts}
    )
    speeds = (
        TranscodeJob.objects.filter(status=JobStatus.RUNNING, speed__isnull=False)
        .values("backend")
        .annotate(speed=Avg("speed"))
        .order_by()
    )
    metrics.TRANSCODE_SPEED_RATIO.publish(
        {(str(row["backend"]),): float(row["speed"]) for row in speeds if row["speed"] is not None}
    )
    oldest = TranscodeJob.objects.aggregate(
        queued=Min("created_at", filter=Q(status=JobStatus.QUEUED)),
        running=Min("started_at", filter=Q(status=JobStatus.RUNNING)),
    )
    metrics.TRANSCODE_OLDEST_JOB_AGE.publish(
        {
            (status,): max(0.0, (now - moment).total_seconds()) if moment else 0.0
            for status, moment in oldest.items()
        }
    )


def _operations(now: datetime) -> None:
    failing = (
        WebhookEvent.objects.filter(processed_at__isnull=True)
        .exclude(error="")
        .values("provider")
        .annotate(total=Count("pk"))
        .order_by()
    )
    by_provider: dict[tuple[str, ...], float] = {
        (str(row["provider"]),): float(row["total"]) for row in failing
    }
    metrics.PAYMENT_WEBHOOKS_FAILING.publish(by_provider)
    outbox: dict[tuple[str, ...], float] = defaultdict(float)
    rows = (
        NotificationOutbox.objects.filter(
            Q(status=OutboxStatus.QUEUED)
            | Q(status=OutboxStatus.FAILED, updated_at__gte=now - FAILED_WINDOW)
        )
        .values("channel", "status")
        .annotate(total=Count("pk"))
        .order_by()
    )
    for row in rows:
        outbox[(str(row["channel"]), str(row["status"]))] += row["total"]
    metrics.NOTIFICATION_OUTBOX.publish(dict(outbox))


def publish(now: datetime | None = None) -> None:
    """Compute and publish every snapshot gauge this module owns."""
    moment = now or timezone.now()
    _business(moment)
    _catalog()
    _transcoding(moment)
    _operations(moment)


class LiveStateCollector(Collector):
    """Heartbeat ages and queue lengths, read from Redis at scrape time; never raises."""

    def describe(self) -> Iterable[Metric]:
        return []

    def collect(self) -> Iterable[Metric]:
        ages = GaugeMetricFamily(
            "iptv_heartbeat_age_seconds",
            "Seconds since a background service last beat (+Inf when it never did).",
            labels=("service",),
        )
        now = time.time()
        try:
            client = state_redis()
            for service, key in HEARTBEATS.items():
                raw = cast("bytes | None", client.get(key))
                try:
                    age = max(0.0, now - int(raw)) if raw is not None else float("inf")
                except ValueError:
                    age = float("inf")
                ages.add_metric([service], age)
        except redis.RedisError:
            logger.warning("metrics: redis-state unavailable for heartbeats", exc_info=True)
        queues = GaugeMetricFamily(
            "iptv_celery_queue_length", "Messages waiting in each Celery queue.", labels=("queue",)
        )
        for depth in health.queue_depths():
            if depth.depth is not None:
                queues.add_metric([depth.name], depth.depth)
        info = GaugeMetricFamily(
            "iptv_build_info", "The running application version.", labels=("version",)
        )
        info.add_metric([str(settings.APP_VERSION)], 1)
        return [ages, queues, info]
