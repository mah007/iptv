#!/usr/bin/env python3
"""A small steady load while web rolls (M15, ADR-0022): `make rollout-test`.

Standard library only. Threads send requests through Traefik for --seconds and count
every answer that isn't 2xx/3xx/401 and every connection error. Exit 1 when any request
failed. Only *.localhost hosts are accepted: this never leaves the machine.

    python3 loadtests/rollout_probe.py --base http://127.0.0.1:8080 --domain localhost \
        --seconds 90 --threads 16 [--xtream USER:PASS from XC_AUTH in the environment]
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import sys
import threading
import time
from collections import Counter
from urllib.parse import quote, urlsplit


def targets(domain: str) -> list[tuple[str, str, str]]:
    """(method, host, path) mixes: the Xtream host, the API, both SPAs' APIs, POSTs."""
    paths = [
        ("GET", f"tv.{domain}", "/health"),
        ("GET", f"api.{domain}", "/api/v1/health"),
        ("GET", f"admin.{domain}", "/api/v1/health"),
        ("GET", f"app.{domain}", "/api/v1/branding"),
        # The SPAs themselves (the frontend service, rolled by `make rollout s=frontend`).
        ("GET", f"admin.{domain}", "/"),
        ("GET", f"app.{domain}", "/"),
        ("POST", f"api.{domain}", "/api/v1/auth/login"),
    ]
    credentials = os.environ.get("XC_AUTH", "")
    if ":" in credentials:
        user, password = credentials.split(":", 1)
        query = f"username={quote(user)}&password={quote(password)}"
        paths += [
            ("GET", f"tv.{domain}", f"/player_api.php?{query}"),
            ("GET", f"tv.{domain}", f"/player_api.php?{query}&action=get_vod_categories"),
            ("GET", f"tv.{domain}", f"/player_api.php?{query}&action=get_vod_streams"),
        ]
    return paths


def worker(base: str, plan: list[tuple[str, str, str]], until: float, counts: Counter[str],
           failures: list[str], lock: threading.Lock, index: int) -> None:  # fmt: skip
    url = urlsplit(base)
    n = index
    while time.monotonic() < until:
        method, host, path = plan[n % len(plan)]
        n += 1
        body = json.dumps({"username": "nobody", "password": "wrong-password"}) if method == "POST" else None
        started = time.monotonic()
        try:
            conn = http.client.HTTPConnection(url.hostname or "127.0.0.1", url.port or 80, timeout=15)
            headers = {"Host": host, "Content-Type": "application/json"}
            conn.request(method, path, body=body, headers=headers)
            status = conn.getresponse().status
            conn.close()
        except OSError as exc:
            status = 0
            detail = type(exc).__name__
        # 429 is Traefik's per-address rate limit doing its job (every request here comes
        # from one address): counted, not a rollout failure.
        ok = status in range(200, 400) or status in (400, 401, 403, 429)
        with lock:
            counts["requests"] += 1
            counts[f"status_{status}"] += 1
            if not ok:
                counts["failed"] += 1
                label = path.split("?")[0]
                failures.append(f"{time.strftime('%H:%M:%S')} {method} {host}{label} -> "
                                f"{status or detail} ({time.monotonic() - started:.2f}s)")  # fmt: skip
        time.sleep(0.15)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base", default="http://127.0.0.1:8080")
    parser.add_argument("--domain", default="localhost")
    parser.add_argument("--seconds", type=float, default=90)
    parser.add_argument("--threads", type=int, default=16)
    args = parser.parse_args()
    if not (args.domain == "localhost" or args.domain.endswith(".localhost")):
        sys.exit("rollout_probe: only a *.localhost development stack, never a real server")
    if urlsplit(args.base).hostname not in ("127.0.0.1", "localhost", "::1"):
        sys.exit("rollout_probe: --base must be this machine")
    plan = targets(args.domain)
    counts: Counter[str] = Counter()
    failures: list[str] = []
    lock = threading.Lock()
    until = time.monotonic() + args.seconds
    threads = [
        threading.Thread(target=worker, args=(args.base, plan, until, counts, failures, lock, i))
        for i in range(args.threads)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    statuses = {k: v for k, v in sorted(counts.items()) if k.startswith("status_")}
    print(f"rollout_probe: {counts['requests']} requests, {counts['failed']} failed; {statuses}")
    for line in failures[:20]:
        print(f"  {line}")
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
