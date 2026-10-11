"""Backups seen from Django (apps/ops/backups.py): the schedule, the agent's jobs, the
drill's verdict and what the admin sees."""

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import pytest
from django.utils import timezone as dj_timezone

from apps.audit.models import AuditLog
from apps.core.services import set_setting
from apps.ops import backups
from apps.ops.models import BackupKind, BackupRun, RunStatus, RunTrigger
from apps.ops.tests.conftest import at

pytestmark = pytest.mark.django_db


class Clock:
    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment

    def advance(self, **delta: float) -> None:
        self.moment += timedelta(**delta)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    """django.utils.timezone.now under the test's control (auto_now_add included)."""
    fake = Clock(at("2026-10-07T12:00:00"))  # a Wednesday
    monkeypatch.setattr(dj_timezone, "now", fake)
    return fake


def kinds(runs: list[BackupRun]) -> list[str]:
    return [run.kind for run in runs]


def succeed(run: BackupRun, **detail: Any) -> BackupRun:
    run.status = RunStatus.RUNNING
    run.started_at = dj_timezone.now()
    run.save()
    return backups.record_result(run, {"status": "succeeded", "detail": detail, "duration_s": 1.5})


# --- Slots --------------------------------------------------------------------------------


def test_daily_and_weekly_slots_follow_the_time_zone() -> None:
    now = at("2026-10-07T01:00:00")  # 04:00 in Riyadh, Wednesday
    assert backups.last_daily_slot(now, "03:30", "Asia/Riyadh") == at("2026-10-07T00:30:00")
    assert backups.last_daily_slot(now, "05:00", "Asia/Riyadh") == at("2026-10-06T02:00:00")
    assert backups.next_daily_slot(now, "03:30", "Asia/Riyadh") == at("2026-10-08T00:30:00")
    monday = backups.last_weekly_slot(now, 0, "05:00", "UTC")
    assert monday == at("2026-10-05T05:00:00")
    assert backups.next_weekly_slot(now, 0, "05:00", "UTC") == at("2026-10-12T05:00:00")


# --- The schedule ---------------------------------------------------------------------


def test_nothing_is_queued_without_a_backup_service(clock: Clock) -> None:
    assert backups.schedule_due() == []


def test_nothing_is_queued_while_backups_are_off(
    clock: Clock, agent_seen: Callable[..., None]
) -> None:
    agent_seen()
    set_setting("backups.enabled", False, actor=None)
    assert backups.schedule_due() == []


def test_a_new_installation_gets_a_full_backup_at_once(
    clock: Clock, agent_seen: Callable[..., None]
) -> None:
    agent_seen()
    runs = backups.schedule_due()
    assert kinds(runs) == [BackupKind.DB_FULL, BackupKind.FILES]
    assert {run.trigger for run in runs} == {RunTrigger.INITIAL}
    assert backups.schedule_due() == []  # already queued


def test_the_nightly_backup_and_the_first_drill(
    clock: Clock, agent_seen: Callable[..., None]
) -> None:
    agent_seen()
    for run in backups.schedule_due():
        succeed(run)
    # Right after the first backup: the first drill (SPEC §17: a drill within 7 days).
    first = backups.schedule_due()
    assert kinds(first) == [BackupKind.DRILL]
    assert first[0].trigger == RunTrigger.INITIAL
    succeed(first[0], migrations=[["core", "0001_postgres_extensions", "2026-10-01"]])
    clock.advance(hours=1)
    assert backups.schedule_due() == []
    # The next night, 03:30 UTC (Thursday): a differential backup.
    clock.moment = at("2026-10-08T03:31:00")
    nightly = backups.schedule_due()
    assert kinds(nightly) == [BackupKind.DB_DIFF, BackupKind.FILES]
    assert backups.schedule_due() == []


def test_the_full_backup_day_and_a_missed_night(
    clock: Clock, agent_seen: Callable[..., None]
) -> None:
    agent_seen()
    set_setting("backups.full_weekday", 3, actor=None)  # Thursday
    for run in backups.schedule_due():
        succeed(run)
    clock.moment = at("2026-10-08T22:00:00")  # the stack was down at 03:30; still Thursday
    nightly = backups.schedule_due()
    assert BackupKind.DB_FULL in kinds(nightly)
    assert nightly[1].params == {"prune": True}


