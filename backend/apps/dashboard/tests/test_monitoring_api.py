"""The admin's Monitoring page API (ADR-0018): status and the test alert."""

from typing import Any

import pytest
from django.conf import settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.conftest import AdminFactory
from apps.core import metrics as core_metrics
from apps.core.services import set_setting
from apps.dashboard import metrics

pytestmark = pytest.mark.django_db

ADMIN = {"host": settings.ADMIN_HOST}
STATUS = "/api/v1/admin/monitoring"
TEST_ALERT = "/api/v1/admin/monitoring/test-alert"


@pytest.fixture(autouse=True)
def _overlay(settings: Any) -> None:
    settings.GRAFANA_URL = "http://grafana.localhost"
    settings.MONITORING_DEPLOYMENT = {
        "prometheus_retention": "30d",
        "prometheus_retention_size": "10GB",
        "loki_retention": "720h",
    }
    settings.ALERT_FALLBACK = {"email": True, "telegram": False}
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    core_metrics.FIRE_DRILL.publish({})


def api(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user)
    return client


def test_the_status_shows_delivery_and_the_values_that_need_a_restart(owner: User) -> None:
    set_setting("monitoring.alert_emails", "a@example.com, b@example.com", actor=None)
    body = api(owner).get(STATUS, headers=ADMIN).json()
    assert body == {
        "enabled": True,
        "grafana_url": "http://grafana.localhost",
        "alerts_enabled": True,
        "email_recipients": 2,
        "email_configured": True,
        "telegram_configured": False,
        "fire_drill_active": False,
        "deployment": {
            "prometheus_retention": "30d",
            "prometheus_retention_size": "10GB",
            "loki_retention": "720h",
            "fallback_email": True,
            "fallback_telegram": False,
        },
    }


def test_the_status_needs_monitoring_view(make_admin: AdminFactory) -> None:
    assert api(make_admin("support")).get(STATUS, headers=ADMIN).status_code == 403
    watcher = make_admin(permissions=["monitoring.view"])
    assert api(watcher).get(STATUS, headers=ADMIN).status_code == 200


def test_a_test_alert_runs_the_fire_drill_and_is_audited(owner: User) -> None:
    response = api(owner).post(TEST_ALERT, {"minutes": 5}, format="json", headers=ADMIN)
    assert response.status_code == 200
    assert response.json()["active"] is True
    assert metrics.fire_drill_active()
    assert api(owner).get(STATUS, headers=ADMIN).json()["fire_drill_active"] is True
    entry = AuditLog.objects.get(action="monitoring.fire_drill")
    assert entry.actor == owner
    assert entry.after == {"step": "start", "minutes": 5}

    stopped = api(owner).post(TEST_ALERT, {"stop": True}, format="json", headers=ADMIN)
    assert stopped.json() == {"active": False, "until": None}
    assert not metrics.fire_drill_active()


def test_a_test_alert_needs_settings_edit(make_admin: AdminFactory) -> None:
    watcher = make_admin(permissions=["monitoring.view"])
    assert api(watcher).post(TEST_ALERT, {}, format="json", headers=ADMIN).status_code == 403
    editor = make_admin(permissions=["settings.edit"])
    assert api(editor).post(TEST_ALERT, {}, format="json", headers=ADMIN).status_code == 200


@pytest.mark.parametrize("minutes", [0, 61, "x"])
def test_the_length_is_checked(owner: User, minutes: object) -> None:
    response = api(owner).post(TEST_ALERT, {"minutes": minutes}, format="json", headers=ADMIN)
    assert response.status_code == 400


def test_without_the_overlay_there_is_no_test_alert(owner: User, settings: Any) -> None:
    settings.GRAFANA_URL = ""
    response = api(owner).post(TEST_ALERT, {}, format="json", headers=ADMIN)
    assert response.status_code == 404
    assert not metrics.fire_drill_active()
    assert api(owner).get(STATUS, headers=ADMIN).json()["enabled"] is False
