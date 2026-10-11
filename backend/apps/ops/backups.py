"""Backups seen from Django (ADR-0020): the schedule, the agent's jobs, their results.

Django decides *when* (the admin's settings, group Backups); the agent in the `backup`
container decides nothing and asks every 30 s (`/internal/backups/agent/poll`):

- `agent_config` is what it renders `pgbackrest.conf` and restic's environment from;
- `schedule_due` (a beat task, every minute) queues the nightly backup (database,
  files, offsite), the hourly offsite copy, the weekly restore drill, the first full
  backup of a new installation and the first drill after it;
- `claim_next` hands the oldest queued run to the agent, one at a time;
- `record_result` stores what came back; a failure alerts (`apps.ops.alerting`).

The drill's verdict is Django's: the agent reports what it restored (migrations,
row counts, how far the data reaches) and `judge_drill` compares that with the live
database.
"""

import json
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final, cast
from zoneinfo import ZoneInfo

import redis
from django.conf import settings
from django.db import connection, transaction
from django.db.models import QuerySet
from django.utils import timezone

from apps.accounts.models import User
from apps.core.redaction import redact_text
from apps.core.services import get_secret, get_setting
from apps.core.stores import state_redis
from apps.ops.models import DB_KINDS, BackupKind, BackupRun, RunStatus, RunTrigger

logger = logging.getLogger(__name__)

AGENT_KEY: Final = "ops:backup:agent"
AGENT_STATUS_TTL_S: Final = 7 * 86400
#: The agent polls every 30 s; silent for this long means the service is down.
AGENT_SILENT_S: Final = 600
#: How long a run may stay `running` before it counts as lost.
RUN_LIMIT_S: Final[dict[str, int]] = {
    BackupKind.DB_FULL: 14 * 3600,
    BackupKind.DB_DIFF: 14 * 3600,
    BackupKind.FILES: 26 * 3600,
    BackupKind.OFFSITE: 26 * 3600,
    BackupKind.DRILL: 8 * 3600,
    BackupKind.CHECK: 3600,
}
MAX_ERROR_CHARS: Final = 8000
#: Restored data older than this (at the drill) means WAL archiving stopped somewhere.
DRILL_MAX_DATA_AGE: Final = timedelta(hours=26)


# --- What the agent gets ------------------------------------------------------------


def s3_target() -> dict[str, Any] | None:
    """The offsite bucket, when it is switched on and complete; None otherwise."""
    if not get_setting("backups.s3_enabled"):
        return None
    target = {
        "endpoint": str(get_setting("backups.s3_endpoint")),
        "region": str(get_setting("backups.s3_region")),
        "bucket": str(get_setting("backups.s3_bucket")),
        "prefix": str(get_setting("backups.s3_prefix")),
        "path_style": bool(get_setting("backups.s3_path_style")),
        "access_key": get_secret("backups.s3_access_key"),
        "secret_key": get_secret("backups.s3_secret_key"),
    }
    required = ("endpoint", "bucket", "access_key", "secret_key")
    if not all(target[name] for name in required):
        return None
    return target


def agent_config() -> dict[str, Any]:
    return {
        "enabled": bool(get_setting("backups.enabled")),
        "db_full_retention": int(get_setting("backups.db_full_retention")),
        "keep_daily": int(get_setting("backups.files_keep_daily")),
        "keep_weekly": int(get_setting("backups.files_keep_weekly")),
        "keep_monthly": int(get_setting("backups.files_keep_monthly")),
        "media": bool(get_setting("backups.media_enabled")),
        "s3": s3_target(),
    }


def drill_tables() -> list[str]:
    """Tables the drill counts: identity, access, money and the catalogue."""
    from django.apps import apps  # noqa: PLC0415

    labels = (
        "accounts.User",
        "accounts.Device",
        "accounts.XtreamCredential",
        "billing.Plan",
        "billing.Subscription",
        "billing.Invoice",
        "billing.Payment",
        "catalog.Movie",
        "catalog.Series",
        "catalog.Episode",
        "catalog.MediaFile",
        "library.Library",
        "live.LiveChannel",
        "core.Setting",
        "audit.AuditLog",
    )
    tables = ["django_migrations"]
    for label in labels:
        try:
            tables.append(apps.get_model(label)._meta.db_table)
        except LookupError:  # pragma: no cover - an app was renamed
            continue
    return tables


