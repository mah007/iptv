#!/usr/bin/env python3
"""End-to-end test of the log pipeline: Docker logs -> Alloy -> Loki (SPEC §11, §14).

It runs the production configuration unchanged (monitoring/alloy, monitoring/loki) in
throwaway containers: Loki, Alloy (with the same hardening as the compose overlay) and
one "emitter" container per service that prints the cases in redaction_cases.py. Then,
through Loki's API, it asserts that:

- every case arrived, labelled with service, container, project and stream;
- no secret value survived anywhere: log line, stream labels or structured metadata;
- each case contains its expected redacted text, and the control case is unchanged;
- request_id is structured metadata, from Django's field and Traefik's header field;

and, through Alloy's /metrics, that the Nginx edge lines produced the iptv_edge_*
metrics the Edges dashboard and the media alerts use.

Usage:  python3 monitoring/tests/test_log_pipeline.py
Needs Docker and network access to pull the pinned images. Everything it creates is
named <prefix>-<random> (MONITORING_TEST_PREFIX, default "iptv-montest") and removed at
the end; Loki and Alloy are published on 127.0.0.1 only, on free ports from
MONITORING_TEST_PORT_BASE (default 18000) upwards. Alloy reads the Docker socket
read-only and only sees containers carrying this run's compose project label.
"""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
import socket
import string
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from redaction_cases import CASES, Case  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
OVERLAY = REPO / "docker" / "compose.monitoring.yml"
SECRET_PLACEHOLDER = re.compile(r"<<(\w+)>>")
PUBLIC_PLACEHOLDER = re.compile(r"\[\[(\w+)\]\]")
CASE_MARKER = re.compile(r"case\W{1,4}(c\d\d)")
ALNUM = string.ascii_letters + string.digits
TIMEOUT_S = 150


# --- Secrets -------------------------------------------------------------------------


def _b64url(n_bytes: int) -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(n_bytes)).decode().rstrip("=")


def make_secret(name: str) -> str:
    """A fresh random value shaped like the real thing, so the shape-based rules apply."""
    if name == "jwt":
        return f"eyJ{_b64url(18)}.eyJ{_b64url(30)}.{_b64url(32)}"
    if name == "tg_token":
        digits = str(secrets.randbelow(9 * 10**9) + 10**9)
        tail = "".join(secrets.choice(ALNUM + "_-") for _ in range(35))
        return f"{digits}:{tail}"
    if name == "otp_digits":
        return str(secrets.randbelow(9 * 10**7) + 10**7)
    if name == "basic_auth":
        pair = f"{''.join(secrets.choice(ALNUM) for _ in range(10))}:{secrets.token_hex(12)}"
        return base64.b64encode(pair.encode()).decode()
    if name.startswith("edge_token"):
        return _b64url(30)
    return "".join(secrets.choice(ALNUM) for _ in range(20))


