"""Waiting for backup runs from the command line (`make backup`, `make restore-test`)."""

import time
from collections.abc import Iterator, Sequence
from typing import TextIO

from django.core.management.base import CommandError, OutputWrapper

from apps.ops.models import BackupKind, BackupRun, RunStatus

DONE = (RunStatus.SUCCEEDED, RunStatus.FAILED)


def wait_for(
    runs: Sequence[BackupRun], *, timeout: float, poll_s: float = 2.0
) -> Iterator[BackupRun]:
    """Yield each run once it has finished, in order; CommandError after `timeout`."""
    deadline = time.monotonic() + timeout
    for run in runs:
        while True:
            run.refresh_from_db()
            if run.status in DONE:
                yield run
                break
            if time.monotonic() > deadline:
                msg = f"Timed out waiting for {run.kind} ({run.status})."
                raise CommandError(msg)
            time.sleep(poll_s)


def _size(value: int | None) -> str:
    if value is None:
        return "-"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"  # pragma: no cover


def print_run(out: OutputWrapper | TextIO, run: BackupRun) -> None:
    duration = f"{run.duration_s:.1f} s" if run.duration_s is not None else "-"
    out.write(f"{run.kind}: {run.status} in {duration}, {_size(run.size_bytes)} added")
    if run.kind == BackupKind.DRILL:
        detail = run.detail
        verdict = detail.get("verdict", {})
        restore_s, ready_s = detail.get("restore_s"), detail.get("ready_s")
        out.write(f"  restore: {restore_s} s; ready (WAL replayed): {ready_s} s")
        out.write(f"  data reaches: {detail.get('recovered_to') or 'unknown'}")
        out.write(f"  migrations restored: {len(detail.get('migrations', []))}")
        live = verdict.get("live_counts", {})
        for table, count in sorted((detail.get("counts") or {}).items()):
            out.write(f"  {table}: {count} rows (live {live.get(table, '?')})")
        out.write(f"  repository verify: {'ok' if detail.get('verify_ok') else 'problems'}")
        for problem in verdict.get("problems", []):
            out.write(f"  PROBLEM: {problem}")
    if run.status == RunStatus.FAILED and run.error:
        out.write(f"  error: {run.error.splitlines()[0][:300]}")
