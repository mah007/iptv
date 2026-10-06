#!/usr/bin/env python3
"""Generate the Smart IPTV Grafana dashboards (SPEC §14, ADR-0018).

    python3 monitoring/grafana/build_dashboards.py          # write dashboards/*.json
    python3 monitoring/grafana/build_dashboards.py --check  # fail if they are stale

The seven dashboards are code, so every panel stays consistent and reviewable; Grafana
provisions the generated JSON read-only (monitoring/grafana/provisioning). Datasource
UIDs: prometheus, loki. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

OUT = Path(__file__).resolve().parent / "dashboards"
PROM = {"type": "prometheus", "uid": "prometheus"}
LOKI = {"type": "loki", "uid": "loki"}
PROJECT = 'project="smart-iptv"'
SERVICE = "container_label_com_docker_compose_service"
OURS = 'container_label_com_docker_compose_project="smart-iptv"'
FS = 'fstype!~"tmpfs|ramfs|overlay|squashfs|nsfs|fuse.lxcfs|autofs"'


def target(expr: str, legend: str = "", ref: str = "A", **extra: Any) -> dict[str, Any]:
    return {"refId": ref, "expr": expr, "legendFormat": legend, "datasource": PROM, **extra}


@dataclass
class Board:
    """Lays panels out left to right in rows of 24 columns."""

    uid: str
    title: str
    description: str
    panels: list[dict[str, Any]] = field(default_factory=list)
    x: int = 0
    y: int = 0
    row_height: int = 0
    next_id: int = 1

    def _place(self, width: int, height: int) -> dict[str, int]:
        if self.x + width > 24:
            self.x = 0
            self.y += self.row_height
            self.row_height = 0
        position = {"x": self.x, "y": self.y, "w": width, "h": height}
        self.x += width
        self.row_height = max(self.row_height, height)
        return position

    def _add(self, panel: dict[str, Any], width: int, height: int) -> None:
        panel["id"] = self.next_id
        self.next_id += 1
        panel["gridPos"] = self._place(width, height)
        self.panels.append(panel)

    def row(self, title: str) -> None:
        if self.x:
            self.y += self.row_height
        self.x, self.row_height = 0, 0
        self._add({"type": "row", "title": title, "collapsed": False, "panels": []}, 24, 1)
        self.x, self.y, self.row_height = 0, self.y + 1, 0

    def stat(
        self,
        title: str,
        expr: str,
        unit: str = "short",
        *,
        description: str = "",
        legend: str = "",
        width: int = 4,
        thresholds: list[tuple[float | None, str]] | None = None,
        decimals: int | None = None,
    ) -> None:
        steps = [
            {"color": color, "value": value} for value, color in (thresholds or [(None, "green")])
        ]
        defaults: dict[str, Any] = {
            "unit": unit,
            "thresholds": {"mode": "absolute", "steps": steps},
            "color": {"mode": "thresholds"},
        }
        if decimals is not None:
            defaults["decimals"] = decimals
        self._add(
            {
                "type": "stat",
                "title": title,
                "description": description,
                "datasource": PROM,
                "targets": [target(expr, legend)],
                "fieldConfig": {"defaults": defaults, "overrides": []},
                "options": {
                    "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                    "colorMode": "value",
                    "graphMode": "area",
                    "justifyMode": "auto",
                    "textMode": "auto",
                    "orientation": "auto",
                },
            },
            width,
            4,
        )

    def series(
        self,
        title: str,
        targets: list[tuple[str, str]],
        unit: str = "short",
        *,
        description: str = "",
        width: int = 12,
        height: int = 8,
        stack: bool = False,
        min_zero: bool = True,
    ) -> None:
        defaults: dict[str, Any] = {
            "unit": unit,
            "custom": {
                "drawStyle": "line",
                "lineWidth": 1,
                "fillOpacity": 20 if stack else 10,
                "showPoints": "never",
                "spanNulls": True,
                "stacking": {"mode": "normal" if stack else "none", "group": "A"},
            },
        }
        if min_zero:
            defaults["min"] = 0
        refs = "ABCDEFGHIJ"
        self._add(
            {
                "type": "timeseries",
                "title": title,
                "description": description,
                "datasource": PROM,
                "targets": [
                    target(expr, legend, refs[index])
                    for index, (expr, legend) in enumerate(targets)
                ],
                "fieldConfig": {"defaults": defaults, "overrides": []},
                "options": {
                    "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                    "tooltip": {"mode": "multi", "sort": "desc"},
                },
            },
            width,
            height,
        )

    def heatmap(self, title: str, expr: str, *, description: str = "", width: int = 12) -> None:
        self._add(
            {
                "type": "heatmap",
                "title": title,
                "description": description,
                "datasource": PROM,
                "targets": [target(expr, "{{le}}", format="heatmap")],
                "fieldConfig": {"defaults": {}, "overrides": []},
                "options": {
                    "calculate": False,
                    "yAxis": {"axisPlacement": "left"},
                    "cellGap": 1,
                    "color": {"mode": "scheme", "scheme": "Oranges", "steps": 64},
                    "tooltip": {"mode": "single", "yHistogram": True},
                },
            },
            width,
            8,
        )

    def logs(self, title: str, expr: str, *, description: str = "", height: int = 10) -> None:
        self._add(
            {
                "type": "logs",
                "title": title,
                "description": description,
                "datasource": LOKI,
                "targets": [{"refId": "A", "expr": expr, "datasource": LOKI, "queryType": "range"}],
                "options": {
                    "showTime": True,
                    "wrapLogMessage": True,
                    "sortOrder": "Descending",
                    "enableLogDetails": True,
                    "dedupStrategy": "none",
                },
            },
            24,
            height,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "title": self.title,
            "description": self.description,
            "tags": ["smart-iptv"],
            "timezone": "browser",
            "editable": False,
            "graphTooltip": 1,
            "refresh": "30s",
            "schemaVersion": 39,
            "version": 1,
            "time": {"from": "now-6h", "to": "now"},
            "templating": {"list": []},
            "annotations": {"list": []},
            "links": [
                {
                    "type": "dashboards",
                    "tags": ["smart-iptv"],
                    "asDropdown": True,
                    "title": "Smart IPTV",
                    "includeVars": False,
                    "keepTime": True,
                }
            ],
            "panels": self.panels,
        }


# --- Dashboards ------------------------------------------------------------------------


def streaming_ops() -> Board:
    b = Board(
        "iptv-streaming-ops",
        "Streaming Ops",
        "Playback now: streams, starts and refusals, kicks, the Xtream API and stream-auth.",
    )
    b.row("Now")
    b.stat("Streams playing", "sum(iptv_active_streams)", description="Open playback sessions.")
    b.stat(
        "Starts (1 h)",
        'sum(increase(iptv_stream_starts_total{result="ok"}[1h]))',
        decimals=0,
    )
    b.stat(
        "Refused starts (1 h)",
        'sum(increase(iptv_stream_starts_total{result!="ok"}[1h]))',
        decimals=0,
        thresholds=[(None, "green"), (1, "orange")],
    )
    b.stat(
        "Concurrency refusals (1 h)",
        "sum(increase(iptv_concurrency_rejections_total[1h]))",
        decimals=0,
        thresholds=[(None, "green"), (1, "orange")],
    )
    b.stat(
        "Xtream catalogue p95",
        'max(xtream:latency_seconds:p95_5m{action=~"get_.*"})',
        "s",
        description="Target under 150 ms for cached catalogue calls (SPEC §15).",
        thresholds=[(None, "green"), (0.15, "orange"), (0.3, "red")],
    )
    b.stat(
        "stream-auth cache hits",
        'sum(rate(iptv_edge_auth_total{auth_cache="HIT"}[15m])) / sum(rate(iptv_edge_auth_total[15m]))',
        "percentunit",
        description="Share of media requests the edge authorised from its 60 s cache.",
    )
    b.row("Playback")
    b.series("Streams by plan", [("sum by (plan) (iptv_active_streams)", "{{plan}}")], stack=True)
    b.series(
        "Streams by rendition",
        [("sum by (rendition) (iptv_active_streams)", "{{rendition}}")],
        stack=True,
    )
    b.series(
        "Stream starts by result (per 5 min)",
        [("sum by (result) (increase(iptv_stream_starts_total[5m]))", "{{result}}")],
    )
    b.series(
        "Sessions ended by the platform (per 5 min)",
        [("sum by (reason) (increase(iptv_kicks_total[5m]))", "{{reason}}")],
    )
    b.row("Xtream API")
    b.series(
        "Requests by action",
        [("sum by (action) (rate(iptv_xtream_requests_total[$__rate_interval]))", "{{action}}")],
        "reqps",
        stack=True,
    )
    b.series(
        "Responses other than 200/302",
        [
            (
                'sum by (status) (rate(iptv_xtream_requests_total{status!~"200|302"}[$__rate_interval]))',
                "{{status}}",
            )
        ],
        "reqps",
    )
    b.series("p95 latency by action", [("xtream:latency_seconds:p95_5m", "{{action}}")], "s")
    b.series(
        "stream-auth latency",
        [
            (
                "histogram_quantile(0.5, sum by (le) (rate("
                'django_http_requests_latency_seconds_by_view_method_bucket{view="stream-auth"}[5m])))',
                "p50",
            ),
            (
                "histogram_quantile(0.95, sum by (le) (rate("
                'django_http_requests_latency_seconds_by_view_method_bucket{view="stream-auth"}[5m])))',
                "p95",
            ),
        ],
        "s",
        description="Django's Redis-only heartbeat and kick check behind the edge's auth_request.",
    )
    b.row("Refusals at the edge")
    b.series(
        "Media requests refused or failed, by reason",
        [("sum by (reason) (rate(iptv_edge_denied_total[$__rate_interval]))", "{{reason}}")],
        "reqps",
        width=24,
    )
    b.logs(
        "Playback and Xtream warnings and errors",
        f'{{{PROJECT}, service="web"}} |~ "apps\\\\.(playback|xtream_api)" '
        '| detected_level=~"warn|warning|error|critical"',
    )
    return b


def transcoding() -> Board:
    b = Board(
        "iptv-transcoding",
        "Transcoding",
        "Transcode jobs, encoding speed, failures and the transcoder's resources.",
    )
    media_task = 'task="apps.media.tasks.run_transcode_job"'
    b.row("Now")
    b.stat("Queued jobs", 'sum(iptv_transcode_jobs{status="queued"})', decimals=0)
    b.stat("Running jobs", 'sum(iptv_transcode_jobs{status="running"})', decimals=0)
    b.stat(
        "Oldest unfinished job",
        "max(iptv_transcode_oldest_job_age_seconds)",
        "s",
        thresholds=[(None, "green"), (3600, "orange"), (7200, "red")],
    )
    b.stat(
        "Failed encodes (24 h)",
        f'sum(increase(iptv_celery_tasks_total{{{media_task},state="failed"}}[24h]))',
        decimals=0,
        thresholds=[(None, "green"), (1, "orange")],
    )
    b.stat(
        "Speed (running)",
        "avg(iptv_transcode_speed_ratio)",
        "x",
        description="Times real time; 1x means a 2-hour film takes 2 hours.",
    )
    b.stat("transcode.* waiting", 'sum(iptv_celery_queue_length{queue=~"transcode.*"})', decimals=0)
    b.row("Jobs")
    b.series(
        "Unfinished jobs by status and backend",
        [
            (
                'sum by (status, backend) (iptv_transcode_jobs{status=~"queued|running"})',
                "{{status}} {{backend}}",
            )
        ],
        stack=True,
    )
    b.series(
        "Oldest unfinished job",
        [("iptv_transcode_oldest_job_age_seconds", "{{status}}")],
        "s",
    )
    b.series("Encoding speed by backend", [("iptv_transcode_speed_ratio", "{{backend}}")], "x")
    b.series(
        "Media tasks by outcome (per 15 min)",
        [
            (
                'sum by (task, state) (increase(iptv_celery_tasks_total{task=~"apps.media.tasks.*"}[15m]))',
                "{{task}} {{state}}",
            )
        ],
    )
    b.series(
        "Transcode task duration p95",
        [
            (
                "histogram_quantile(0.95, sum by (le) (rate("
                f"iptv_celery_task_duration_seconds_bucket{{{media_task}}}[1h])))",
                "p95",
            )
        ],
        "s",
    )
    b.series(
        "Transcode queues",
        [('sum by (queue) (iptv_celery_queue_length{queue=~"transcode.*"})', "{{queue}}")],
    )
    b.row("Transcoder resources")
    b.series(
        "Transcoder CPU",
        [
            (
                f'sum(rate(container_cpu_usage_seconds_total{{{SERVICE}="transcoder"}}[$__rate_interval]))',
                "cores",
            )
        ],
    )
    b.series(
        "Transcoder memory",
        [(f'sum(container_memory_working_set_bytes{{{SERVICE}="transcoder"}})', "working set")],
        "bytes",
    )
    b.series(
        "NVIDIA GPU and encoder load",
        [
            ("nvidia_smi_utilization_gpu_ratio", "gpu {{name}}"),
            ("nvidia_smi_utilization_encoder_ratio", "encoder {{name}}"),
        ],
        "percentunit",
        description="Only with the gpu-nvidia profile; empty on CPU-only servers.",
    )
    b.series(
        "NVIDIA GPU memory",
        [("nvidia_smi_memory_used_bytes", "{{name}}")],
        "bytes",
        description="Only with the gpu-nvidia profile.",
    )
    b.logs(
        "Transcoder warnings and errors",
        f'{{{PROJECT}, service="transcoder"}} | detected_level=~"warn|warning|error|critical"',
    )
    return b


def library_metadata() -> Board:
    b = Board(
        "iptv-library-metadata",
        "Library & Metadata",
        "Scans, matching confidence, the review queue and the metadata providers.",
    )
    b.row("Now")
    b.stat(
        "Open reviews",
        "max(iptv_review_queue_open)",
        decimals=0,
        thresholds=[(None, "green"), (20, "orange"), (50, "red")],
    )
    b.stat("Titles ready", 'sum(iptv_catalog_titles{status="ready"})', decimals=0)
    b.stat("Titles waiting", 'sum(iptv_catalog_titles{status=~"processing|review"})', decimals=0)
    b.stat("Files seen (24 h)", "sum(increase(iptv_scan_files_total[24h]))", decimals=0)
    b.stat(
        "TMDB errors (1 h)",
        'sum(increase(iptv_metadata_api_requests_total{status!~"200|404"}[1h]))',
        decimals=0,
        thresholds=[(None, "green"), (1, "orange")],
    )
    b.stat(
        "Ingest queues",
        'sum(iptv_celery_queue_length{queue=~"scan|metadata|images"})',
        decimals=0,
    )
    b.row("Scans and matching")
    b.series(
        "Scan outcomes per file (per hour)",
        [("sum by (result) (increase(iptv_scan_files_total[1h]))", "{{result}}")],
        stack=True,
    )
    b.heatmap(
        "Match confidence",
        "sum by (le) (increase(iptv_match_confidence_bucket[$__interval]))",
        description="Auto-accept needs 0.85 and a 0.10 lead; the rest goes to review.",
    )
    b.series("Open reviews", [("max(iptv_review_queue_open)", "open")])
    b.series(
        "Titles by status",
        [("sum by (kind, status) (iptv_catalog_titles)", "{{kind}} {{status}}")],
    )
    b.row("Providers and tasks")
    b.series(
        "Metadata API calls by status",
        [
            (
                "sum by (provider, status) (rate(iptv_metadata_api_requests_total[$__rate_interval]))",
                "{{provider}} {{status}}",
            )
        ],
        "reqps",
    )
    b.series(
        "Ingest task failures and retries (per 15 min)",
        [
            (
                'sum by (task, state) (increase(iptv_celery_tasks_total{task=~"apps.(library|metadata).tasks.*",'
                'state!="succeeded"}[15m]))',
                "{{task}} {{state}}",
            )
        ],
    )
    b.series(
        "Ingest queues",
        [('sum by (queue) (iptv_celery_queue_length{queue=~"scan|metadata|images"})', "{{queue}}")],
        width=24,
    )
    b.logs(
        "Worker and watcher warnings and errors",
        f'{{{PROJECT}, service=~"worker|watcher"}} | detected_level=~"warn|warning|error|critical"',
    )
    return b


def business() -> Board:
    b = Board(
        "iptv-business",
        "Business",
        "Customers, subscriptions, revenue, payments and notifications. Live figures; "
        "the admin's dashboard and invoices are the records.",
    )
    b.row("Now")
    b.stat("Active customers", 'sum(iptv_customers{status="active"})', decimals=0)
    b.stat("Active subscriptions", 'sum(iptv_subscriptions{status="active"})', decimals=0)
    b.stat("Trials", "sum(iptv_trials_active)", decimals=0)
    b.stat(
        "MRR",
        "iptv_mrr_minor / 100",
        legend="{{currency}}",
        decimals=2,
        description="Monthly recurring revenue per currency, as sold (VAT included when prices include it).",
    )
    b.stat(
        "Revenue this month",
        "iptv_revenue_month_minor / 100",
        legend="{{currency}}",
        decimals=2,
    )
    b.stat(
        "Failing webhooks",
        "sum(iptv_payment_webhooks_failing) or vector(0)",
        decimals=0,
        thresholds=[(None, "green"), (1, "red")],
    )
    b.row("Customers and subscriptions")
    b.series("Customers by status", [("sum by (status) (iptv_customers)", "{{status}}")])
    b.series("Subscriptions by status", [("sum by (status) (iptv_subscriptions)", "{{status}}")])
    b.series("Devices", [("sum by (state) (iptv_devices)", "{{state}}")])
    b.series("MRR by currency", [("iptv_mrr_minor / 100", "{{currency}}")])
    b.row("Payments and notifications")
    b.series(
        "Payments by provider and status (per hour)",
        [
            (
                "sum by (provider, status) (increase(iptv_payments_total[1h]))",
                "{{provider}} {{status}}",
            )
        ],
    )
    b.series(
        "Payment webhooks by result (per hour)",
        [
            (
                "sum by (provider, result) (increase(iptv_payment_webhooks_total[1h]))",
                "{{provider}} {{result}}",
            )
        ],
    )
    b.series(
        "Notifications by channel and status (per hour)",
        [
            (
                "sum by (channel, status) (increase(iptv_notifications_total[1h]))",
                "{{channel}} {{status}}",
            )
        ],
    )
    b.series(
        "Notification outbox",
        [("sum by (channel, status) (iptv_notification_outbox)", "{{channel}} {{status}}")],
        description="Queued now, and failed in the last 24 hours.",
    )
    return b


def platform() -> Board:
    b = Board(
        "iptv-platform",
        "Platform",
        "Traefik, Django, PostgreSQL, both Valkeys, Celery, Meilisearch and the containers.",
    )
    b.row("Now")
    b.stat(
        "Targets up",
        "sum(up) / count(up)",
        "percentunit",
        thresholds=[(None, "red"), (0.95, "orange"), (1, "green")],
    )
    b.stat(
        "HTTP 5xx (5 min)",
        'sum(rate(traefik_service_requests_total{code=~"5.."}[5m])) or vector(0)',
        "reqps",
        thresholds=[(None, "green"), (0.01, "orange"), (0.1, "red")],
    )
    b.stat(
        "Web ready",
        'min(probe_success{probe="web-ready"})',
        thresholds=[(None, "red"), (1, "green")],
    )
    b.stat(
        "Meilisearch",
        'min(probe_success{probe="meilisearch"})',
        thresholds=[(None, "red"), (1, "green")],
    )
    b.stat(
        "Beat heartbeat age",
        'max(iptv_heartbeat_age_seconds{service="beat"})',
        "s",
        thresholds=[(None, "green"), (90, "orange"), (180, "red")],
    )
    b.stat(
        "Workers up",
        'sum(up{job="celery"})',
        decimals=0,
        thresholds=[(None, "red"), (2, "green")],
    )
    b.row("Ingress (Traefik) and Django")
    b.series(
        "Requests by router",
        [("sum by (router) (rate(traefik_router_requests_total[$__rate_interval]))", "{{router}}")],
        "reqps",
        stack=True,
    )
    b.series(
        "5xx by router",
        [
            (
                'sum by (router) (rate(traefik_router_requests_total{code=~"5.."}[$__rate_interval]))',
                "{{router}}",
            )
        ],
        "reqps",
    )
    b.series(
        "p95 latency by router",
        [
            (
                "histogram_quantile(0.95, sum by (le, router) "
                "(rate(traefik_router_request_duration_seconds_bucket[5m])))",
                "{{router}}",
            )
        ],
        "s",
    )
    b.series(
        "Slowest Django views (p95)",
        [
            (
                "topk(10, histogram_quantile(0.95, sum by (le, view) "
                "(rate(django_http_requests_latency_seconds_by_view_method_bucket[5m]))))",
                "{{view}}",
            )
        ],
        "s",
    )
    b.row("PostgreSQL")
    b.series(
        "Connections by state",
        [("sum by (state) (pg_stat_activity_count)", "{{state}}")],
        stack=True,
    )
    b.series(
        "Transactions",
        [
            ('sum(rate(pg_stat_database_xact_commit{datname!~"template.*"}[5m]))', "commits"),
            ('sum(rate(pg_stat_database_xact_rollback{datname!~"template.*"}[5m]))', "rollbacks"),
        ],
        "ops",
    )
    b.series(
        "Database size",
        [('pg_database_size_bytes{datname!~"template.*|postgres"}', "{{datname}}")],
        "bytes",
    )
    b.series(
        "Deadlocks and conflicts (per hour)",
        [
            ("sum(increase(pg_stat_database_deadlocks[1h]))", "deadlocks"),
            ("sum(increase(pg_stat_database_conflicts[1h]))", "conflicts"),
        ],
    )
    b.row("Valkey")
    b.series("Memory used", [("redis_memory_used_bytes", "{{instance}}")], "bytes")
    b.series(
        "Commands",
        [("rate(redis_commands_processed_total[$__rate_interval])", "{{instance}}")],
        "ops",
    )
    b.series(
        "Evicted keys (per 5 min)",
        [("increase(redis_evicted_keys_total[5m])", "{{instance}}")],
        description="redis-state must stay at 0; redis-cache evicts by design.",
    )
    b.series("Clients", [("redis_connected_clients", "{{instance}}")])
    b.row("Celery")
    b.series(
        "Tasks by state",
        [("sum by (state) (rate(iptv_celery_tasks_total[$__rate_interval]))", "{{state}}")],
        "ops",
        stack=True,
    )
    b.series("Queue lengths", [("iptv_celery_queue_length", "{{queue}}")])
    b.series("Heartbeat ages", [("iptv_heartbeat_age_seconds", "{{service}}")], "s")
    b.series(
        "Slowest tasks (p95)",
        [
            (
                "topk(8, histogram_quantile(0.95, sum by (le, task) "
                "(rate(iptv_celery_task_duration_seconds_bucket[30m]))))",
                "{{task}}",
            )
        ],
        "s",
    )
    b.row("Containers")
    b.series(
        "CPU by service",
        [
            (
                f"sum by ({SERVICE}) (rate(container_cpu_usage_seconds_total{{{OURS}}}[$__rate_interval]))",
                f"{{{{{SERVICE}}}}}",
            )
        ],
        stack=True,
    )
    b.series(
        "Memory by service",
        [
            (
                f"sum by ({SERVICE}) (container_memory_working_set_bytes{{{OURS}}})",
                f"{{{{{SERVICE}}}}}",
            )
        ],
        "bytes",
        stack=True,
    )
    b.series(
        "Health probes",
        [("probe_success", "{{probe}}")],
        min_zero=True,
        width=24,
        height=6,
    )
    b.logs("Errors from every service", f'{{{PROJECT}}} | detected_level=~"error|critical|fatal"')
    return b


def edges() -> Board:
    b = Board(
        "iptv-edges",
        "Edges",
        "The media edges: egress against capacity, status codes, caches, request time and refusals.",
    )
    b.row("Now")
    b.stat("Egress", "sum(edge:egress_bits:rate5m)", "bps")
    b.stat(
        "Capacity used",
        "max(edge:egress_bits:rate5m) / scalar(iptv:edge_capacity_bits)",
        "percentunit",
        thresholds=[(None, "green"), (0.6, "orange"), (0.8, "red")],
        description="Of EDGE_CAPACITY_MBPS; EdgeEgressHigh fires above 80% for 10 minutes.",
    )
    b.stat(
        "5xx share",
        "max(edge:requests_5xx:ratio5m) or vector(0)",
        "percentunit",
        thresholds=[(None, "green"), (0.005, "orange"), (0.01, "red")],
    )
    b.stat("Connections", "sum(nginx_connections_active)", decimals=0)
    b.stat("Requests", "sum(edge:requests:rate5m)", "reqps")
    b.stat(
        "Edge up",
        'min(probe_success{probe="edge"})',
        thresholds=[(None, "red"), (1, "green")],
    )
    b.row("Traffic")
    b.series(
        "Egress per edge and capacity",
        [
            ("edge:egress_bits:rate5m", "{{edge}}"),
            ("iptv:edge_capacity_bits", "capacity"),
        ],
        "bps",
        description="From the edge container's network counters (real time).",
    )
    b.series(
        "Requests by status class",
        [
            (
                "sum by (status_class) (rate(iptv_edge_requests_total[$__rate_interval]))",
                "{{status_class}}",
            )
        ],
        "reqps",
        stack=True,
    )
    b.series(
        "4xx and 5xx per edge",
        [
            (
                'sum by (edge, status_class) (rate(iptv_edge_requests_total{status_class=~"4xx|5xx"}'
                "[$__rate_interval]))",
                "{{edge}} {{status_class}}",
            )
        ],
        "reqps",
    )
    b.series(
        "Refusals by reason",
        [("sum by (reason) (rate(iptv_edge_denied_total[$__rate_interval]))", "{{reason}}")],
        "reqps",
    )
    b.row("Caches and timing")
    b.series(
        "Cache hit ratio",
        [
            (
                'sum by (edge) (rate(iptv_edge_auth_total{auth_cache="HIT"}[15m])) '
                "/ sum by (edge) (rate(iptv_edge_auth_total[15m]))",
                "stream-auth {{edge}}",
            ),
            (
                'sum by (edge) (rate(iptv_edge_requests_total{cache_status="HIT"}[15m])) '
                '/ sum by (edge) (rate(iptv_edge_requests_total{cache_status!="NONE"}[15m]))',
                "media {{edge}}",
            ),
        ],
        "percentunit",
        description="Media caching only applies to edges with an S3 origin; local disk shows none.",
    )
    b.series(
        "Request time p95 (playlists and segments, TTFB proxy)",
        [
            (
                "histogram_quantile(0.95, sum by (le, kind) "
                '(rate(iptv_edge_request_duration_seconds_bucket{kind=~"playlist|segment"}[5m])))',
                "{{kind}}",
            )
        ],
        "s",
    )
    b.series(
        "nginx connections",
        [
            ("sum(nginx_connections_active)", "active"),
            ("sum(nginx_connections_writing)", "writing"),
            ("sum(nginx_connections_waiting)", "waiting"),
        ],
    )
    b.series(
        "Bytes sent (from the access log)",
        [("sum by (edge) (rate(iptv_edge_sent_bytes_total[$__rate_interval])) * 8", "{{edge}}")],
        "bps",
        description="Counted when a request ends, so long progressive downloads arrive late.",
    )
    b.logs(
        "Edge errors (status 5xx)",
        f'{{{PROJECT}, service="nginx-stream"}} | json | status >= 500',
    )
    return b


def hosts_gpu() -> Board:
    b = Board(
        "iptv-hosts-gpu",
        "Hosts & GPU",
        "The host's CPU, memory, disks and network, container usage, and NVIDIA GPUs.",
    )
    b.row("Now")
    b.stat(
        "CPU busy",
        'avg(1 - rate(node_cpu_seconds_total{mode="idle"}[5m]))',
        "percentunit",
        thresholds=[(None, "green"), (0.7, "orange"), (0.9, "red")],
    )
    b.stat(
        "Memory used",
        "1 - sum(node_memory_MemAvailable_bytes) / sum(node_memory_MemTotal_bytes)",
        "percentunit",
        thresholds=[(None, "green"), (0.8, "orange"), (0.9, "red")],
    )
    b.stat(
        "Root disk used",
        'max(node:filesystem_used:ratio{mountpoint="/"})',
        "percentunit",
        thresholds=[(None, "green"), (0.75, "orange"), (0.85, "red")],
    )
    b.stat("Load (1 min)", "max(node_load1)", decimals=2)
    b.stat(
        "Public egress",
        f'sum(rate(container_network_transmit_bytes_total{{{SERVICE}="traefik"}}[5m])) * 8',
        "bps",
        description="Everything Traefik sends to clients (single-host tier).",
    )
    b.stat(
        "OOM kills (24 h)",
        f"sum(increase(container_oom_events_total{{{OURS}}}[24h])) or vector(0)",
        decimals=0,
        thresholds=[(None, "green"), (1, "orange")],
    )
    b.row("Host")
    b.series(
        "CPU by mode",
        [
            (
                'sum by (mode) (rate(node_cpu_seconds_total{mode!="idle"}[$__rate_interval]))',
                "{{mode}}",
            )
        ],
        stack=True,
        description="In cores.",
    )
    b.series(
        "Memory",
        [
            ("sum(node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes)", "used"),
            ("sum(node_memory_Buffers_bytes + node_memory_Cached_bytes)", "page cache"),
            ("sum(node_memory_MemTotal_bytes)", "total"),
        ],
        "bytes",
    )
    b.series("Filesystems used", [("node:filesystem_used:ratio", "{{mountpoint}}")], "percentunit")
    b.series(
        "Free space in 7 days (trend)",
        [(f"predict_linear(node_filesystem_avail_bytes{{{FS}}}[6h], 7 * 86400)", "{{mountpoint}}")],
        "bytes",
        min_zero=False,
        description="Where the last 6 hours point; below 0 means the disk fills within a week.",
    )
    b.series(
        "Disk I/O",
        [
            (
                "sum by (device) (rate(node_disk_read_bytes_total[$__rate_interval]))",
                "read {{device}}",
            ),
            (
                "sum by (device) (rate(node_disk_written_bytes_total[$__rate_interval]))",
                "write {{device}}",
            ),
        ],
        "Bps",
    )
    b.series(
        "Network through Traefik",
        [
            (
                f'sum(rate(container_network_transmit_bytes_total{{{SERVICE}="traefik"}}[$__rate_interval])) * 8',
                "to clients",
            ),
            (
                f'sum(rate(container_network_receive_bytes_total{{{SERVICE}="traefik"}}[$__rate_interval])) * 8',
                "from clients",
            ),
        ],
        "bps",
    )
    b.row("Containers")
    b.series(
        "Top memory",
        [
            (
                f"topk(10, sum by ({SERVICE}) (container_memory_working_set_bytes{{{OURS}}}))",
                f"{{{{{SERVICE}}}}}",
            )
        ],
        "bytes",
    )
    b.series(
        "Top CPU",
        [
            (
                f"topk(10, sum by ({SERVICE}) (rate(container_cpu_usage_seconds_total{{{OURS}}}[$__rate_interval])))",
                f"{{{{{SERVICE}}}}}",
            )
        ],
    )
    b.row("NVIDIA GPU (gpu-nvidia profile)")
    b.series(
        "GPU, encoder and decoder load",
        [
            ("nvidia_smi_utilization_gpu_ratio", "gpu {{name}}"),
            ("nvidia_smi_utilization_encoder_ratio", "encoder {{name}}"),
            ("nvidia_smi_utilization_decoder_ratio", "decoder {{name}}"),
        ],
        "percentunit",
    )
    b.series(
        "GPU memory and temperature",
        [
            ("nvidia_smi_memory_used_bytes", "memory {{name}}"),
        ],
        "bytes",
    )
    return b


DASHBOARDS = (streaming_ops, transcoding, library_metadata, business, platform, edges, hosts_gpu)


def render() -> dict[str, str]:
    files = {}
    for build in DASHBOARDS:
        board = build()
        name = board.uid.removeprefix("iptv-") + ".json"
        files[name] = json.dumps(board.to_json(), indent=2, ensure_ascii=False) + "\n"
    return files


def expressions() -> list[tuple[str, str, str]]:
    """(dashboard, panel, PromQL) for every Prometheus query, for the test harness."""
    found = []
    for build in DASHBOARDS:
        board = build()
        for panel in board.panels:
            for item in panel.get("targets", []):
                if item.get("datasource") == PROM:
                    found.append((board.title, panel["title"], item["expr"]))
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true", help="fail if the JSON files are stale")
    parser.add_argument(
        "--list-exprs", action="store_true", help="print every PromQL query as JSON"
    )
    args = parser.parse_args()
    if args.list_exprs:
        print(json.dumps(expressions(), indent=1))
        return 0
    files = render()
    if args.check:
        stale = [
            name
            for name, content in files.items()
            if not (OUT / name).is_file() or (OUT / name).read_text() != content
        ]
        extra = sorted({path.name for path in OUT.glob("*.json")} - set(files))
        if stale or extra:
            print(
                f"dashboards are stale: {stale + extra}; run make monitoring-dashboards",
                file=sys.stderr,
            )
            return 1
        print(f"{len(files)} dashboards up to date")
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (OUT / name).write_text(content)
    print(f"wrote {len(files)} dashboards to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