def test_the_weekly_drill_and_the_hourly_offsite_copy(
    clock: Clock, agent_seen: Callable[..., None], s3: None
) -> None:
    agent_seen()
    for run in backups.schedule_due():
        assert run.kind != BackupKind.OFFSITE or run.params["full"] is True
        succeed(run)
    BackupRun.objects.filter(kind=BackupKind.OFFSITE).delete()
    drill_and_copy = backups.schedule_due()
    assert set(kinds(drill_and_copy)) == {BackupKind.DRILL, BackupKind.OFFSITE}
    offsite = next(run for run in drill_and_copy if run.kind == BackupKind.OFFSITE)
    assert offsite.params == {"full": False}
    for run in drill_and_copy:
        succeed(run)
    clock.advance(minutes=30)
    assert backups.schedule_due() == []
    clock.advance(minutes=31)
    assert kinds(backups.schedule_due()) == [BackupKind.OFFSITE]
    # The next Monday at 05:00: the weekly drill.
    BackupRun.objects.filter(status=RunStatus.QUEUED).delete()
    clock.moment = at("2026-10-12T05:01:00")
    assert BackupKind.DRILL in kinds(backups.schedule_due())


@pytest.fixture
def s3() -> None:
    for key, value in (
        ("backups.s3_enabled", True),
        ("backups.s3_endpoint", "https://s3.example.com"),
        ("backups.s3_bucket", "backups"),
        ("backups.s3_access_key", "AK" + "IA" + "TESTVALUE"),
        ("backups.s3_secret_key", "secret-key-value"),
    ):
        set_setting(key, value, actor=None)


def test_the_agent_config_carries_the_bucket_only_when_complete(s3: None) -> None:
    config = backups.agent_config()
    assert config["enabled"] is True
    assert config["s3"]["secret_key"] == "secret-key-value"  # noqa: S105
    assert config["s3"]["prefix"] == "smart-iptv"
    set_setting("backups.s3_secret_key", "", actor=None)
    assert backups.agent_config()["s3"] is None


def test_the_drill_counts_the_tables_that_matter() -> None:
    tables = backups.drill_tables()
    assert tables[0] == "django_migrations"
    assert {"accounts_user", "billing_payment", "audit_auditlog", "core_setting"} <= set(tables)


# --- Jobs -------------------------------------------------------------------------------


def test_one_job_at_a_time_oldest_first(clock: Clock) -> None:
    first = backups.queue(BackupKind.DB_FULL, trigger=RunTrigger.MANUAL)
    clock.advance(seconds=1)
    backups.queue(BackupKind.FILES, trigger=RunTrigger.MANUAL)
    claimed = backups.claim_next()
    assert claimed == first
    assert claimed is not None
    assert claimed.status == RunStatus.RUNNING
    assert backups.claim_next() is None  # one is running
    assert backups.job_payload(claimed) == {"id": str(first.pk), "kind": "db_full", "params": {}}


def test_a_drill_job_lists_its_tables() -> None:
    run = backups.queue(BackupKind.DRILL, trigger=RunTrigger.MANUAL)
    assert "accounts_user" in backups.job_payload(run)["tables"]


def test_a_failure_is_stored_redacted_and_alerts(delivered: list[dict[str, Any]]) -> None:
    run = backups.queue(BackupKind.DB_FULL, trigger=RunTrigger.SCHEDULE)
    backups.claim_next()
    run.refresh_from_db()
    backups.record_result(
        run, {"status": "failed", "error": "redis://:hunter2secret@redis-state:6379 refused"}
    )
    run.refresh_from_db()
    assert run.status == RunStatus.FAILED
    assert "hunter2secret" not in run.error
    assert delivered[0]["commonLabels"] == {"alertname": "BackupFailed", "severity": "critical"}
    assert delivered[0]["status"] == "firing"
    # The next success resolves it.
    run2 = backups.queue(BackupKind.DB_DIFF, trigger=RunTrigger.SCHEDULE)
    backups.claim_next()
    run2.refresh_from_db()
    backups.record_result(run2, {"status": "succeeded", "detail": {"label": "x"}, "size_bytes": 5})
    assert delivered[-1]["status"] == "resolved"


def test_results_for_runs_not_running_are_ignored() -> None:
    run = backups.queue(BackupKind.FILES, trigger=RunTrigger.MANUAL)
    backups.record_result(run, {"status": "succeeded"})
    run.refresh_from_db()
    assert run.status == RunStatus.QUEUED
    backups.claim_next()
    run.refresh_from_db()
    backups.record_result(run, {"status": "weird"})
    run.refresh_from_db()
    assert run.status == RunStatus.RUNNING


