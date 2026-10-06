#!/usr/bin/env python3
"""Offline checks of the monitoring overlay (make monitoring-test, ADR-0018).

Each tool checks its own configuration, in the image the overlay pins:

- Prometheus: the rendered configuration, the rule files and their unit tests
  (promtool test rules: every alert fires on its condition and stays quiet below it);
- every PromQL expression of the generated dashboards, parsed by a throwaway
  Prometheus, and the dashboards' JSON up to date with their generator;
- Alertmanager: the rendered configuration with email only and with email, Telegram
  and the watchdog (amtool check-config), and the notification templates;
- Loki (-verify-config), Alloy (validate, and canonical formatting), the blackbox
  exporter (--config.check);
- the compose files: dev and production overlays merge cleanly with an environment
  generated from .env.example.

Usage: python3 monitoring/tests/check_configs.py   (Docker; nothing is left behind)
"""

from __future__ import annotations

import json
import re
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MONITORING = REPO / "monitoring"
OVERLAY = REPO / "docker" / "compose.monitoring.yml"


def pinned(prefix: str) -> str:
    match = re.search(rf"image:\s*({re.escape(prefix)}:\S+)", OVERLAY.read_text())
    if not match:
        raise RuntimeError(f"{prefix} is not pinned in {OVERLAY}")
    return match[1]


def run(title: str, *args: str, cwd: Path = REPO) -> bool:
    result = subprocess.run(list(args), capture_output=True, text=True, check=False, cwd=cwd)
    if result.returncode == 0:
        print(f"  ok    {title}")
        return True
    print(f"  FAIL  {title}\n{result.stdout[-3000:]}{result.stderr[-3000:]}")
    return False


def docker(title: str, *args: str) -> bool:
    return run(title, "docker", "run", "--rm", *args)


def prometheus_checks(image: str) -> list[bool]:
    mount = f"{MONITORING}/prometheus:/etc/prometheus:ro"
    return [
        docker(
            "prometheus: rendered configuration and rule files",
            "--entrypoint",
            "sh",
            "-v",
            mount,
            "--tmpfs",
            "/run/prometheus:mode=1777",
            "-e",
            "PROMETHEUS_CHECK_ONLY=1",
            "-e",
            "PUBLIC_PROBE_URLS=https://tv.example.com/health https://admin.example.com/",
            image,
            "/etc/prometheus/entrypoint.sh",
        ),
        docker(
            "prometheus: alert rule unit tests",
            "--entrypoint",
            "/bin/promtool",
            "-v",
            mount,
            image,
            "test",
            "rules",
            "/etc/prometheus/tests/alerts.test.yml",
        ),
    ]


def dashboard_expressions(image: str) -> bool:
    """Every dashboard query must parse: a throwaway Prometheus answers each one."""
    listed = subprocess.run(
        [sys.executable, str(MONITORING / "grafana" / "build_dashboards.py"), "--list-exprs"],
        capture_output=True,
        text=True,
        check=True,
    )
    expressions = json.loads(listed.stdout)
    name = f"iptv-monitoring-check-{secrets.token_hex(3)}"
    subprocess.run(
        ["docker", "run", "-d", "--name", name, "-p", "127.0.0.1::9090", image],
        capture_output=True,
        check=True,
    )
    try:
        port = (
            subprocess.run(
                ["docker", "port", name, "9090"], capture_output=True, text=True, check=True
            )
            .stdout.split(":")[-1]
            .strip()
        )
        base = f"http://127.0.0.1:{port}"
        for _ in range(60):
            try:
                with urllib.request.urlopen(f"{base}/-/ready", timeout=2):  # noqa: S310
                    break
            except (urllib.error.URLError, ConnectionError):
                time.sleep(0.5)
        bad = []
        for dashboard, panel, expr in expressions:
            # Grafana's interval variables, as Grafana substitutes them.
            query = expr.replace("$__rate_interval", "1m").replace("$__interval", "1m")
            url = f"{base}/api/v1/query?{urllib.parse.urlencode({'query': query})}"
            detail = ""
            try:
                with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310
                    ok = json.load(response).get("status") == "success"
            except urllib.error.HTTPError as error:
                ok = False
                detail = f"\n      {error.read().decode()[:300]}"
            if not ok:
                bad.append(f"{dashboard} / {panel}: {expr}{detail}")
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
    if bad:
        print("  FAIL  dashboards: queries that do not parse\n    " + "\n    ".join(bad))
        return False
    print(f"  ok    dashboards: {len(expressions)} queries parse")
    return True