def render(cases: tuple[Case, ...]) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Fill the placeholders. Returns (rendered line per case, secrets, public values)."""
    secret_values: dict[str, str] = {}
    public_values: dict[str, str] = {}
    lines: dict[str, str] = {}

    def secret(match: re.Match[str]) -> str:
        return secret_values.setdefault(match[1], make_secret(match[1]))

    def public(match: re.Match[str]) -> str:
        return public_values.setdefault(match[1], uuid.uuid4().hex)

    for case in cases:
        lines[case.case_id] = PUBLIC_PLACEHOLDER.sub(public, SECRET_PLACEHOLDER.sub(secret, case.line))
    return lines, secret_values, public_values


# --- Docker --------------------------------------------------------------------------


def docker(*args: str, check: bool = True) -> str:
    result = subprocess.run(["docker", *args], capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args[:3])} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def pinned_image(prefix: str) -> str:
    """The image tag the overlay pins, so the test runs exactly what production runs."""
    match = re.search(rf"image:\s*({re.escape(prefix)}:\S+)", OVERLAY.read_text())
    if not match:
        raise RuntimeError(f"{prefix} image not found in {OVERLAY}")
    return match[1]


def free_port(start: int) -> int:
    for port in range(start, start + 1000):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"no free port in {start}-{start + 999}")


@contextmanager
def environment(prefix: str) -> Iterator[list[str]]:
    """Track created containers and the network; remove them whatever happens."""
    containers: list[str] = []
    try:
        docker("network", "create", f"{prefix}-net")
        yield containers
    finally:
        for name in containers:
            docker("rm", "-f", name, check=False)
        docker("network", "rm", f"{prefix}-net", check=False)


# --- HTTP ----------------------------------------------------------------------------


def http_get(url: str, headers: dict[str, str] | None = None) -> tuple[int, str]:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()
    except (urllib.error.URLError, ConnectionError, TimeoutError):
        return 0, ""


def wait_for(url: str, what: str, deadline: float) -> None:
    while time.monotonic() < deadline:
        status, _ = http_get(url)
        if status == 200:
            return
        time.sleep(1)
    raise RuntimeError(f"{what} did not become ready ({url})")


def loki_query(loki: str, query: str) -> list[dict[str, object]]:
    """All entries for a LogQL query over the last hour, as dicts (labels, line, metadata)."""
    now_ns = time.time_ns()
    params = urllib.parse.urlencode(
        {
            "query": query,
            "start": now_ns - 3600 * 10**9,
            "end": now_ns + 60 * 10**9,
            "limit": 5000,
            "direction": "forward",
        }
    )
    status, body = http_get(
        f"{loki}/loki/api/v1/query_range?{params}",
        {"X-Loki-Response-Encoding-Flags": "categorize-labels"},
    )
    if status != 200:
        raise RuntimeError(f"Loki query failed ({status}): {body[:300]}")
    entries: list[dict[str, object]] = []
    for stream in json.loads(body)["data"]["result"]:
        for value in stream["values"]:
            metadata = value[2].get("structuredMetadata", {}) if len(value) > 2 else {}
            entries.append({"labels": stream["stream"], "line": value[1], "metadata": metadata})
    return entries


# --- Metrics -------------------------------------------------------------------------

SAMPLE = re.compile(r"^([a-zA-Z_:][\w:]*)(?:\{(.*)\})?\s+(\S+)")
LABEL = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')


def metric_value(exposition: str, name: str, **labels: str) -> float | None:
    """Sum of the samples of `name` whose labels include `labels`."""
    total: float | None = None
    for raw in exposition.splitlines():
        match = SAMPLE.match(raw)
        if not match or match[1] != name:
            continue
        sample_labels = dict(LABEL.findall(match[2] or ""))
        if all(sample_labels.get(key) == value for key, value in labels.items()):
            total = (total or 0.0) + float(match[3])
    return total


# --- Test ----------------------------------------------------------------------------


class Failures:
    def __init__(self) -> None:
        self.items: list[str] = []

    def check(self, condition: bool, message: str) -> None:
        if not condition:
            self.items.append(message)


def run() -> int:
    prefix = f"{os.environ.get('MONITORING_TEST_PREFIX', 'iptv-montest')}-{secrets.token_hex(3)}"
    port_base = int(os.environ.get("MONITORING_TEST_PORT_BASE", "18000"))
    project = f"{prefix}-project"
    loki_image = pinned_image("grafana/loki")
    alloy_image = pinned_image("grafana/alloy")
    emitter_image = pinned_image("prom/prometheus")  # any pinned image with /bin/sh and cat
    lines, secret_values, public_values = render(CASES)
    services = sorted({case.service for case in CASES})
    failures = Failures()

    print(f"log pipeline test: {len(CASES)} cases, {len(secret_values)} secrets, prefix {prefix}")
    with tempfile.TemporaryDirectory(prefix="iptv-montest-") as workdir, environment(prefix) as created:
        for service in services:
            body = "".join(lines[c.case_id] + "\n" for c in CASES if c.service == service)
            path = Path(workdir) / f"{service}.txt"
            path.write_text(body)
            path.chmod(0o644)

        loki_port = free_port(port_base)
        created.append(f"{prefix}-loki")
        docker(
            "run", "-d", "--name", f"{prefix}-loki", "--network", f"{prefix}-net",
            "--network-alias", "loki", "-p", f"127.0.0.1:{loki_port}:3100",
            "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
            "--tmpfs", "/loki:rw,mode=1777", "--tmpfs", "/tmp:rw,mode=1777",
            "-v", f"{REPO}/monitoring/loki/config.yml:/etc/loki/config.yml:ro",
            loki_image, "-config.file=/etc/loki/config.yml",
        )  # fmt: skip
        for service in services:
            created.append(f"{prefix}-emit-{service}")
            docker(
                "run", "-d", "--name", f"{prefix}-emit-{service}", "--network", "none",
                "--label", f"com.docker.compose.project={project}",
                "--label", f"com.docker.compose.service={service}",
                "-v", f"{workdir}/{service}.txt:/lines.txt:ro",
                "--entrypoint", "/bin/sh", emitter_image, "-c", "cat /lines.txt && exec sleep 600",
            )  # fmt: skip

        loki = f"http://127.0.0.1:{loki_port}"
        deadline = time.monotonic() + TIMEOUT_S
        wait_for(f"{loki}/ready", "Loki", deadline)

        alloy_port = free_port(loki_port + 1)
        created.append(f"{prefix}-alloy")
        docker(
            "run", "-d", "--name", f"{prefix}-alloy", "--network", f"{prefix}-net",
            "-p", f"127.0.0.1:{alloy_port}:12345", "-e", f"ALLOY_COMPOSE_PROJECT={project}",
            "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
            "--tmpfs", "/var/lib/alloy/data:rw", "--tmpfs", "/tmp:rw",
            "-v", f"{REPO}/monitoring/alloy:/etc/alloy:ro",
            "-v", "/var/run/docker.sock:/var/run/docker.sock:ro",
            alloy_image, "run", "/etc/alloy/config.alloy", "--storage.path=/var/lib/alloy/data",
            "--server.http.listen-addr=0.0.0.0:12345", "--disable-reporting",
        )  # fmt: skip
        alloy = f"http://127.0.0.1:{alloy_port}"
        wait_for(f"{alloy}/-/ready", "Alloy", deadline)

        expected_ids = {case.case_id for case in CASES}
        entries: list[dict[str, object]] = []
        seen: dict[str, dict[str, object]] = {}
        while time.monotonic() < deadline:
            entries = loki_query(loki, f'{{project="{project}"}}')
            seen = {}
            for entry in entries:
                marker = CASE_MARKER.search(str(entry["line"]))
                if marker:
                    seen[marker[1]] = entry
            if expected_ids <= seen.keys():
                break
            time.sleep(2)
        missing = sorted(expected_ids - seen.keys())
        failures.check(not missing, f"cases never reached Loki: {missing}")
        print(f"  Loki returned {len(entries)} lines for {len(seen)}/{len(expected_ids)} cases")

        # 1. No secret anywhere: lines, stream labels and structured metadata.
        everything = json.dumps(entries)
        for name, value in sorted(secret_values.items()):
            failures.check(value not in everything, f"secret <<{name}>> reached Loki unredacted")

        # 2. Expected redactions, public values kept, labels set, control case unchanged.
        for case in CASES:
            entry = seen.get(case.case_id)
            if entry is None:
                continue
            line = str(entry["line"])
            labels = entry["labels"]
            assert isinstance(labels, dict)
            if case.unchanged:
                failures.check(line == lines[case.case_id], f"{case.case_id}: changed: {line}")
            for fragment in case.expect:
                fragment = PUBLIC_PLACEHOLDER.sub(lambda m: public_values[m[1]], fragment)
                failures.check(fragment in line, f"{case.case_id}: missing {fragment!r} in {line}")
            failures.check(labels.get("service") == case.service, f"{case.case_id}: labels {labels}")
            failures.check(labels.get("project") == project, f"{case.case_id}: project label {labels}")
            failures.check(
                labels.get("container") == f"{prefix}-emit-{case.service}",
                f"{case.case_id}: container label {labels}",
            )
            failures.check(labels.get("stream") == "stdout", f"{case.case_id}: stream label {labels}")

        # 3. request_id as structured metadata, from Django/Nginx and from Traefik's header.
        for key in ("rid_web", "rid_traefik", "rid_edge"):
            rid = public_values[key]
            found = loki_query(loki, f'{{project="{project}"}} | request_id="{rid}"')
            failures.check(len(found) == 1, f"request_id {key}: {len(found)} lines (want 1)")

        # 4. Edge metrics derived from the Nginx edge access log.
        status, exposition = http_get(f"{alloy}/metrics")
        failures.check(status == 200, f"Alloy /metrics returned {status}")
        edge = {"edge": "edge-test"}
        expectations = [
            ("iptv_edge_requests_total", {**edge, "status_class": "2xx", "cache_status": "HIT"}, 1),
            ("iptv_edge_requests_total", {**edge, "status_class": "2xx", "cache_status": "MISS"}, 1),
            ("iptv_edge_requests_total", {**edge, "status_class": "5xx", "cache_status": "MISS"}, 1),
            ("iptv_edge_requests_total", {**edge, "status_class": "4xx", "cache_status": "NONE"}, 1),
            ("iptv_edge_sent_bytes_total", {**edge, "cache_status": "HIT"}, 1048576),
            ("iptv_edge_sent_bytes_total", {**edge, "cache_status": "MISS"}, 4194304 + 157),
            ("iptv_edge_request_duration_seconds_count", edge, 4),
            ("iptv_edge_upstream_header_seconds_count", edge, 2),
        ]
        for name, labels, want in expectations:
            got = metric_value(exposition, name, **labels)
            failures.check(got == want, f"{name}{labels} = {got}, want {want}")
        duration_sum = metric_value(exposition, "iptv_edge_request_duration_seconds_sum", **edge)
        failures.check(
            duration_sum is not None and abs(duration_sum - 5.972) < 1e-6,
            f"iptv_edge_request_duration_seconds_sum = {duration_sum}, want 5.972",
        )
        failures.check(
            metric_value(exposition, "iptv_edge_requests_total", service="web") is None,
            "edge metrics must only count the nginx-stream service",
        )

        if failures.items:
            print(docker("logs", "--tail", "40", f"{prefix}-alloy", check=False), file=sys.stderr)

    if failures.items:
        print(f"FAIL: {len(failures.items)} problem(s)")
        for item in failures.items:
            print(f"  - {item}")
        return 1
    print(
        f"PASS: {len(CASES)} cases redacted, {len(secret_values)} secrets absent from Loki, "
        "labels, request_id metadata and edge metrics as expected"
    )
    return 0


if __name__ == "__main__":
    sys.exit(run())
