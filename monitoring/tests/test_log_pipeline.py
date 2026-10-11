#!/usr/bin/env python3
"""End-to-end test of the log pipeline: Docker logs -> Alloy -> Loki (SPEC §11, §14).

It runs the production configuration unchanged (monitoring/alloy, monitoring/loki and
the access-log block of docker/traefik/traefik.yml) in throwaway containers:

- Loki and Alloy, with the same hardening as the compose overlay;
- one "emitter" container per service that prints the cases in redaction_cases.py, as
  that service would write them (Django, the Nginx edge, Traefik, third parties);
- a real Traefik with the project's access-log settings, sent requests that carry
  Xtream credentials in the path and the query, a media token, an Authorization header
  and cookies (the first line of defence: nothing secret is ever written).

Then it asserts, through Docker and Loki's API, that:

- Traefik's raw output holds none of the secrets it was sent, and both Traefik
  configurations (dev and production) share the same access-log block;
- every case arrived, labelled with service, container, project and stream;
- no secret value survived anywhere: log line, stream labels or structured metadata;
- each case contains its expected redacted text, and the control case is unchanged;
- request_id is structured metadata, from Django's and the edge's field and from
  Traefik's request and response header fields;

and, through Alloy's /metrics, that the Nginx edge lines produced the iptv_edge_*
metrics the Edges dashboard and the media alerts use.

Usage:  python3 monitoring/tests/test_log_pipeline.py      (make monitoring-test)
Needs Docker and the pinned images. Everything it creates is named <prefix>-<random>
(MONITORING_TEST_PREFIX, default "iptv-montest") and removed at the end; Loki, Alloy and
Traefik are published on 127.0.0.1 only, on free ports from MONITORING_TEST_PORT_BASE
(default 18000) upwards. Alloy reads the Docker socket read-only and only sees
containers carrying this run's compose project label.
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

from redaction_cases import CASES, Case

REPO = Path(__file__).resolve().parents[2]
OVERLAY = REPO / "docker" / "compose.monitoring.yml"
BASE = REPO / "docker" / "compose.yml"
TRAEFIK_CONFIGS = (
    REPO / "docker" / "traefik" / "traefik.yml",
    REPO / "docker" / "traefik" / "prod" / "traefik.yml",
)
SECRET_PLACEHOLDER = re.compile(r"<<(\w+)>>")
PUBLIC_PLACEHOLDER = re.compile(r"\[\[(\w+)\]\]")
CASE_MARKER = re.compile(r"case\W{1,4}(c\d\d)")
ALNUM = string.ascii_letters + string.digits
TIMEOUT_S = 180


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
    if name in {"basic_auth", "ts_basic"}:
        pair = f"{''.join(secrets.choice(ALNUM) for _ in range(10))}:{secrets.token_hex(12)}"
        return base64.b64encode(pair.encode()).decode()
    if name.startswith("edge_token") or name == "ts_token":
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
        lines[case.case_id] = PUBLIC_PLACEHOLDER.sub(
            public, SECRET_PLACEHOLDER.sub(secret, case.line)
        )
    return lines, secret_values, public_values


# --- Docker --------------------------------------------------------------------------


def docker(*args: str, check: bool = True) -> str:
    result = subprocess.run(["docker", *args], capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args[:3])} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def docker_logs(name: str) -> str:
    result = subprocess.run(["docker", "logs", name], capture_output=True, text=True, check=False)
    return result.stdout + result.stderr


def pinned_image(prefix: str, compose: Path = OVERLAY) -> str:
    """The image a compose file pins, so the test runs exactly what production runs."""
    match = re.search(rf"image:\s*({re.escape(prefix)}:\S+)", compose.read_text())
    if not match:
        raise RuntimeError(f"{prefix} image not found in {compose}")
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
    request = urllib.request.Request(url, headers=headers or {})  # noqa: S310 - local test URLs
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
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


# --- Traefik -------------------------------------------------------------------------


def access_log_block(path: Path) -> str:
    """The top-level `accessLog:` block of a Traefik static configuration, verbatim."""
    lines = path.read_text().splitlines()
    try:
        start = lines.index("accessLog:")
    except ValueError as error:
        raise RuntimeError(f"{path} has no accessLog block") from error
    block = [lines[start]]
    for line in lines[start + 1 :]:
        if line and not line.startswith((" ", "#")):
            break
        block.append(line)
    return "\n".join(block).rstrip() + "\n"


def traefik_config(workdir: Path) -> tuple[Path, Path]:
    """A minimal Traefik setup around the project's own access-log block."""
    static = workdir / "traefik.yml"
    static.write_text(
        'entryPoints:\n  web:\n    address: ":80"\n'
        "providers:\n  file:\n    filename: /etc/traefik/dynamic.yml\n"
        "ping: {}\nlog:\n  level: INFO\n  format: json\n\n" + access_log_block(TRAEFIK_CONFIGS[0])
    )
    dynamic = workdir / "dynamic.yml"
    # Every request goes to Loki's HTTP port: any backend that answers will do.
    dynamic.write_text(
        'http:\n  routers:\n    tv:\n      rule: "PathPrefix(`/`)"\n      service: backend\n'
        "  services:\n    backend:\n      loadBalancer:\n        servers:\n"
        '          - url: "http://loki:3100"\n'
    )
    for path in (static, dynamic):
        path.chmod(0o644)
    return static, dynamic