# --- The agent's status ---------------------------------------------------------------


def record_agent_status(status: Mapping[str, Any]) -> None:
    payload = {"received_at": timezone.now().isoformat(), "status": dict(status)}
    try:
        state_redis().set(AGENT_KEY, json.dumps(payload, default=str), ex=AGENT_STATUS_TTL_S)
    except redis.RedisError:
        logger.warning("ops: could not store the backup agent's status")


@dataclass(frozen=True, slots=True)
class AgentState:
    seen_at: datetime | None
    status: dict[str, Any]

    @property
    def healthy(self) -> bool:
        if self.seen_at is None:
            return False
        return (timezone.now() - self.seen_at).total_seconds() < AGENT_SILENT_S


def agent_state() -> AgentState:
    try:
        raw = cast("bytes | None", state_redis().get(AGENT_KEY))
    except redis.RedisError:
        return AgentState(None, {})
    if not raw:
        return AgentState(None, {})
    try:
        payload = json.loads(raw)
        seen = datetime.fromisoformat(payload["received_at"])
    except (ValueError, KeyError, TypeError):
        return AgentState(None, {})
    status = payload.get("status")
    return AgentState(seen, status if isinstance(status, dict) else {})


# --- The schedule ---------------------------------------------------------------------


def _clock(value: str) -> tuple[int, int]:
    hours, _, minutes = value.partition(":")
    return int(hours), int(minutes)


def last_daily_slot(now: datetime, at: str, zone: str) -> datetime:
    """The most recent `at` (HH:MM in `zone`) at or before `now`."""
    tz = ZoneInfo(zone)
    local = now.astimezone(tz)
    hours, minutes = _clock(at)
    slot = local.replace(hour=hours, minute=minutes, second=0, microsecond=0)
    if slot > local:
        slot -= timedelta(days=1)
    return slot


def last_weekly_slot(now: datetime, weekday: int, at: str, zone: str) -> datetime:
    """The most recent `weekday` (0 Monday) at `at` in `zone`, at or before `now`."""
    slot = last_daily_slot(now, at, zone)
    while slot.weekday() != weekday:
        slot -= timedelta(days=1)
    return slot


def next_daily_slot(now: datetime, at: str, zone: str) -> datetime:
    return last_daily_slot(now, at, zone) + timedelta(days=1)


def next_weekly_slot(now: datetime, weekday: int, at: str, zone: str) -> datetime:
    return last_weekly_slot(now, weekday, at, zone) + timedelta(days=7)


def _pending(kinds: Iterable[str]) -> bool:
    return BackupRun.objects.filter(
        kind__in=list(kinds), status__in=(RunStatus.QUEUED, RunStatus.RUNNING)
    ).exists()


def _scheduled_since(kinds: Iterable[str], since: datetime) -> bool:
    return BackupRun.objects.filter(
        kind__in=list(kinds),
        trigger__in=(RunTrigger.SCHEDULE, RunTrigger.INITIAL),
        created_at__gte=since,
    ).exists()


def last_success(kinds: Iterable[str]) -> BackupRun | None:
    return (
        BackupRun.objects.filter(kind__in=list(kinds), status=RunStatus.SUCCEEDED)
        .order_by("-finished_at")
        .first()
    )


def queue(
    kind: str,
    *,
    trigger: str,
    actor: User | None = None,
    params: Mapping[str, Any] | None = None,
) -> BackupRun:
    return BackupRun.objects.create(
        kind=kind,
        trigger=trigger,
        requested_by=actor,
        params=dict(params or {}),
    )


def queue_nightly(
    *, trigger: str, actor: User | None = None, full: bool = False
) -> list[BackupRun]:
    """The database (full or differential), then files, then the offsite copy."""
    runs = [
        queue(BackupKind.DB_FULL if full else BackupKind.DB_DIFF, trigger=trigger, actor=actor),
        queue(
            BackupKind.FILES,
            trigger=trigger,
            actor=actor,
            params={"prune": trigger != RunTrigger.MANUAL and _weekly_prune_due()},
        ),
    ]
    if s3_target() is not None:
        runs.append(
            queue(
                BackupKind.OFFSITE,
                trigger=trigger,
                actor=actor,
                params={"full": True, "prune": _weekly_prune_due()},
            )
        )
    return runs


