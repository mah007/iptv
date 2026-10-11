"""Backup metrics for Prometheus (ADR-0020): what the monitoring overlay's rules watch.

- `iptv_backup_runs_total{kind,result}`: counted where results arrive (web);
- read at scrape time (never raises): the last success per kind, the drill's duration,
  when the backup service last asked for work, whether backups are on, and whether
  PostgreSQL's WAL archiver is failing.
"""

import logging
from collections.abc import Iterable

from django.db.models import Max
from prometheus_client import Counter
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.metrics_core import Metric
from prometheus_client.registry import Collector

from apps.ops import alerting, backups
from apps.ops.models import DB_KINDS, BackupKind, BackupRun, RunStatus

logger = logging.getLogger(__name__)

BACKUP_RUNS = Counter(
    "iptv_backup_runs", "Backup service jobs by kind and result.", ("kind", "result")
)

#: Run kinds grouped as the alerts see them.
GROUPS = {
    "db": DB_KINDS,
    "files": (BackupKind.FILES,),
    "offsite": (BackupKind.OFFSITE,),
    "drill": (BackupKind.DRILL,),
}


class BackupCollector(Collector):
    def describe(self) -> Iterable[Metric]:
        return []

    def collect(self) -> Iterable[Metric]:
        try:
            yield from self._collect()
        except Exception:  # the database is down: no samples, and WebNotReady says why
            logger.warning("metrics: backup state unavailable", exc_info=True)

    def _collect(self) -> Iterable[Metric]:
        from apps.core.services import get_setting  # noqa: PLC0415

        enabled = GaugeMetricFamily("iptv_backups_enabled", "1 when backups are switched on.")
        enabled.add_metric([], 1.0 if get_setting("backups.enabled") else 0.0)
        yield enabled
        latest = dict(
            BackupRun.objects.filter(status=RunStatus.SUCCEEDED)
            .values("kind")
            .annotate(at=Max("finished_at"))
            .values_list("kind", "at")
        )
        success = GaugeMetricFamily(
            "iptv_backup_last_success_timestamp_seconds",
            "When a job of this kind last succeeded (Unix time; absent when never).",
            labels=("kind",),
        )
        for group, kinds in GROUPS.items():
            moments = [latest[kind] for kind in kinds if latest.get(kind)]
            if moments:
                success.add_metric([group], max(moments).timestamp())
        yield success
        drill = backups.last_success([BackupKind.DRILL])
        if drill is not None and drill.duration_s is not None:
            duration = GaugeMetricFamily(
                "iptv_restore_drill_duration_seconds",
                "How long the last passed restore drill took to restore and recover.",
            )
            duration.add_metric([], drill.duration_s)
            yield duration
        agent = backups.agent_state()
        if agent.seen_at is not None:
            seen = GaugeMetricFamily(
                "iptv_backup_agent_last_seen_timestamp_seconds",
                "When the backup service last asked for work (Unix time).",
            )
            seen.add_metric([], agent.seen_at.timestamp())
            yield seen
        archiving = bool(agent.status.get("archiving"))
        failing = GaugeMetricFamily(
            "iptv_wal_archive_failing",
            "1 when PostgreSQL's WAL archiver failed more recently than it succeeded.",
        )
        failing.add_metric([], 1.0 if archiving and alerting.wal_archive_failing() else 0.0)
        yield failing