def test_lost_runs_fail(clock: Clock, delivered: list[dict[str, Any]]) -> None:
    run = backups.queue(BackupKind.DB_FULL, trigger=RunTrigger.SCHEDULE)
    backups.claim_next()
    stale = backups.queue(BackupKind.CHECK, trigger=RunTrigger.MANUAL)
    clock.advance(hours=25)
    assert backups.expire_lost_runs() == 2
    run.refresh_from_db()
    stale.refresh_from_db()
    assert run.status == stale.status == RunStatus.FAILED
    assert "stopped answering" in run.error


# --- The drill's verdict ---------------------------------------------------------------


def live_migration_rows() -> list[list[str]]:
    from django.db import connection  # noqa: PLC0415

    with connection.cursor() as cursor:
        cursor.execute("SELECT app, name, applied::text FROM django_migrations")
        return [list(row) for row in cursor.fetchall()]


def test_a_complete_restore_passes() -> None:
    recovered = dj_timezone.now()
    detail = {
        "migrations": live_migration_rows(),
        "counts": {"django_migrations": len(live_migration_rows())},
        "missing_tables": [],
        "recovered_to": recovered.isoformat(),
        "verify_ok": True,
    }
    verdict = backups.judge_drill(detail, recovered + timedelta(minutes=3))
    assert verdict["problems"] == []
    assert verdict["missing_migrations"] == []
    assert verdict["live_counts"]["django_migrations"] > 0
    assert verdict["data_age_s"] == 180.0


def test_a_restore_missing_migrations_rows_or_freshness_fails(
    make_admin: Callable[..., Any],
) -> None:
    make_admin("owner")
    rows = live_migration_rows()
    detail = {
        "migrations": rows[:-3],
        "counts": {"accounts_user": 0},
        "missing_tables": ["billing_payment"],
        "recovered_to": dj_timezone.now().isoformat(),
        "verify_ok": False,
    }
    verdict = backups.judge_drill(detail, dj_timezone.now())
    problems = " | ".join(verdict["problems"])
    assert "3 migrations missing" in problems
    assert len(verdict["missing_migrations"]) == 3
    assert "accounts_user" in problems
    assert "billing_payment" in problems
    assert "verify" in problems
    stale = backups.judge_drill(
        {"migrations": rows, "recovered_to": "2026-01-01 00:00:00+00"}, dj_timezone.now()
    )
    assert stale["problems"] == [
        "the restored data is more than a day old: WAL archiving may have stopped"
    ]
    assert backups.judge_drill({"migrations": []}, dj_timezone.now())["problems"][0] == (
        "no migrations in the restored database"
    )


def test_a_drill_with_problems_fails_and_alerts(delivered: list[dict[str, Any]]) -> None:
    run = backups.queue(BackupKind.DRILL, trigger=RunTrigger.SCHEDULE)
    backups.claim_next()
    run.refresh_from_db()
    backups.record_result(
        run, {"status": "succeeded", "detail": {"migrations": []}, "duration_s": 3}
    )
    run.refresh_from_db()
    assert run.status == RunStatus.FAILED
    assert run.detail["verdict"]["problems"]
    assert delivered[0]["commonLabels"]["alertname"] == "RestoreDrillFailed"


# --- What the admin sees ----------------------------------------------------------------


def test_the_summary(clock: Clock, agent_seen: Callable[..., None]) -> None:
    agent_seen(disk={"total_bytes": 100, "free_bytes": 40}, db_backups=[{"label": "x"}])
    for run in backups.schedule_due():
        succeed(run)
    summary = backups.summary()
    assert summary["enabled"] is True
    assert summary["agent"]["healthy"] is True
    assert summary["agent"]["disk_free_bytes"] == 40
    assert summary["latest"]["db"].kind == BackupKind.DB_FULL
    assert summary["next_backup_at"] == at("2026-10-08T03:30:00")
    assert summary["next_drill_at"] == at("2026-10-12T05:00:00")
    assert summary["s3"]["configured"] is False
    assert summary["last_success_db_at"] == clock.moment


def test_the_agent_state_survives_garbage(agent_seen: Callable[..., None]) -> None:
    from apps.core.stores import state_redis  # noqa: PLC0415

    state_redis().set(backups.AGENT_KEY, b"not json")
    assert backups.agent_state().seen_at is None
    assert not backups.agent_state().healthy
    agent_seen()
    assert backups.agent_state().healthy


def test_no_audit_noise_from_the_schedule(clock: Clock, agent_seen: Callable[..., None]) -> None:
    agent_seen()
    backups.schedule_due()
    assert not AuditLog.objects.filter(action__startswith="backup.").exists()
