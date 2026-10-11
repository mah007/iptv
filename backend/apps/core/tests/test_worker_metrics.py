"""Celery worker metrics (ADR-0018): task outcomes and the worker's own endpoint."""

from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from celery import signals
from prometheus_client import REGISTRY, generate_latest

from apps.core import worker_metrics
from apps.core.tasks import heartbeat
from apps.core.worker_metrics import OTHER_TASK, state_label, task_label
from config import celery as celery_config


def sample(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


@pytest.fixture(autouse=True)
def _reset_server_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker_metrics, "_server_started", False)
    worker_metrics._started.clear()


def test_task_names_are_bounded() -> None:
    assert task_label("apps.library.tasks.scan_library") == "apps.library.tasks.scan_library"
    assert task_label("celery.chord_unlock") == OTHER_TASK
    assert task_label(None) == OTHER_TASK
    assert task_label("apps." + "x" * 200) == OTHER_TASK


@pytest.mark.parametrize(
    ("state", "label"),
    [("SUCCESS", "succeeded"), ("FAILURE", "failed"), ("RETRY", "retried"), ("REVOKED", "other")],
)
def test_states(state: str, label: str) -> None:
    assert state_label(state) == label


def test_a_finished_task_is_counted_and_timed() -> None:
    task = SimpleNamespace(name="apps.core.tasks.heartbeat")
    before = sample("iptv_celery_tasks_total", task=task.name, state="succeeded")
    timed = sample("iptv_celery_task_duration_seconds_count", task=task.name)
    worker_metrics.on_task_prerun(task_id="t-1", task=task)
    worker_metrics.on_task_postrun(task_id="t-1", task=task, state="SUCCESS")
    assert sample("iptv_celery_tasks_total", task=task.name, state="succeeded") == before + 1
    assert sample("iptv_celery_task_duration_seconds_count", task=task.name) == timed + 1
    assert "t-1" not in worker_metrics._started


def test_a_task_without_a_start_is_counted_but_not_timed() -> None:
    task = SimpleNamespace(name="vendor.task")
    before = sample("iptv_celery_tasks_total", task=OTHER_TASK, state="failed")
    timed = sample("iptv_celery_task_duration_seconds_count", task=OTHER_TASK)
    worker_metrics.on_task_postrun(task_id="unknown", task=task, state="FAILURE")
    assert sample("iptv_celery_tasks_total", task=OTHER_TASK, state="failed") == before + 1
    assert sample("iptv_celery_task_duration_seconds_count", task=OTHER_TASK) == timed


def test_celery_signals_reach_the_handlers() -> None:
    """config/celery.py connects the signals; the handlers import lazily."""
    task = heartbeat
    before = sample("iptv_celery_tasks_total", task=task.name, state="retried")
    signals.task_prerun.send(sender=task, task_id="t-2", task=task, args=(), kwargs={})
    signals.task_postrun.send(
        sender=task, task_id="t-2", task=task, args=(), kwargs={}, retval=None, state="RETRY"
    )
    assert sample("iptv_celery_tasks_total", task=task.name, state="retried") == before + 1
    # Worker-level signals carry a worker as sender (Celery's Django fixup listens too),
    # so their receivers are called directly.
    with mock.patch.object(worker_metrics, "on_worker_ready") as ready:
        celery_config._metrics_worker_ready(sender=None)
    ready.assert_called_once()
    with mock.patch.object(worker_metrics, "on_worker_process_shutdown") as shutdown:
        celery_config._metrics_worker_process_shutdown(pid=7, exitcode=0)
    shutdown.assert_called_once_with(pid=7, exitcode=0)


@pytest.mark.parametrize(
    ("argv", "port", "prepared"),
    [
        (["/opt/venv/bin/celery", "-A", "config", "worker", "--queues", "default"], "9100", True),
        (["celery", "-A", "config", "worker"], "", False),
        (["celery", "-A", "config", "inspect", "ping"], "9100", False),
        (["python", "manage.py", "shell"], "9100", False),
        ([], "9100", False),
    ],
)
def test_only_a_celery_worker_switches_to_multiprocess_mode(
    tmp_path: Path, argv: list[str], port: str, prepared: bool
) -> None:
    target = tmp_path / "prom"
    target.mkdir()
    (target / "counter_12.db").write_bytes(b"stale")
    environ = {"CELERY_METRICS_PORT": port, "CELERY_METRICS_DIR": str(target)}
    result = celery_config.prepare_worker_metrics(argv, environ)
    if prepared:
        assert result == target
        assert environ["PROMETHEUS_MULTIPROC_DIR"] == str(target)
        assert list(target.iterdir()) == []
    else:
        assert result is None
        assert "PROMETHEUS_MULTIPROC_DIR" not in environ
        assert (target / "counter_12.db").exists()


def test_without_multiprocess_mode_the_process_registry_is_served(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)
    assert worker_metrics.registry() is REGISTRY


def test_the_server_starts_once_on_the_configured_port(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))
    monkeypatch.setenv("CELERY_METRICS_PORT", "9123")
    with mock.patch.object(worker_metrics, "start_http_server") as start:
        worker_metrics.on_worker_ready()
        worker_metrics.on_worker_ready()
    start.assert_called_once()
    assert start.call_args.args == (9123,)
    assert start.call_args.kwargs["addr"] == "0.0.0.0"  # noqa: S104 - the backend network only
    assert start.call_args.kwargs["registry"] is not REGISTRY


def test_no_port_no_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CELERY_METRICS_PORT", raising=False)
    with mock.patch.object(worker_metrics, "start_http_server") as start:
        worker_metrics.on_worker_ready()
    start.assert_not_called()


def test_a_busy_port_is_logged_not_raised(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)
    monkeypatch.setenv("CELERY_METRICS_PORT", "9100")
    with mock.patch.object(worker_metrics, "start_http_server", side_effect=OSError("in use")):
        worker_metrics.on_worker_ready()
    assert worker_metrics._server_started is False
    assert "not available" in caplog.text


def test_multiprocess_samples_are_summed_across_pool_processes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Two pool processes' files, read through the worker's registry."""
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))
    from prometheus_client import values  # noqa: PLC0415

    for pid in (101, 102):
        value = values.MultiProcessValue(lambda pid=pid: pid)(
            "counter",
            "iptv_scan_files",
            "iptv_scan_files_total",
            ("result",),
            ("new",),
            "Library scan file outcomes.",
        )
        value.inc(2)
    body = generate_latest(worker_metrics.registry()).decode()
    assert 'iptv_scan_files_total{result="new"} 4.0' in body


def test_a_dead_pool_process_is_marked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))
    with mock.patch("prometheus_client.multiprocess.mark_process_dead") as mark_dead:
        worker_metrics.on_worker_process_shutdown(pid=4242)
    mark_dead.assert_called_once_with(4242)
    monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR")
    with mock.patch("prometheus_client.multiprocess.mark_process_dead") as mark_dead:
        worker_metrics.on_worker_process_shutdown(pid=4242)
    mark_dead.assert_not_called()