def _weekly_prune_due() -> bool:
    """restic's prune rewrites packs: once a week, on the full-backup day."""
    zone = ZoneInfo(str(get_setting("backups.schedule_timezone")))
    return timezone.now().astimezone(zone).weekday() == int(get_setting("backups.full_weekday"))


def schedule_due(now: datetime | None = None) -> list[BackupRun]:
    """Queue whatever the schedule says is due; returns the new runs."""
    now = now or timezone.now()
    if not get_setting("backups.enabled"):
        return []
    agent = agent_state()
    if agent.seen_at is None:
        return []  # no backup service on this server (yet): nothing would run the jobs
    created: list[BackupRun] = []
    zone = str(get_setting("backups.schedule_timezone"))
    db_ok = last_success(DB_KINDS)

    # The first full backup of a new installation, as soon as the agent is there.
    if (
        db_ok is None
        and not _pending(DB_KINDS)
        and not _scheduled_since(DB_KINDS, now - timedelta(hours=1))
    ):
        created += queue_nightly(trigger=RunTrigger.INITIAL, full=True)
        return created

    nightly = last_daily_slot(now, str(get_setting("backups.schedule_time")), zone)
    if not _scheduled_since(DB_KINDS, nightly) and not _pending(DB_KINDS):
        full = nightly.weekday() == int(get_setting("backups.full_weekday"))
        created += queue_nightly(trigger=RunTrigger.SCHEDULE, full=full)

    if (
        get_setting("backups.drill_enabled")
        and db_ok is not None
        and not _pending([BackupKind.DRILL])
    ):
        drill_ok = last_success([BackupKind.DRILL])
        weekly = last_weekly_slot(
            now,
            int(get_setting("backups.drill_weekday")),
            str(get_setting("backups.drill_time")),
            zone,
        )
        never = drill_ok is None and not BackupRun.objects.filter(kind=BackupKind.DRILL).exists()
        if never or not _scheduled_since([BackupKind.DRILL], weekly):
            trigger = RunTrigger.INITIAL if never else RunTrigger.SCHEDULE
            created.append(queue(BackupKind.DRILL, trigger=trigger))

    if s3_target() is not None and db_ok is not None and not _pending([BackupKind.OFFSITE]):
        interval = timedelta(minutes=int(get_setting("backups.offsite_interval_minutes")))
        recent = BackupRun.objects.filter(kind=BackupKind.OFFSITE, created_at__gte=now - interval)
        if not recent.exists():
            created.append(
                queue(BackupKind.OFFSITE, trigger=RunTrigger.SCHEDULE, params={"full": False})
            )
    return created


def expire_lost_runs(now: datetime | None = None) -> int:
    """Runs the agent never finished: it restarted mid-job, or it is gone."""
    now = now or timezone.now()
    agent = agent_state()
    lost = 0
    for run in BackupRun.objects.filter(status=RunStatus.RUNNING):
        limit = timedelta(seconds=RUN_LIMIT_S.get(run.kind, 3600))
        started = run.started_at or run.created_at
        silent = agent.seen_at is None or (now - agent.seen_at) > timedelta(seconds=AGENT_SILENT_S)
        if now - started > limit or (silent and now - started > timedelta(seconds=AGENT_SILENT_S)):
            fail(run, "The backup service stopped answering before this job finished.")
            lost += 1
    # Queued work that nothing picked up for a day is dropped: the next slot queues anew.
    stale = BackupRun.objects.filter(
        status=RunStatus.QUEUED, created_at__lt=now - timedelta(days=1)
    )
    for run in stale:
        fail(run, "Nothing ran this job for a day (is the backup service running?).")
        lost += 1
    return lost


# --- The agent's jobs -------------------------------------------------------------------


