#!/usr/bin/env python3
"""Smoke test of the running monitoring overlay (make monitoring-smoke, ADR-0018).

Against the dev stack started with MONITORING=1, it checks that:

- every Prometheus scrape target is up, the alert rules are loaded and the
  always-firing Watchdog has reached Alertmanager;
- grafana.<domain> refuses a browser without a monitoring session (a page goes to the
  admin to sign in, an API call gets 401), and an admin's one-time ticket opens
  Grafana (the seven provisioned dashboards) and Prometheus behind the same check;
- one request id is found in Loki from Traefik and Django (an Xtream call) and from
  Traefik and the media edge (a media request): the correlation of SPEC §14.

Usage: python3 monitoring/tests/smoke.py --domain localhost --port 8080
"""

from __future__ import annotations

import argparse
import http.client
import json
import subprocess
import sys
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

PROJECT = "smart-iptv"
DASHBOARDS = 7


def container(service: str) -> str:
    found = subprocess.run(
        ["docker", "ps", "-q", "--filter", f"label=com.docker.compose.project={PROJECT}",
         "--filter", f"label=com.docker.compose.service={service}"],
        capture_output=True, text=True, check=True,
    ).stdout.split()  # fmt: skip
    if not found:
        raise RuntimeError(f"the {service} container is not running (make up MONITORING=1)")
    return found[0]


