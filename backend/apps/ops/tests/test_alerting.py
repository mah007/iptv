"""Backup alerts Django sends itself without the monitoring overlay (apps/ops/alerting.py)."""

from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone

from apps.core.services import set_setting
from apps.ops import alerting, backups
from apps.ops.models import BackupKind, BackupRun, RunStatus, RunTrigger

pytestmark = pytest.mark.django_db


def finished(kind: str, *, days_ago: float, status: str = RunStatus.SUCCEEDED) -> BackupRun:
    moment = timezone.now() - timedelta(days=days_ago)
    run = backups.queue(kind, trigger=RunTrigger.SCHEDULE)
    BackupRun.objects.filter(pk=run.pk).update(status=status, finished_at=moment, created_at=moment)
    return run


def names(delivered: list[dict[str, Any]], status: str = "firing") -> list[str]:
    return [item["commonLabels"]["alertname"] for item in delivered if item["status"] == status]


def test_stale_backups_and_drills_alert_once_and_resolve(
    delivered: list[dict[str, Any]],
    agent_seen: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_seen()
    monkeypatch.setattr(alerting, "wal_archive_failing", lambda: False)
    finished(BackupKind.DB_FULL, days_ago=3)
    finished(BackupKind.DRILL, days_ago=10)
    assert sorted(alerting.check()) == ["BackupStale", "RestoreDrillStale"]
    assert sorted(names(delivered)) == ["BackupStale", "RestoreDrillStale"]
    alerting.check()
    assert len(delivered) == 2  # once
    finished(BackupKind.DB_DIFF, days_ago=0.1)
    finished(BackupKind.DRILL, days_ago=0.1)
    assert alerting.check() == []
    assert sorted(names(delivered, "resolved")) == ["BackupStale", "RestoreDrillStale"]
    payload = delivered[0]["alerts"][0]
    assert payload["annotations"]["runbook"] == "docs/runbooks/alerts.md#backupstale"


def test_a_new_installation_gets_a_grace_period(
    delivered: list[dict[str, Any]], agent_seen: Callable[..., None]
) -> None:
    agent_seen()
    backups.queue(BackupKind.DB_FULL, trigger=RunTrigger.INITIAL)
    assert alerting.check() == []
    assert delivered == []


def test_a_silent_backup_service_alerts(delivered: list[dict[str, Any]]) -> None:
    backups.record_agent_status({})
    import json  # noqa: PLC0415

    from apps.core.stores import state_redis  # noqa: PLC0415

    old = (timezone.now() - timedelta(minutes=30)).isoformat()
    state_redis().set(backups.AGENT_KEY, json.dumps({"received_at": old, "status": {}}))
    assert "BackupAgentDown" in alerting.check()
    set_setting("backups.enabled", False, actor=None)
    assert alerting.check() == []
    assert names(delivered, "resolved") == ["BackupAgentDown"]


def test_wal_archive_failures_alert(
    delivered: list[dict[str, Any]],
    agent_seen: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_seen()
    monkeypatch.setattr(alerting, "wal_archive_failing", lambda: True)
    assert "WalArchiveFailing" in alerting.check()


def test_the_archiver_check_reads_postgres() -> None:
    assert alerting.wal_archive_failing() in (True, False)


def test_with_the_overlay_prometheus_alerts_instead(
    settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from apps.notifications import alerts  # noqa: PLC0415

    settings.GRAFANA_URL = "http://grafana.localhost"
    sent: list[Any] = []
    monkeypatch.setattr(alerts, "deliver", sent.append)
    assert (
        alerting.transition(
            "BackupStale",
            firing=True,
            severity="critical",
            summary="s",
            description="d",
            anchor="a",
        )
        is False
    )
    assert sent == []