def claim_next() -> BackupRun | None:
    """The oldest queued run, now running; None when nothing waits or one already runs."""
    with transaction.atomic():
        if BackupRun.objects.filter(status=RunStatus.RUNNING).exists():
            return None
        run = (
            BackupRun.objects.select_for_update(skip_locked=True)
            .filter(status=RunStatus.QUEUED)
            .order_by("created_at", "id")
            .first()
        )
        if run is None:
            return None
        run.status = RunStatus.RUNNING
        run.started_at = timezone.now()
        run.save(update_fields=["status", "started_at", "updated_at"])
    return run


def job_payload(run: BackupRun) -> dict[str, Any]:
    payload: dict[str, Any] = {"id": str(run.pk), "kind": run.kind, "params": run.params}
    if run.kind == BackupKind.DRILL:
        payload["tables"] = drill_tables()
    return payload


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def fail(run: BackupRun, error: str) -> None:
    run.status = RunStatus.FAILED
    run.error = redact_text(error)[:MAX_ERROR_CHARS]
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "error", "finished_at", "updated_at"])
    from apps.ops import alerting  # noqa: PLC0415 (alerting imports this module)

    alerting.run_failed(run)


def record_result(run: BackupRun, payload: Mapping[str, Any]) -> BackupRun:
    """Store what the agent reported for a run it claimed."""
    status = payload.get("status")
    if status not in (RunStatus.SUCCEEDED, RunStatus.FAILED) or run.status != RunStatus.RUNNING:
        return run
    detail = payload.get("detail")
    run.detail = json.loads(redact_text(json.dumps(detail))) if isinstance(detail, dict) else {}
    duration = _number(payload.get("duration_s"))
    size = _number(payload.get("size_bytes"))
    run.duration_s = duration
    run.size_bytes = int(size) if size is not None else None
    if status == RunStatus.SUCCEEDED and run.kind == BackupKind.DRILL:
        verdict = judge_drill(run.detail, run.started_at or run.created_at)
        run.detail["verdict"] = verdict
        if verdict["problems"]:
            run.save(update_fields=["detail", "duration_s", "size_bytes", "updated_at"])
            fail(run, "The restore drill found problems: " + "; ".join(verdict["problems"]))
            return run
    if status == RunStatus.FAILED:
        run.save(update_fields=["detail", "duration_s", "size_bytes", "updated_at"])
        fail(run, str(payload.get("error") or "The backup service reported a failure."))
        return run
    run.status = RunStatus.SUCCEEDED
    run.error = ""
    run.finished_at = timezone.now()
    run.save()
    from apps.ops import alerting  # noqa: PLC0415

    alerting.run_succeeded(run)
    return run


# --- The drill's verdict ---------------------------------------------------------------


