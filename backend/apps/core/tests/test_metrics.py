"""Prometheus metrics (SPEC §14): internal only, multiprocess-safe, snapshot gauges."""

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest import mock

import pytest
import redis
from django.conf import settings
from django.core.cache import cache
from django.test import Client
from prometheus_client import generate_latest

from apps.core import metrics
from apps.core.metrics import SNAPSHOT_GAUGES, SnapshotGauge, metrics_registry

SPEC_METRICS = (
    "iptv_active_streams",
    "iptv_stream_starts_total",
    "iptv_concurrency_rejections_total",
    "iptv_kicks_total",
    "iptv_xtream_requests_total",
    "iptv_xtream_latency_seconds",
    "iptv_transcode_jobs",
    "iptv_transcode_speed_ratio",
    "iptv_scan_files_total",
    "iptv_match_confidence_bucket",
    "iptv_review_queue_open",
    "iptv_subscriptions",
    "iptv_payments_total",
    "iptv_notifications_total",
    "iptv_metadata_api_requests_total",
)


@pytest.fixture
def clean_snapshots() -> None:
    for gauge in SNAPSHOT_GAUGES:
        cache.delete(gauge.cache_key)


def exposition() -> str:
    return generate_latest(metrics_registry()).decode()


def test_metrics_are_served_on_the_internal_host(clean_snapshots: None) -> None:
    metrics.SCAN_FILES.labels(result="new").inc()
    metrics.MATCH_CONFIDENCE.observe(0.9)
    response = Client().get("/metrics", headers={"host": "web"})
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/plain")
    assert "no-cache" in response["Cache-Control"]
    body = response.content.decode()
    for name in SPEC_METRICS:
        assert f"# TYPE {name.removesuffix('_total').removesuffix('_bucket')}" in body, name
    assert 'iptv_scan_files_total{result="new"}' in body
    assert "iptv_match_confidence_bucket" in body
    # django-prometheus request metrics, labelled by view, never by path.
    assert "django_http_requests_total_by_method_total" in body


@pytest.mark.parametrize(
    "host", [settings.API_HOST, settings.TV_HOST, settings.ADMIN_HOST, settings.APP_HOST]
)
def test_metrics_are_unreachable_from_public_hosts(host: str) -> None:
    assert Client().get("/metrics", headers={"host": host}).status_code == 404


def test_snapshot_gauges_export_the_latest_published_samples(clean_snapshots: None) -> None:
    metrics.SUBSCRIPTIONS.publish({("active",): 12, ("grace",): 3})
    metrics.REVIEW_QUEUE_OPEN.publish({(): 4})
    body = exposition()
    assert 'iptv_subscriptions{status="active"} 12.0' in body
    assert 'iptv_subscriptions{status="grace"} 3.0' in body
    assert "iptv_review_queue_open 4.0" in body
    # A new snapshot replaces the old one.
    metrics.SUBSCRIPTIONS.publish({("expired",): 1})
    body = exposition()
    assert 'status="active"' not in body
    assert 'iptv_subscriptions{status="expired"} 1.0' in body


def test_snapshots_expire_with_their_ttl(clean_snapshots: None) -> None:
    gauge = SnapshotGauge("iptv_test_snapshot", "Test.", ("x",), ttl_s=60)
    with mock.patch.object(cache, "set") as cache_set:
        gauge.publish({("a",): 1})
    cache_set.assert_called_once_with(gauge.cache_key, [(["a"], 1.0)], timeout=60)


def test_snapshot_labels_must_match() -> None:
    with pytest.raises(ValueError, match="expects labels"):
        metrics.SUBSCRIPTIONS.publish({("active", "extra"): 1})


def test_an_unavailable_cache_exports_empty_snapshots() -> None:
    with mock.patch.object(cache, "get", side_effect=redis.ConnectionError):
        (family,) = metrics.SUBSCRIPTIONS.collect()
    assert family.samples == []


def test_multiprocess_mode_aggregates_worker_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, clean_snapshots: None
) -> None:
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))
    metrics.SUBSCRIPTIONS.publish({("active",): 2})
    body = exposition()
    assert 'iptv_subscriptions{status="active"} 2.0' in body
    # Only multiprocess files and snapshots: this process's in-memory registry is not read.
    assert "iptv_scan_files_total" not in body


def _load_gunicorn_conf() -> ModuleType:
    path = Path(settings.BASE_DIR) / "config" / "gunicorn_conf.py"
    spec = importlib.util.spec_from_file_location("gunicorn_conf_under_test", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_gunicorn_starts_with_a_fresh_metrics_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    conf = _load_gunicorn_conf()
    assert conf.worker_class == "uvicorn_worker.UvicornWorker"
    assert conf.accesslog is None
    assert conf.control_socket_disable is True
    target = tmp_path / "prometheus"
    target.mkdir()
    (target / "counter_1.db").write_bytes(b"stale")
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(target))
    conf.on_starting(server=None)
    assert target.is_dir()
    assert list(target.iterdir()) == []


def test_gunicorn_marks_dead_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    conf = _load_gunicorn_conf()
    worker = mock.Mock(pid=4242)
    with mock.patch("prometheus_client.multiprocess.mark_process_dead") as mark_dead:
        conf.child_exit(server=None, worker=worker)
    mark_dead.assert_called_once_with(4242)
