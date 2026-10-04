"""State and business gauges for Prometheus, and the fire drill (SPEC §14, §17, ADR-0018)."""

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest import mock

import pytest
import redis
from django.conf import settings
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client
from django.utils import timezone
from prometheus_client import generate_latest

from apps.audit.models import AuditLog
from apps.billing import kpis as billing_kpis
from apps.billing.models import WebhookEvent
from apps.catalog.models import Movie, Series, TitleStatus
from apps.conftest import CustomerFactory
from apps.core import metrics
from apps.core.metrics import metrics_registry
from apps.core.stores import state_redis
from apps.core.tasks import HEARTBEAT_KEY
from apps.dashboard import metrics as dashboard_metrics
from apps.dashboard.health import QueueDepth
from apps.dashboard.tasks import publish_metrics
from apps.dashboard.tests.conftest import open_review, transcode_job
from apps.library.models import Library
from apps.library.watcher import HEARTBEAT_KEY as WATCHER_HEARTBEAT_KEY
from apps.media.models import JobStatus, TranscodeJob
from apps.notifications.models import NotificationOutbox, OutboxStatus

pytestmark = pytest.mark.django_db

type Capture = Callable[..., Any]


def exposition() -> str:
    return generate_latest(metrics_registry()).decode()


def billing(**overrides: Any) -> billing_kpis.BillingKpis:
    values: dict[str, Any] = {
        "active_subscribers": 0,
        "grace": 0,
        "suspended": 0,
        "trials_active": 2,
        "trial_requests": 0,
        "expiring_7d": 0,
        "new_subscriptions_30d": 0,
        "churned_30d": 0,
        "mrr": [billing_kpis.Amount("SAR", 450_00)],
        "mrr_net": [],
        "revenue_mtd": [billing_kpis.Amount("SAR", 120_00), billing_kpis.Amount("USD", 5_00)],
        "revenue_last_month": [],
        "by_plan": [],
        "as_of": timezone.now(),
    }
    values.update(overrides)
    return billing_kpis.BillingKpis(**values)


def test_publish_exports_every_state_gauge(
    make_customer: CustomerFactory, library: Library, django_assert_max_num_queries: Capture
) -> None:
    now = timezone.now()
    make_customer(expires_at=now + timedelta(days=30), devices=2)
    make_customer(expires_at=now + timedelta(days=3))
    make_customer(expires_at=now - timedelta(days=1))
    Movie.objects.create(title="Ready", status=TitleStatus.READY)
    Movie.objects.create(title="Waiting", status=TitleStatus.REVIEW)
    Series.objects.create(title="Show", status=TitleStatus.READY)
    open_review(library)
    open_review(library)
    queued = transcode_job(library, "queued")
    TranscodeJob.objects.filter(pk=queued.pk).update(created_at=now - timedelta(hours=3))
    running = transcode_job(library, "running")
    TranscodeJob.objects.filter(pk=running.pk).update(
        started_at=now - timedelta(minutes=10), speed=2.5, backend="cpu"
    )
    transcode_job(library, "failed")
    WebhookEvent.objects.create(provider="stripe", event_id="evt-1", type="x", error="Boom")
    WebhookEvent.objects.create(provider="stripe", event_id="evt-2", type="x", processed_at=now)
    NotificationOutbox.objects.create(template_key="t", locale="en")
    NotificationOutbox.objects.create(template_key="t", locale="en", status=OutboxStatus.FAILED)
    NotificationOutbox.objects.create(template_key="t", locale="en", status=OutboxStatus.SENT)

    with (
        mock.patch.object(billing_kpis, "compute", return_value=billing()),
        django_assert_max_num_queries(20),
    ):
        publish_metrics()
    body = exposition()

    assert 'iptv_customers{status="active"} 2.0' in body
    assert 'iptv_customers{status="expired"} 1.0' in body
    assert 'iptv_customers{status="expiring_7d"} 1.0' in body
    assert 'iptv_devices{state="active"} 2.0' in body
    assert "iptv_trials_active 2.0" in body
    assert 'iptv_mrr_minor{currency="SAR"} 45000.0' in body
    assert 'iptv_revenue_month_minor{currency="SAR"} 12000.0' in body
    assert 'iptv_revenue_month_minor{currency="USD"} 500.0' in body
    assert 'iptv_catalog_titles{kind="movie",status="ready"} 1.0' in body
    assert 'iptv_catalog_titles{kind="movie",status="review"} 1.0' in body
    assert 'iptv_catalog_titles{kind="series",status="ready"} 1.0' in body
    assert "iptv_review_queue_open 2.0" in body
    assert 'iptv_transcode_jobs{backend="cpu",status="queued"} 1.0' in body
    assert 'iptv_transcode_jobs{backend="cpu",status="failed"} 1.0' in body
    assert 'iptv_transcode_speed_ratio{backend="cpu"} 2.5' in body
    assert 'iptv_payment_webhooks_failing{provider="stripe"} 1.0' in body
    assert 'iptv_notification_outbox{channel="email",status="queued"} 1.0' in body
    assert 'iptv_notification_outbox{channel="email",status="failed"} 1.0' in body
    assert 'status="sent"' not in body

    family = next(iter(metrics.TRANSCODE_OLDEST_JOB_AGE.collect()))
    ages = {sample.labels["status"]: sample.value for sample in family.samples}
    assert 3 * 3600 - 60 <= ages["queued"] <= 3 * 3600 + 60
    assert 600 - 60 <= ages["running"] <= 600 + 60