def fetch_in(service: str, url: str) -> Any:
    """GET a URL from inside a monitoring container (busybox wget) and parse its JSON."""
    output = subprocess.run(
        ["docker", "exec", container(service), "wget", "-q", "-O", "-", url],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    return json.loads(output)


@dataclass
class Reply:
    status: int
    headers: http.client.HTTPMessage
    body: bytes

    def header(self, name: str) -> str:
        return self.headers.get(name) or ""


class Browser:
    """A tiny cookie-keeping HTTP client for Traefik on the host."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.cookies: dict[str, str] = {}

    def get(self, host: str, path: str, **headers: str) -> Reply:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        cookie = "; ".join(f"{key}={value}" for key, value in self.cookies.items())
        sent = {"Host": host, **headers}
        if cookie:
            sent["Cookie"] = cookie
        connection.request("GET", path, headers=sent)
        response = connection.getresponse()
        reply = Reply(response.status, response.headers, response.read())
        for header in response.headers.get_all("Set-Cookie") or []:
            name, _, rest = header.partition("=")
            value = rest.split(";", 1)[0]
            if "max-age=0" in header.lower() or not value:
                self.cookies.pop(name.strip(), None)
            else:
                self.cookies[name.strip()] = value
        return reply


class Checks:
    def __init__(self) -> None:
        self.failed = 0

    def check(self, condition: bool, message: str) -> None:
        print(f"  {'ok  ' if condition else 'FAIL'}  {message}")
        self.failed += 0 if condition else 1


def retry(attempt: Callable[[], bool], seconds: int) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if attempt():
            return True
        time.sleep(3)
    return attempt()


def prometheus(checks: Checks) -> None:
    base = "http://127.0.0.1:9090/prometheus/api/v1"

    def targets_up() -> bool:
        targets = fetch_in("prometheus", f"{base}/targets?state=active")["data"]
        active = targets["activeTargets"]  # type: ignore[index]
        down = [f"{t['labels']['job']} {t['scrapeUrl']}" for t in active if t["health"] != "up"]
        prometheus.down = down  # type: ignore[attr-defined]
        return bool(active) and not down

    checks.check(
        retry(targets_up, 90),
        f"every scrape target is up {getattr(prometheus, 'down', [])}",
    )
    rules = fetch_in("prometheus", f"{base}/rules?type=alert")["data"]
    names = {rule["name"] for group in rules["groups"] for rule in group["rules"]}  # type: ignore[index]
    checks.check(len(names) >= 27, f"{len(names)} alert rules loaded")

    def watchdog() -> bool:
        alerts = fetch_in("alertmanager", "http://127.0.0.1:9093/alertmanager/api/v2/alerts")
        return any(a["labels"]["alertname"] == "Watchdog" for a in alerts)  # type: ignore[union-attr]

    checks.check(retry(watchdog, 90), "the Watchdog alert reached Alertmanager")


def sign_in_ticket() -> str:
    """A one-time monitoring ticket for the first owner (dev only, through Django's shell)."""
    code = (
        "from apps.accounts.models import User;"
        "from apps.accounts.monitoring import issue_ticket;"
        "u=User.objects.filter(is_staff=True,roles__name='owner').first();"
        "print(issue_ticket(u))"
    )
    output = subprocess.run(
        ["docker", "compose", "--project-directory", ".", "-f", "docker/compose.yml",
         "-f", "docker/compose.dev.yml", "exec", "-T", "web", "python", "manage.py", "shell",
         "-c", code],
        capture_output=True, text=True, check=True,
    ).stdout.strip().splitlines()[-1]  # fmt: skip
    return urllib.parse.parse_qs(urllib.parse.urlsplit(output).query)["ticket"][0]


def grafana(checks: Checks, domain: str, port: int) -> None:
    host = f"grafana.{domain}"
    anonymous = Browser(port)
    page = anonymous.get(host, "/d/iptv-edges", Accept="text/html")
    checks.check(
        page.status == 302 and f"admin.{domain}" in page.header("Location"),
        "without a session, a Grafana page sends the browser to the admin",
    )
    api = anonymous.get(host, "/api/search", Accept="application/json")
    checks.check(api.status == 401, "without a session, Grafana's API answers 401")
    spoof = anonymous.get(
        host, "/api/search", Accept="application/json", **{"X-WEBAUTH-USER": "owner"}
    )
    checks.check(spoof.status == 401, "a forged X-WEBAUTH-USER header is refused")

    browser = Browser(port)
    callback = browser.get(host, f"/_sso/callback?ticket={sign_in_ticket()}")
    checks.check(callback.status == 302 and bool(browser.cookies), "a ticket opens a session")
    search = browser.get(host, "/api/search?type=dash-db&tag=smart-iptv", Accept="application/json")
    dashboards = json.loads(search.body) if search.status == 200 else []
    checks.check(
        search.status == 200 and len(dashboards) == DASHBOARDS,
        f"Grafana lists {len(dashboards)} provisioned dashboards (want {DASHBOARDS})",
    )
    query = browser.get(host, "/prometheus/api/v1/query?query=up", Accept="application/json")
    checks.check(query.status == 200, "Prometheus answers behind the same sign-in")
    alerts = browser.get(host, "/alertmanager/api/v2/status", Accept="application/json")
    checks.check(alerts.status == 200, "Alertmanager answers behind the same sign-in")
    browser.get(host, "/_sso/logout")
    after = browser.get(host, "/api/search", Accept="application/json")
    checks.check(after.status == 401, "after signing out, Grafana's API answers 401")


def loki_services(request_id: str) -> set[str]:
    query = f'{{project="{PROJECT}"}} | request_id="{request_id}"'
    now = time.time_ns()
    params = urllib.parse.urlencode(
        {"query": query, "start": now - 900 * 10**9, "end": now + 60 * 10**9, "limit": 100}
    )
    # From Grafana's container, which queries Loki anyway (Prometheus's busybox
    # wget cannot resolve every service name).
    data = fetch_in("grafana", f"http://loki:3100/loki/api/v1/query_range?{params}")
    return {stream["stream"].get("service", "") for stream in data["data"]["result"]}  # type: ignore[index]


def correlation(checks: Checks, domain: str, port: int) -> None:
    browser = Browser(port)
    xtream = browser.get(f"tv.{domain}", "/player_api.php?username=smoke&password=wrong-smoke")
    media = browser.get(f"media.{domain}", "/v/not-a-token/compat.mp4")
    for response, want, label in (
        (xtream, {"traefik", "web"}, "an Xtream call (Traefik and Django)"),
        (media, {"traefik", "nginx-stream"}, "a media request (Traefik and the edge)"),
    ):
        request_id = response.header("X-Request-Id")
        found: set[str] = set()

        def arrived(rid: str = request_id, want: set[str] = want, seen: set[str] = found) -> bool:
            seen.clear()
            seen.update(loki_services(rid))
            return want <= seen

        checks.check(
            bool(request_id) and retry(arrived, 90),
            f"one request id in Loki for {label}: {sorted(found)}",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--domain", default="localhost")
    parser.add_argument("--port", type=int, default=80)
    args = parser.parse_args()
    checks = Checks()
    print("monitoring smoke test")
    prometheus(checks)
    grafana(checks, args.domain, args.port)
    correlation(checks, args.domain, args.port)
    print("PASS" if not checks.failed else f"FAIL: {checks.failed} check(s)")
    return 1 if checks.failed else 0


if __name__ == "__main__":
    sys.exit(main())
