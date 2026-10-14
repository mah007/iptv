#!/usr/bin/env python3
"""`make loadtest`: the Locust tiers against the local dev stack (M15, ADR-0022).

Standard library only. For each tier it runs Locust (loadtests/locust/locustfile.py,
the pinned image, host network, 127.0.0.1 only) and, meanwhile:
- a **memory guard**: it stops Locust when the host's available memory falls under
  --min-free-mb. MemAvailable counts everything on the machine, other projects'
  containers included;
- the **slot check**: every 10 s, the contention customers' active streams. Any group
  over its two streams is an over-allocation;
- **container figures** every 15 s (CPU and memory of web, the edge, the data stores,
  Traefik and Locust).

Before the tiers:
1. It refuses while an E2E run, a gate or ZAP is going.
2. It switches web to production gunicorn with Traefik's per-address limits raised
   (loadtests/compose.loadtest.yml).
3. It creates the throwaway customers (`manage.py loadtest_data`).

Afterwards it always deletes them and puts web back as it was. The results land in
loadtests/results/<UTC time>/: Locust's CSVs, summary.json and summary.md.

    python3 loadtests/run_load.py --tiers 100,500,1000,5000 [--hold 120] [--min-free-mb 1500]

The compose command comes from LT_COMPOSE (the Makefile's $(COMPOSE)).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import secrets
import shlex
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LOCUST_IMAGE = "locustio/locust:2.46.6@sha256:d43616228012a7d79b883e2ecd42f87640469377f2b666c4ab27e5248dfdf35f"
CONTAINER = "smart-iptv-locust"
OVERLAY = ["-f", "loadtests/compose.loadtest.yml"]
WATCHED = ("web", "nginx-stream", "postgres", "redis-state", "redis-cache", "traefik", "worker", "locust", "odoo")
BUSY = (["pgrep", "-f", "playwright"], ["pgrep", "-f", "make ci"])


def say(message: str) -> None:
    print(f"loadtest: {message}", flush=True)


def compose_base() -> list[str]:
    default = "docker compose --project-directory . -f docker/compose.yml -f docker/compose.dev.yml"
    raw = os.environ.get("LT_COMPOSE", default)
    return shlex.split(raw)


def run(cmd: list[str], *, check: bool = True, capture: bool = False, timeout: float = 900) -> str:
    result = subprocess.run(
        cmd, cwd=ROOT, check=check, text=True, timeout=timeout,
        stdout=subprocess.PIPE if capture else None, stderr=subprocess.PIPE if capture else None,
    )  # fmt: skip
    return result.stdout or ""


def http_port() -> str:
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("HTTP_PORT="):
            return line.split("=", 1)[1].strip().strip("'\"") or "80"
    return "80"


def mem_available_mb() -> int:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) // 1024
    return 0


def ancestors() -> set[str]:
    """This process and its parents: a shell that started us may mention the words."""
    found, pid = set(), os.getpid()
    while pid > 1:
        found.add(str(pid))
        try:
            pid = int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
    return found


def busy() -> list[str]:
    found = []
    mine = ancestors()
    for cmd in BUSY:
        out = subprocess.run(cmd, capture_output=True, text=True, check=False).stdout.split()
        if [pid for pid in out if pid not in mine]:
            found.append(cmd[-1])
    names = run(["docker", "ps", "--format", "{{.Names}} {{.Image}}"], capture=True)
    found += [line for line in names.splitlines() if "zap" in line.lower()]
    return found


def manage(*args: str) -> str:
    # The worker keeps the dev settings (DEBUG), which loadtest_data requires.
    return run(
        [*compose_base(), "exec", "-T", "-e", "LOADTEST_PASSWORD", "worker", "python", "manage.py",
         "loadtest_data", *args],
        capture=True, timeout=3600,
    )  # fmt: skip


def container_stats() -> list[dict[str, Any]]:
    out = run(["docker", "stats", "--no-stream", "--format", "{{json .}}"], capture=True, check=False)
    rows = []
    for line in out.splitlines():
        row = json.loads(line)
        if any(word in row.get("Name", "") for word in WATCHED):
            rows.append({k: row.get(k) for k in ("Name", "CPUPerc", "MemUsage")})
    return rows


def contention_groups(users: int) -> int:
    """About a tenth of the users race for slots, in groups of four devices."""
    return max(2, users // 40)


def summarise(prefix: Path) -> dict[str, Any]:
    stats_file = prefix.with_name(prefix.name + "_stats.csv")
    rows: dict[str, Any] = {}
    if not stats_file.exists():
        return rows
    with stats_file.open() as handle:
        for row in csv.DictReader(handle):
            rows[row["Name"]] = {
                "requests": int(row["Request Count"]),
                "failures": int(row["Failure Count"]),
                "p50_ms": row["50%"],
                "p95_ms": row["95%"],
                "p99_ms": row["99%"],
                "max_ms": row["Max Response Time"],
                "rps": round(float(row["Requests/s"]), 1),
            }
    return rows


def steady(prefix: Path, after: float) -> dict[str, Any]:
    """Per request name, the 10-second windows' p95 once the ramp is over: their median
    and their worst (Locust's history rows carry the current window's percentiles).
    Meaningful for what repeats (heartbeats, browsing, contention); a name asked only
    during the ramp (sign-in, the first play) keeps its last window: read the totals."""
    history = prefix.with_name(prefix.name + "_stats_history.csv")
    windows: dict[str, list[float]] = {}
    if not history.exists():
        return {}
    with history.open() as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        return {}
    start = int(rows[0]["Timestamp"])
    for row in rows:
        if int(row["Timestamp"]) - start < after or row["95%"] in ("N/A", ""):
            continue
        if float(row["95%"]) > 0:  # 0: no request of that name in the window
            windows.setdefault(row["Name"], []).append(float(row["95%"]))
    return {
        name: {"p95_median_ms": sorted(v)[len(v) // 2], "p95_worst_ms": max(v), "windows": len(v)}
        for name, v in windows.items()
    }


def run_tier(users: int, args: argparse.Namespace, out: Path, groups: int) -> dict[str, Any]:
    prefix = out / f"tier{users}"
    viewers = users - groups * 4
    spawn = min(args.max_spawn, max(10, users // 10))
    ramp_s = users // spawn + 1
    run_time = ramp_s + args.hold
    env = {
        **os.environ,
        "LT_VIEWERS": str(viewers),
        "LT_GROUPS": str(groups),
        "LT_PROCESS_STRIDE": str(args.processes),
    }
    cmd = [
        "docker", "run", "--rm", "--name", CONTAINER, "--network", "host",
        "-e", "LOADTEST_PASSWORD", "-e", "LT_VIEWERS", "-e", "LT_GROUPS", "-e", "LT_PROCESS_STRIDE",
        "-v", f"{ROOT / 'loadtests' / 'locust'}:/mnt/locust:ro", "-v", f"{out}:/mnt/out",
        LOCUST_IMAGE, "-f", "/mnt/locust/locustfile.py", "--headless", "--only-summary",
        "-u", str(users), "-r", str(spawn), "-t", f"{run_time}s", "--stop-timeout", "10",
        "--host", f"http://127.0.0.1:{http_port()}", "--processes", str(args.processes),
        "--csv", f"/mnt/out/{prefix.name}", "--csv-full-history",
    ]  # fmt: skip
    say(f"tier {users}: {viewers} viewers, {groups * 4} contention devices; ramp {spawn}/s, then {args.hold} s")
    started = time.monotonic()
    log = (out / f"{prefix.name}.log").open("w")
    locust = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    guard: dict[str, Any] = {"min_free_mb": mem_available_mb(), "aborted": False}
    slots: dict[str, Any] = {"samples": 0, "max": 0, "over": 0, "group_streams_max": 0, "viewer_streams_max": 0}
    stats: list[dict[str, Any]] = []
    stop = threading.Event()

    def check_slots() -> None:
        while not stop.wait(10):
            try:
                sample = json.loads(manage("--slots").strip().splitlines()[-1])
            except (subprocess.SubprocessError, ValueError, IndexError):
                continue
            slots["samples"] += 1
            slots["max"] = max(slots["max"], sample["max"])
            slots["over"] += sample["over"]
            slots["group_streams_max"] = max(slots["group_streams_max"], sample["group_streams"])
            slots["viewer_streams_max"] = max(slots["viewer_streams_max"], sample["viewer_streams"])

    def sample_stats() -> None:
        while not stop.wait(15):
            stats.append({"t": round(time.monotonic() - started), "containers": container_stats()})

    threads = [threading.Thread(target=check_slots, daemon=True), threading.Thread(target=sample_stats, daemon=True)]
    for thread in threads:
        thread.start()
    while locust.poll() is None:
        free = mem_available_mb()
        guard["min_free_mb"] = min(guard["min_free_mb"], free)
        if free < args.min_free_mb:
            say(f"memory guard: {free} MB available (< {args.min_free_mb}); stopping Locust")
            guard["aborted"] = True
            subprocess.run(["docker", "stop", "-t", "5", CONTAINER], check=False, capture_output=True)
            break
        time.sleep(2)
    locust.wait()
    stop.set()
    log.close()
    result = {
        "users": users,
        "viewers": viewers,
        "contention_devices": groups * 4,
        "spawn_per_s": spawn,
        "hold_s": args.hold,
        "seconds": round(time.monotonic() - started),
        "memory_guard": guard,
        "slots": slots,
        "requests": summarise(prefix),
        # Steady state: from 10 s after the ramp ended.
        "steady": steady(prefix, ramp_s + 10),
        "containers": stats,
    }
    say(
        f"tier {users}: done in {result['seconds']} s; slots max {slots['max']} (over: {slots['over']}); "
        f"lowest free memory {guard['min_free_mb']} MB{' (ABORTED)' if guard['aborted'] else ''}"
    )
    return result


def markdown(results: list[dict[str, Any]]) -> str:
    lines = [
        "| Tier | Name | Requests | Failures | p50 ms | p95 ms | p99 ms | req/s |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for tier in results:
        for name, row in tier["requests"].items():
            lines.append(
                f"| {tier['users']} | {name} | {row['requests']} | {row['failures']} | {row['p50_ms']} "
                f"| {row['p95_ms']} | {row['p99_ms']} | {row['rps']} |"
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--tiers", default="100,500,1000")
    parser.add_argument("--hold", type=int, default=120, help="seconds at full load per tier")
    parser.add_argument("--min-free-mb", type=int, default=1500)
    parser.add_argument("--processes", type=int, default=4, help="Locust worker processes")
    parser.add_argument("--max-spawn", type=int, default=50, help="new users per second at most")
    parser.add_argument("--cooldown", type=int, default=100, help="seconds between tiers (slots lapse after 90)")
    args = parser.parse_args()
    tiers = [int(t) for t in args.tiers.split(",") if t.strip()]
    found = busy()
    if found:
        say(f"refusing: something else is running ({', '.join(found)})")
        return 2
    if mem_available_mb() < args.min_free_mb + 1000:
        say(f"refusing: only {mem_available_mb()} MB available")
        return 2
    out = ROOT / "loadtests" / "results" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out.mkdir(parents=True)
    os.environ["LOADTEST_PASSWORD"] = secrets.token_urlsafe(18)
    groups = contention_groups(max(tiers))
    results: list[dict[str, Any]] = []
    try:
        say("web: production gunicorn, Traefik's per-address limits raised")
        run([*compose_base(), *OVERLAY, "up", "-d", "--no-deps", "--wait", "web"])
        say(f"customers: {max(tiers) - groups * 4} viewers, {groups} contention groups")
        say(manage("--viewers", str(max(tiers) - groups * 4), "--groups", str(groups)).strip())
        run(["docker", "pull", "-q", LOCUST_IMAGE], capture=True)
        for index, users in enumerate(tiers):
            if index:
                say(f"cooldown {args.cooldown} s")
                time.sleep(args.cooldown)
            result = run_tier(users, args, out, contention_groups(users))
            results.append(result)
            if result["memory_guard"]["aborted"]:
                say("stopping: the memory guard fired")
                break
    finally:
        (out / "summary.json").write_text(json.dumps(results, indent=2))
        (out / "summary.md").write_text(markdown(results))
        say("cleanup: deleting the customers, putting web back")
        try:
            say(manage("--delete").strip())
        except subprocess.SubprocessError as exc:
            say(f"cleanup: delete failed ({exc}); run: make shell, manage.py loadtest_data --delete")
        run([*compose_base(), "up", "-d", "--no-deps", "--wait", "web"], check=False)
    say(f"results: {out.relative_to(ROOT)}")
    print(markdown(results))
    return 0 if all(r["slots"]["over"] == 0 for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
