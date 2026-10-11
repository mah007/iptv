"""Backup metrics for Prometheus and the command-line entry points (`make backup`,
`make restore-test`)."""

import io
from collections.abc import Callable
from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.ops import backups, waiting
from apps.ops.metrics import BackupCollector
from apps.ops.models import BackupKind, BackupRun, RunStatus, RunTrigger

pytestmark = pytest.mark.django_db


def samples() -> dict[str, list[Any]]:
    found: dict[str, list[Any]] = {}
    for family in BackupCollector().collect():
        found[family.name] = family.samples
    return found


def test_the_collector_exports_the_backup_state(
    agent_seen: Callable[..., None], monkeypatch: pytest.MonkeyPatch
) -> None:
    from apps.ops import alerting  # noqa: PLC0415

    monkeypatch.setattr(alerting, "wal_archive_failing", lambda: False)
    assert samples()["iptv_backups_enabled"][0].value == 1.0
    assert samples()["iptv_backup_last_success_timestamp_seconds"] == []
    agent_seen()
    run = backups.queue(BackupKind.DRILL, trigger=RunTrigger.SCHEDULE)
    BackupRun.objects.filter(pk=run.pk).update(
        status=RunStatus.SUCCEEDED, finished_at=timezone.now(), duration_s=4.4
    )
    found = samples()
    assert [sample.labels for sample in found["iptv_backup_last_success_timestamp_seconds"]] == [
        {"kind": "drill"}
    ]
    assert found["iptv_restore_drill_duration_seconds"][0].value == 4.4
    assert "iptv_backup_agent_last_seen_timestamp_seconds" in found
    assert found["iptv_wal_archive_failing"][0].value == 0.0


def test_the_collector_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("database down")

    monkeypatch.setattr(backups, "last_success", broken)
    list(BackupCollector().collect())


def test_backup_now_needs_the_service() -> None:
    with pytest.raises(CommandError, match="not running"):
        call_command("backup_now")


def test_backup_now_queues_and_waits(
    agent_seen: Callable[..., None], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_seen()
    out = io.StringIO()
    call_command("backup_now", "--full", stdout=out)
    assert "Queued: db_full, files" in out.getvalue()
    with pytest.raises(CommandError, match="already queued"):
        call_command("backup_now")

    def finished(runs: Any, *, timeout: float) -> Any:
        for run in runs:
            run.status = RunStatus.FAILED if run.kind == BackupKind.FILES else RunStatus.SUCCEEDED
            run.error = "restic: repository locked"
            yield run

    BackupRun.objects.all().delete()
    monkeypatch.setattr("apps.ops.management.commands.backup_now.wait_for", finished)
    out = io.StringIO()
    with pytest.raises(CommandError, match="failed"):
        call_command("backup_now", "--wait", stdout=out)
    assert "files: failed" in out.getvalue()
    assert "repository locked" in out.getvalue()


def test_restore_drill_backs_up_first_and_prints_the_checks(
    agent_seen: Callable[..., None], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_seen()

    def finished(runs: Any, *, timeout: float) -> Any:
        for run in runs:
            run.status = RunStatus.SUCCEEDED
            run.duration_s = 4.4
            if run.kind == BackupKind.DRILL:
                run.detail = {
                    "restore_s": 2.1,
                    "ready_s": 4.4,
                    "recovered_to": "2026-10-11 13:01:16+00",
                    "migrations": [["core", "0001"]],
                    "counts": {"accounts_user": 5},
                    "verify_ok": True,
                    "verdict": {"problems": [], "live_counts": {"accounts_user": 5}},
                }
            yield run

    monkeypatch.setattr("apps.ops.management.commands.restore_drill.wait_for", finished)
    out = io.StringIO()
    call_command("restore_drill", "--wait", stdout=out)
    text = out.getvalue()
    assert "No backup yet: taking one first." in text
    assert "restore: 2.1 s; ready (WAL replayed): 4.4 s" in text
    assert "accounts_user: 5 rows (live 5)" in text
    assert "repository verify: ok" in text


def test_waiting_times_out() -> None:
    run = backups.queue(BackupKind.CHECK, trigger=RunTrigger.COMMAND)
    with pytest.raises(CommandError, match="Timed out"):
        list(waiting.wait_for([run], timeout=0, poll_s=0))


def test_sizes_print_readably() -> None:
    assert waiting._size(None) == "-"
    assert waiting._size(512) == "512 B"
    assert waiting._size(5 * 1024 * 1024) == "5.0 MB"
