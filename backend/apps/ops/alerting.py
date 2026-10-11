"""Backup alerts through the M13 alert path (ADR-0020, ADR-0018).

With the monitoring overlay on, Prometheus watches the backup metrics (`apps.ops.metrics`)
and its rules alert as for anything else. Without it, nothing would notice a failed
backup, so Django sends the same alerts itself, through `apps.notifications.alerts`
(the recipients and Telegram chat of Settings → Monitoring and Notifications):

- `BackupFailed` and `RestoreDrillFailed` when a run fails, resolved by the next success;
- `BackupStale` when no database backup succeeded for 26 hours;
- `RestoreDrillStale` when no drill passed for 8 days (SPEC §17: within 7 days);
- `BackupAgentDown` when the backup service has not asked for work for 10 minutes;
- `WalArchiveFailing` when PostgreSQL fails to archive WAL (point-in-time recovery stops).

Each alert fires once and resolves once: its state lives in redis-state.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Final

import redis
from django.conf import settings
from django.db import connection
from django.utils import timezone

from apps.core.services import get_setting
from apps.core.stores import state_redis
from apps.ops import backups
from apps.ops.models import DB_KINDS, BackupKind, BackupRun

logger = logging.getLogger(__name__)

STATE_KEY: Final = "ops:alert:{name}"
RUNBOOK: Final = "docs/runbooks/alerts.md"
DB_STALE: Final = timedelta(hours=26)
DRILL_STALE: Final = timedelta(days=8)
#: A new installation gets this long before staleness alerts may fire.
GRACE: Final = timedelta(hours=6)


def django_delivers() -> bool:
    """True without the monitoring overlay: Django is then the only one watching."""
    return not settings.GRAFANA_URL


def _firing(name: str) -> bool:
    try:
        return bool(state_redis().exists(STATE_KEY.format(name=name)))
    except redis.RedisError:
        return False


def _set_firing(name: str, *, firing: bool) -> None:
    key = STATE_KEY.format(name=name)
    try:
        if firing:
            state_redis().set(key, timezone.now().isoformat())
        else:
            state_redis().delete(key)
    except redis.RedisError:
        logger.warning("ops: could not store the state of alert %s", name)


def _payload(  # noqa: PLR0913 (one alert's fields, keyword-only)
    *, name: str, severity: str, status: str, summary: str, description: str, anchor: str
) -> dict[str, Any]:
    now = timezone.now().isoformat()
    return {
        "status": status,
        "commonLabels": {"alertname": name, "severity": severity},
        "externalURL": "",
        "alerts": [
            {
                "status": status,
                "labels": {"alertname": name, "severity": severity, "service": "backup"},
                "annotations": {
                    "summary": summary,
                    "description": description,
                    "runbook": f"{RUNBOOK}#{anchor}",
                },
                "startsAt": now,
                "endsAt": now if status == "resolved" else "",
                "generatorURL": "",
            }
        ],
    }


def transition(  # noqa: PLR0913
    name: str, *, firing: bool, severity: str, summary: str, description: str, anchor: str
) -> bool:
    """Send the alert when its state changes; True when something was sent."""
    if firing == _firing(name):
        return False
    _set_firing(name, firing=firing)
    if not django_delivers():
        return False
    from apps.notifications import alerts  # noqa: PLC0415 (notifications imports the registry)

    status = "firing" if firing else "resolved"
    outcome = alerts.deliver(
        _payload(
            name=name,
            severity=severity,
            status=status,
            summary=summary,
            description=description,
            anchor=anchor,
        )
    )
    logger.info("ops.alert", extra={"alert": name, "status": status, "channels": outcome.channels})
    return True


def _alert_name(run: BackupRun) -> str:
    return "RestoreDrillFailed" if run.kind == BackupKind.DRILL else "BackupFailed"


def run_failed(run: BackupRun) -> None:
    from apps.ops import metrics  # noqa: PLC0415

    metrics.BACKUP_RUNS.labels(kind=run.kind, result="failed").inc()
    name = _alert_name(run)
    first_line = (run.error.splitlines() or [""])[0][:300]
    transition(
        name,
        firing=True,
        severity="critical",
        summary=f"{run.get_kind_display()} failed",
        description=first_line or "See the admin's Backups page.",
        anchor=name.lower(),
    )


def run_succeeded(run: BackupRun) -> None:
    from apps.ops import metrics  # noqa: PLC0415

    metrics.BACKUP_RUNS.labels(kind=run.kind, result="succeeded").inc()
    if run.kind == BackupKind.CHECK:
        return
    name = _alert_name(run)
    if run.kind == BackupKind.DRILL or run.kind in DB_KINDS:
        transition(
            name,
            firing=False,
            severity="critical",
            summary=f"{run.get_kind_display()} succeeded again",
            description="The last run succeeded.",
            anchor=name.lower(),
        )


def wal_archive_failing() -> bool:
    """PostgreSQL's archiver failed more recently than it succeeded."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT failed_count, last_failed_time, last_archived_time FROM pg_stat_archiver"
        )
        row = cursor.fetchone()
    if not row:
        return False
    failed_count, last_failed, last_archived = row
    if not failed_count or last_failed is None:
        return False
    return last_archived is None or last_failed > last_archived


def _older(moment: datetime | None, limit: timedelta, now: datetime) -> bool:
    return moment is None or now - moment > limit


def check(now: datetime | None = None) -> list[str]:
    """Evaluate the standing conditions; returns the alerts now firing."""
    now = now or timezone.now()
    firing: list[str] = []
    enabled = bool(get_setting("backups.enabled"))
    agent = backups.agent_state()
    first = BackupRun.objects.order_by("created_at").values_list("created_at", flat=True).first()
    settled = first is not None and now - first > GRACE

    agent_down = enabled and agent.seen_at is not None and not agent.healthy
    transition(
        "BackupAgentDown",
        firing=agent_down,
        severity="critical",
        summary="The backup service is not running",
        description="It has not asked for work for 10 minutes: no backups or drills run.",
        anchor="backupagentdown",
    )
    last_db = backups.last_success(DB_KINDS)
    db_stale = (
        enabled and settled and _older(last_db.finished_at if last_db else None, DB_STALE, now)
    )
    transition(
        "BackupStale",
        firing=db_stale,
        severity="critical",
        summary="No database backup in over 26 hours",
        description="The nightly backup did not succeed. Look at the Backups page.",
        anchor="backupstale",
    )
    drill_on = enabled and bool(get_setting("backups.drill_enabled"))
    last_drill = backups.last_success([BackupKind.DRILL])
    drill_stale = (
        drill_on
        and settled
        and _older(last_drill.finished_at if last_drill else None, DRILL_STALE, now)
    )
    transition(
        "RestoreDrillStale",
        firing=drill_stale,
        severity="warning",
        summary="No restore drill passed in 8 days",
        description="The production checklist needs a passed drill within 7 days.",
        anchor="restoredrillstale",
    )
    try:
        archive_failing = enabled and bool(agent.status.get("archiving")) and wal_archive_failing()
    except Exception:  # the database is down: other alerts say so
        archive_failing = False
    transition(
        "WalArchiveFailing",
        firing=archive_failing,
        severity="critical",
        summary="PostgreSQL cannot archive WAL",
        description="Point-in-time recovery has stopped and WAL is piling up on the database disk.",
        anchor="walarchivefailing",
    )
    for name, state in (
        ("BackupAgentDown", agent_down),
        ("BackupStale", db_stale),
        ("RestoreDrillStale", drill_stale),
        ("WalArchiveFailing", archive_failing),
    ):
        if state:
            firing.append(name)
    return firing
