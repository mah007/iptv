"""Readiness against the real stores. They must be running (dev stack or CI services)."""

from unittest import mock

import pytest
from django.test import Client
from pytest_django import Settings

from apps.core import health
from apps.core.health import CheckResult, run_checks

pytestmark = pytest.mark.django_db


def test_every_dependency_is_checked() -> None:
    assert set(health.CHECKS) == {"postgres", "redis_state", "redis_cache", "meilisearch"}


def test_all_real_dependencies_are_ready() -> None:
    results = run_checks()
    failing = {name: result.error for name, result in results.items() if not result.ok}
    assert failing == {}
    assert all(result.latency_ms >= 0 for result in results.values())


def test_ready_endpoint_reports_ok() -> None:
    response = Client().get("/internal/health/ready", headers={"host": "web"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert set(body["checks"]) == set(health.CHECKS)


def test_ready_endpoint_is_503_when_a_dependency_is_down() -> None:
    down = {
        "postgres": CheckResult(ok=True, latency_ms=1.0),
        "redis_state": CheckResult(ok=False, latency_ms=2000.0, error="TimeoutError"),
    }
    with mock.patch("apps.core.views.run_checks", return_value=down):
        response = Client().get("/internal/health/ready", headers={"host": "web"})
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["checks"]["redis_state"] == {
        "ok": False,
        "latency_ms": 2000.0,
        "error": "TimeoutError",
    }


def test_failed_check_records_only_the_exception_type() -> None:
    def boom() -> None:
        msg = "redis://:secret-password@redis-state:6379"
        raise ConnectionError(msg)

    result = health._timed(boom)
    assert result == CheckResult(ok=False, latency_ms=result.latency_ms, error="ConnectionError")
    assert "secret" not in result.error


def test_meilisearch_check_refuses_non_http_urls(settings: Settings) -> None:
    settings.MEILI_URL = "file:///etc/passwd"
    with pytest.raises(health.MeilisearchUnavailableError):
        health._meilisearch()


def test_meilisearch_check_requires_available_status() -> None:
    fake = mock.MagicMock()
    fake.__enter__.return_value = mock.MagicMock()
    with (
        mock.patch("apps.core.health.urllib.request.urlopen", return_value=fake),
        mock.patch("apps.core.health.json.load", return_value={"status": "starting"}),
        pytest.raises(health.MeilisearchUnavailableError),
    ):
        health._meilisearch()