def alertmanager_checks(image: str) -> list[bool]:
    results = []
    with tempfile.TemporaryDirectory(prefix="iptv-am-") as tmp:
        root = Path(tmp)
        root.chmod(0o755)
        variants = {
            "email only": {},
            "email, Telegram and watchdog": {
                "alert_smtp_password": "smtp-password",
                "alert_telegram_bot_token": "123456789:" + "A" * 35,
                "alert_telegram_chat_id": "-1001234567890",
                "alert_watchdog_url": "https://push.example.com/api/push/abc",
            },
        }
        for label, files in variants.items():
            folder = root / re.sub(r"\W+", "-", label)
            folder.mkdir()
            folder.chmod(0o755)
            for name, value in files.items():
                (folder / name).write_text(value)
                (folder / name).chmod(0o644)
            results.append(
                docker(
                    f"alertmanager: {label}",
                    "--entrypoint",
                    "sh",
                    "-v",
                    f"{MONITORING}/alertmanager:/etc/alertmanager:ro",
                    "-v",
                    f"{folder}:/run/secrets:ro",
                    "--tmpfs",
                    "/run/alertmanager:mode=1777",
                    "-e",
                    "ALERT_EMAIL_TO=ops@example.com",
                    "-e",
                    "ALERT_SMTP_SMARTHOST=smtp.example.com:587",
                    "-e",
                    "ALERT_SMTP_FROM=Smart IPTV <alerts@example.com>",
                    "-e",
                    "ALERT_SMTP_USERNAME=mailer",
                    "-e",
                    "ALERTMANAGER_RENDER_ONLY=1",
                    image,
                    "-c",
                    "/etc/alertmanager/entrypoint.sh > /dev/null"
                    " && amtool check-config /run/alertmanager/alertmanager.yml",
                )
            )
    for template in ("iptv.subject", "iptv.text", "iptv.telegram"):
        results.append(
            docker(
                f"alertmanager: template {template}",
                "--entrypoint",
                "amtool",
                "-v",
                f"{MONITORING}/alertmanager/templates:/t:ro",
                image,
                "template",
                "render",
                "--template.glob=/t/*.tmpl",
                f'--template.text={{{{ template "{template}" . }}}}',
            )
        )
    return results


def compose_checks() -> list[bool]:
    results = []
    with tempfile.TemporaryDirectory(prefix="iptv-env-") as tmp:
        env = Path(tmp) / "monitoring.env"
        subprocess.run(["scripts/secrets.sh", str(env)], cwd=REPO, capture_output=True, check=True)
        base = ["docker", "compose", "--project-directory", ".", "--env-file", str(env)]
        for label, files in {
            "dev": ["docker/compose.yml", "docker/compose.dev.yml"],
            "prod": ["docker/compose.yml", "docker/compose.prod.yml"],
        }.items():
            overlay = [
                "docker/compose.monitoring.yml",
                f"docker/compose.monitoring.{label}.yml",
            ]
            args = [arg for path in files + overlay for arg in ("-f", path)]
            results.append(run(f"compose: {label} with the overlay", *base, *args, "config", "-q"))
    return results


def main() -> int:
    print("monitoring configuration checks")
    results = [
        run(
            "dashboards: JSON up to date with the generator",
            sys.executable,
            str(MONITORING / "grafana" / "build_dashboards.py"),
            "--check",
        ),
    ]
    prometheus = pinned("prom/prometheus")
    results += prometheus_checks(prometheus)
    results.append(dashboard_expressions(prometheus))
    results += alertmanager_checks(pinned("prom/alertmanager"))
    results.append(
        docker(
            "loki: configuration",
            "-v",
            f"{MONITORING}/loki:/etc/loki:ro",
            pinned("grafana/loki"),
            "-config.file=/etc/loki/config.yml",
            "-config.expand-env=true",
            "-verify-config",
        )
    )
    alloy = pinned("grafana/alloy")
    results.append(
        docker(
            "alloy: configuration",
            "--entrypoint",
            "alloy",
            "-v",
            f"{MONITORING}/alloy:/etc/alloy:ro",
            alloy,
            "validate",
            "/etc/alloy/config.alloy",
        )
    )
    for name in ("config.alloy", "redaction.alloy"):
        formatted = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--entrypoint",
                "alloy",
                "-v",
                f"{MONITORING}/alloy:/etc/alloy:ro",
                alloy,
                "fmt",
                f"/etc/alloy/{name}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        same = (
            formatted.returncode == 0
            and formatted.stdout == (MONITORING / "alloy" / name).read_text()
        )
        print(f"  {'ok  ' if same else 'FAIL'}  alloy: {name} in canonical format (alloy fmt)")
        results.append(same)
    results.append(
        docker(
            "blackbox: modules",
            "-v",
            f"{MONITORING}/blackbox:/etc/blackbox:ro",
            pinned("prom/blackbox-exporter"),
            "--config.file=/etc/blackbox/blackbox.yml",
            "--config.check",
        )
    )
    results += compose_checks()
    failed = results.count(False)
    print(f"{'FAIL' if failed else 'PASS'}: {len(results) - failed}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