def test_no_unfinished_jobs_means_age_zero() -> None:
    with mock.patch.object(billing_kpis, "compute", return_value=billing(mrr=[], revenue_mtd=[])):
        dashboard_metrics.publish()
    body = exposition()
    assert 'iptv_transcode_oldest_job_age_seconds{status="queued"} 0.0' in body
    assert 'iptv_transcode_oldest_job_age_seconds{status="running"} 0.0' in body
    assert "iptv_review_queue_open 0.0" in body
    assert "iptv_mrr_minor{" not in body


def test_publish_metrics_is_scheduled_every_minute() -> None:
    entry = settings.CELERY_BEAT_SCHEDULE["dashboard-publish-metrics"]
    assert entry["task"] == "apps.dashboard.tasks.publish_metrics"
    assert entry["schedule"] == 60.0


# --- Read while /metrics is scraped -----------------------------------------------------


def test_heartbeat_ages_and_queue_lengths_are_read_at_scrape_time() -> None:
    client = state_redis()
    client.set(HEARTBEAT_KEY, int(time.time()) - 42)
    client.delete(WATCHER_HEARTBEAT_KEY)
    depths = [QueueDepth("default", 3), QueueDepth("scan", None)]
    with mock.patch("apps.dashboard.health.queue_depths", return_value=depths):
        response = Client().get("/metrics", headers={"host": "web"})
    body = response.content.decode()
    beat = next(
        line
        for line in body.splitlines()
        if line.startswith('iptv_heartbeat_age_seconds{service="beat"}')
    )
    assert 41 <= float(beat.split()[-1]) <= 45
    assert 'iptv_heartbeat_age_seconds{service="watcher"} +Inf' in body
    assert 'iptv_celery_queue_length{queue="default"} 3.0' in body
    assert 'queue="scan"' not in body
    assert f'iptv_build_info{{version="{settings.APP_VERSION}"}} 1.0' in body


def test_a_garbled_heartbeat_reads_as_never() -> None:
    state_redis().set(HEARTBEAT_KEY, "not-a-number")
    with mock.patch("apps.dashboard.health.queue_depths", return_value=[]):
        (ages, _queues, _info) = dashboard_metrics.LiveStateCollector().collect()
    beat = next(sample for sample in ages.samples if sample.labels["service"] == "beat")
    assert beat.value == float("inf")


def test_redis_down_drops_the_heartbeats_but_never_fails_the_scrape() -> None:
    broken = mock.Mock(get=mock.Mock(side_effect=redis.ConnectionError))
    with (
        mock.patch.object(dashboard_metrics, "state_redis", return_value=broken),
        mock.patch("apps.dashboard.health.queue_depths", return_value=[]),
    ):
        ages, queues, info = dashboard_metrics.LiveStateCollector().collect()
    assert ages.samples == []
    assert queues.samples == []
    assert len(info.samples) == 1


# --- Fire drill ------------------------------------------------------------------------


def test_fire_drill_raises_the_gauge_for_its_length_and_is_audited() -> None:
    with mock.patch.object(cache, "set", wraps=cache.set) as cache_set:
        call_command("fire_drill", "--minutes", "5")
    assert cache_set.call_args.kwargs["timeout"] == 300
    assert "iptv_fire_drill 1.0" in exposition()
    entry = AuditLog.objects.get(action="monitoring.fire_drill")
    assert entry.actor is None
    assert entry.after == {"step": "start", "minutes": 5}

    call_command("fire_drill", "--stop")
    assert "iptv_fire_drill 1.0" not in exposition()
    assert AuditLog.objects.filter(action="monitoring.fire_drill").count() == 2


@pytest.mark.parametrize(("minutes", "ttl"), [("0", 60), ("500", 3600)])
def test_fire_drill_length_is_clamped(minutes: str, ttl: int) -> None:
    with mock.patch("apps.core.metrics.cache") as cache:
        call_command("fire_drill", "--minutes", minutes)
    assert cache.set.call_args.kwargs["timeout"] == ttl


def test_jobs_finished_long_ago_do_not_count_as_oldest(library: Library) -> None:
    done = transcode_job(library, "done")
    TranscodeJob.objects.filter(pk=done.pk).update(
        created_at=datetime(2020, 1, 1, tzinfo=UTC), status=JobStatus.DONE
    )
    with mock.patch.object(billing_kpis, "compute", return_value=billing()):
        dashboard_metrics.publish()
    assert 'iptv_transcode_oldest_job_age_seconds{status="queued"} 0.0' in exposition()