def live_counts(tables: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    with connection.cursor() as cursor:
        for table in tables:
            cursor.execute(f"SELECT count(*) FROM {connection.ops.quote_name(table)}")  # noqa: S608
            row = cursor.fetchone()
            counts[table] = int(row[0]) if row else 0
    return counts


def live_migrations() -> list[tuple[str, str, datetime]]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT app, name, applied FROM django_migrations")
        return [(str(app), str(name), applied) for app, name, applied in cursor.fetchall()]


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.strip().replace(" ", "T", 1)
    if text.endswith("+00"):
        text += ":00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def judge_drill(detail: dict[str, Any], started: datetime) -> dict[str, Any]:
    """Compare the restored copy with the live database; problems fail the drill."""
    problems: list[str] = []
    recovered = _parse_time(detail.get("recovered_to"))
    restored_migrations = {
        (str(item[0]), str(item[1]))
        for item in detail.get("migrations", [])
        if isinstance(item, list | tuple) and len(item) >= 2
    }
    cutoff = recovered or started - DRILL_MAX_DATA_AGE
    expected = {(app, name) for app, name, applied in live_migrations() if applied <= cutoff}
    missing_migrations = sorted(f"{app}.{name}" for app, name in expected - restored_migrations)
    if not restored_migrations:
        problems.append("no migrations in the restored database")
    elif missing_migrations:
        problems.append(f"{len(missing_migrations)} migrations missing from the restored database")
    raw_counts = detail.get("counts")
    counts: dict[str, Any] = raw_counts if isinstance(raw_counts, dict) else {}
    tables = list(counts) + [str(name) for name in detail.get("missing_tables", [])]
    live = live_counts([table for table in tables if table in set(drill_tables())])
    empty = sorted(table for table, count in live.items() if count > 0 and not counts.get(table))
    if detail.get("missing_tables"):
        problems.append(
            "tables missing from the restored database: " + ", ".join(detail["missing_tables"])
        )
    if empty:
        problems.append("empty in the restored database but not live: " + ", ".join(empty))
    data_age = (started - recovered).total_seconds() if recovered else None
    if recovered is not None and started - recovered > DRILL_MAX_DATA_AGE:
        problems.append("the restored data is more than a day old: WAL archiving may have stopped")
    if detail.get("verify_ok") is False:
        problems.append("pgbackrest verify reported problems in the repository")
    return {
        "problems": problems,
        "missing_migrations": missing_migrations[:50],
        "live_counts": live,
        "data_age_s": round(data_age, 1) if data_age is not None else None,
    }


# --- What the admin sees ----------------------------------------------------------------


def latest_runs() -> dict[str, BackupRun | None]:
    def newest(kinds: Iterable[str], *, finished: bool = True) -> BackupRun | None:
        runs = BackupRun.objects.filter(kind__in=list(kinds))
        if finished:
            runs = runs.filter(status__in=(RunStatus.SUCCEEDED, RunStatus.FAILED))
        return runs.select_related("requested_by").order_by("-created_at").first()

    return {
        "db": newest(DB_KINDS),
        "files": newest([BackupKind.FILES]),
        "offsite": newest([BackupKind.OFFSITE]),
        "drill": newest([BackupKind.DRILL]),
        "check": newest([BackupKind.CHECK]),
    }


def summary(now: datetime | None = None) -> dict[str, Any]:
    now = now or timezone.now()
    zone = str(get_setting("backups.schedule_timezone"))
    agent = agent_state()
    s3 = s3_target()
    enabled = bool(get_setting("backups.enabled"))
    drill_enabled = bool(get_setting("backups.drill_enabled"))
    last_db = last_success(DB_KINDS)
    last_drill = last_success([BackupKind.DRILL])
    return {
        "enabled": enabled,
        "agent": {
            "seen_at": agent.seen_at,
            "healthy": agent.healthy,
            "archiving": bool(agent.status.get("archiving")),
            "passphrase": bool(agent.status.get("passphrase")),
            "disk_total_bytes": (agent.status.get("disk") or {}).get("total_bytes"),
            "disk_free_bytes": (agent.status.get("disk") or {}).get("free_bytes"),
            "db_backups": [
                item for item in agent.status.get("db_backups") or [] if isinstance(item, dict)
            ][-10:],
            "local_snapshots": agent.status.get("local_snapshots"),
            "offsite_snapshots": agent.status.get("offsite_snapshots"),
        },
        "local_dir": settings.BACKUP_DIR_LABEL,
        "s3": {
            "enabled": bool(get_setting("backups.s3_enabled")),
            "configured": s3 is not None,
            "endpoint": str(get_setting("backups.s3_endpoint")),
            "bucket": str(get_setting("backups.s3_bucket")),
            "prefix": str(get_setting("backups.s3_prefix")),
        },
        "next_backup_at": next_daily_slot(now, str(get_setting("backups.schedule_time")), zone)
        if enabled
        else None,
        "next_drill_at": next_weekly_slot(
            now,
            int(get_setting("backups.drill_weekday")),
            str(get_setting("backups.drill_time")),
            zone,
        )
        if enabled and drill_enabled
        else None,
        "last_success_db_at": last_db.finished_at if last_db else None,
        "last_success_drill_at": last_drill.finished_at if last_drill else None,
        "pending": BackupRun.objects.filter(
            status__in=(RunStatus.QUEUED, RunStatus.RUNNING)
        ).count(),
        "latest": latest_runs(),
    }


def runs_for_admin() -> "QuerySet[BackupRun]":
    return BackupRun.objects.select_related("requested_by").all()


def pending_db() -> bool:
    return _pending(DB_KINDS)