def traefik_requests(base: str, secret_values: dict[str, str], request_id: str) -> int:
    """Requests as IPTV apps, browsers and players send them; returns how many were sent."""
    user, password = secret_values["ts_user"], secret_values["ts_pass"]
    cookies = f"sessionid={secret_values['ts_cookie']}; csrftoken={secret_values['ts_csrf']}"
    sent = [
        (
            f"/get.php?username={user}&password={password}&type=m3u_plus",
            {
                "Authorization": f"Basic {secret_values['ts_basic']}",
                "Cookie": cookies,
                "X-Api-Key": secret_values["ts_key"],
                "X-Request-Id": request_id,
            },
        ),
        (f"/movie/{user}/{password}/1001.mp4", {"X-Request-Id": request_id}),
        (f"/{user}/{password}/77.ts", {}),
        (f"/v/{secret_values['ts_token']}/hls/seg-1.m4s", {"Range": "bytes=0-"}),
        (f"/player_api.php?username={user}&password={password}&action=get_vod_streams", {}),
    ]
    for path, headers in sent:
        http_get(base + path, headers)
    return len(sent)


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
    traefik_image = pinned_image("traefik", BASE)
    emitter_image = pinned_image("prom/prometheus")  # any pinned image with /bin/sh and cat
    lines, secret_values, public_values = render(CASES)
    for name in ("ts_user", "ts_pass", "ts_basic", "ts_cookie", "ts_csrf", "ts_key", "ts_token"):
        secret_values[name] = make_secret(name)
    traefik_rid = uuid.uuid4().hex
    services = sorted({case.service for case in CASES})
    failures = Failures()

    blocks = {access_log_block(path) for path in TRAEFIK_CONFIGS}
    failures.check(
        len(blocks) == 1,
        "docker/traefik/traefik.yml and docker/traefik/prod/traefik.yml differ in accessLog",
    )

    print(f"log pipeline test: {len(CASES)} cases, {len(secret_values)} secrets, prefix {prefix}")
    with tempfile.TemporaryDirectory(prefix="iptv-montest-") as tmp, environment(prefix) as created:
        workdir = Path(tmp)
        workdir.chmod(0o755)
        for service in services:
            body = "".join(lines[c.case_id] + "\n" for c in CASES if c.service == service)
            path = workdir / f"{service}.txt"
            path.write_text(body)
            path.chmod(0o644)
        static, dynamic = traefik_config(workdir)

        loki_port = free_port(port_base)
        created.append(f"{prefix}-loki")
        docker(
            "run", "-d", "--name", f"{prefix}-loki", "--network", f"{prefix}-net",
            "--network-alias", "loki", "-p", f"127.0.0.1:{loki_port}:3100",
            "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
            "--tmpfs", "/loki:rw,mode=1777", "--tmpfs", "/tmp:rw,mode=1777",
            "-v", f"{REPO}/monitoring/loki/config.yml:/etc/loki/config.yml:ro",
            loki_image, "-config.file=/etc/loki/config.yml", "-config.expand-env=true",
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

        # The real Traefik with the project's access-log settings, in front of Loki.
        traefik_port = free_port(loki_port + 1)
        traefik_name = f"{prefix}-traefik"
        created.append(traefik_name)
        docker(
            "run", "-d", "--name", traefik_name, "--network", f"{prefix}-net",
            "--label", f"com.docker.compose.project={project}",
            "--label", "com.docker.compose.service=traefik-live",
            "-p", f"127.0.0.1:{traefik_port}:80",
            "-v", f"{static}:/etc/traefik/traefik.yml:ro",
            "-v", f"{dynamic}:/etc/traefik/dynamic.yml:ro",
            traefik_image,
        )  # fmt: skip
        traefik = f"http://127.0.0.1:{traefik_port}"
        while time.monotonic() < deadline and http_get(f"{traefik}/ready")[0] == 0:
            time.sleep(1)
        sent = traefik_requests(traefik, secret_values, traefik_rid)

        alloy_port = free_port(traefik_port + 1)
        created.append(f"{prefix}-alloy")
        docker(
            "run", "-d", "--name", f"{prefix}-alloy", "--network", f"{prefix}-net",
            "-p", f"127.0.0.1:{alloy_port}:12345", "-e", f"ALLOY_COMPOSE_PROJECT={project}",
            "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
            "--tmpfs", "/alloy-data:rw", "--tmpfs", "/tmp:rw",
            "-v", f"{REPO}/monitoring/alloy:/etc/alloy:ro",
            "-v", "/var/run/docker.sock:/var/run/docker.sock:ro",
            alloy_image, "run", "/etc/alloy/config.alloy", "--storage.path=/alloy-data",
            "--server.http.listen-addr=0.0.0.0:12345", "--disable-reporting",
        )  # fmt: skip
        alloy = f"http://127.0.0.1:{alloy_port}"
        try:
            wait_for(f"{alloy}/-/ready", "Alloy", deadline)
        except RuntimeError:
            print(docker_logs(f"{prefix}-alloy")[-3000:], file=sys.stderr)
            raise

        # 1. The first line of defence: Traefik never wrote what it was sent.
        raw = docker_logs(traefik_name)
        access_lines = [line for line in raw.splitlines() if '"RouterName"' in line]
        failures.check(
            len(access_lines) >= sent,
            f"Traefik wrote {len(access_lines)} access lines for {sent} requests",
        )
        for name in (
            "ts_user",
            "ts_pass",
            "ts_basic",
            "ts_cookie",
            "ts_csrf",
            "ts_key",
            "ts_token",
        ):
            failures.check(secret_values[name] not in raw, f"Traefik's own log holds <<{name}>>")
        failures.check(
            any(f'"request_X-Request-Id":"{traefik_rid}"' in line for line in access_lines),
            "Traefik's access log lost the request id",
        )

        expected_ids = {case.case_id for case in CASES}
        entries: list[dict[str, object]] = []
        seen: dict[str, dict[str, object]] = {}
        traefik_entries: list[dict[str, object]] = []
        while time.monotonic() < deadline:
            entries = loki_query(loki, f'{{project="{project}"}}')
            seen = {}
            for entry in entries:
                marker = CASE_MARKER.search(str(entry["line"]))
                if marker:
                    seen[marker[1]] = entry
            traefik_entries = [
                entry
                for entry in entries
                if isinstance(entry["labels"], dict)
                and entry["labels"].get("service") == "traefik-live"
                and '"RouterName"' in str(entry["line"])
            ]
            if expected_ids <= seen.keys() and len(traefik_entries) >= sent:
                break
            time.sleep(2)
        missing = sorted(expected_ids - seen.keys())
        failures.check(not missing, f"cases never reached Loki: {missing}")
        failures.check(
            len(traefik_entries) >= sent,
            f"{len(traefik_entries)} of Traefik's {sent}+ access lines reached Loki",
        )
        print(f"  Loki returned {len(entries)} lines for {len(seen)}/{len(expected_ids)} cases")

        # 2. No secret anywhere: lines, stream labels and structured metadata.
        everything = json.dumps(entries)
        for name, value in sorted(secret_values.items()):
            failures.check(value not in everything, f"secret <<{name}>> reached Loki unredacted")

        # 3. Expected redactions, public values kept, labels set, control case unchanged.
        for case in CASES:
            entry = seen.get(case.case_id)
            if entry is None:
                continue
            line = str(entry["line"])
            labels = entry["labels"]
            assert isinstance(labels, dict)
            if case.unchanged:
                failures.check(line == lines[case.case_id], f"{case.case_id}: changed: {line}")
            for expected in case.expect:
                fragment = PUBLIC_PLACEHOLDER.sub(lambda m: public_values[m[1]], expected)
                failures.check(fragment in line, f"{case.case_id}: missing {fragment!r} in {line}")
            failures.check(
                labels.get("service") == case.service, f"{case.case_id}: labels {labels}"
            )
            failures.check(
                labels.get("project") == project, f"{case.case_id}: project label {labels}"
            )
            failures.check(
                labels.get("container") == f"{prefix}-emit-{case.service}",
                f"{case.case_id}: container label {labels}",
            )
            failures.check(
                labels.get("stream") == "stdout", f"{case.case_id}: stream label {labels}"
            )

        # 4. request_id as structured metadata: Django, the edge, Traefik's response and
        # request headers (fixtures), and the live Traefik's request header.
        rids = {
            key: public_values[key]
            for key in ("rid_web", "rid_traefik", "rid_edge", "rid_sso", "rid_console")
        }
        rids["rid_traefik_req"] = public_values["rid_traefik_req"]
        for key, rid in rids.items():
            found = loki_query(loki, f'{{project="{project}"}} | request_id="{rid}"')
            failures.check(len(found) == 1, f"request_id {key}: {len(found)} lines (want 1)")
        found = loki_query(loki, f'{{project="{project}"}} | request_id="{traefik_rid}"')
        failures.check(len(found) == 2, f"live Traefik request_id: {len(found)} lines (want 2)")

        # 5. Edge metrics derived from the Nginx edge access log.
        status, exposition = http_get(f"{alloy}/metrics")
        failures.check(status == 200, f"Alloy /metrics returned {status}")
        edge = {"edge": "edge-test"}
        expectations = [
            ("iptv_edge_requests_total", {**edge, "status_class": "2xx", "cache_status": "HIT"}, 1),
            (
                "iptv_edge_requests_total",
                {**edge, "status_class": "2xx", "cache_status": "MISS"},
                1,
            ),
            (
                "iptv_edge_requests_total",
                {**edge, "status_class": "5xx", "cache_status": "MISS"},
                1,
            ),
            (
                "iptv_edge_requests_total",
                {**edge, "status_class": "4xx", "cache_status": "NONE"},
                1,
            ),
            ("iptv_edge_sent_bytes_total", {**edge, "cache_status": "HIT"}, 1048576),
            ("iptv_edge_sent_bytes_total", {**edge, "cache_status": "MISS"}, 4194304 + 157),
            ("iptv_edge_request_duration_seconds_count", edge, 4),
            ("iptv_edge_request_duration_seconds_count", {**edge, "kind": "segment"}, 1),
            ("iptv_edge_request_duration_seconds_count", {**edge, "kind": "progressive"}, 2),
            ("iptv_edge_request_duration_seconds_count", {**edge, "kind": "playlist"}, 1),
            ("iptv_edge_auth_total", {**edge, "auth_cache": "HIT"}, 1),
            ("iptv_edge_auth_total", {**edge, "auth_cache": "MISS"}, 1),
            ("iptv_edge_denied_total", {**edge, "reason": "unavailable"}, 1),
            ("iptv_edge_denied_total", {**edge, "reason": "expired"}, 1),
        ]
        for name, labels, want in expectations:
            got = metric_value(exposition, name, **labels)
            failures.check(got == want, f"{name}{labels} = {got}, want {want}")
        failures.check(
            metric_value(exposition, "iptv_edge_auth_total") == 2,
            "stream-auth results: only HIT and MISS lines count",
        )
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
        f"PASS: {len(CASES)} cases redacted, {len(secret_values)} secrets absent from Traefik's "
        "output and from Loki, labels, request_id metadata and edge metrics as expected"
    )
    return 0


if __name__ == "__main__":
    sys.exit(run())
