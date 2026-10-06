#!/usr/bin/env python3
"""Wait for the FireDrill alert's email in Mailpit (make monitoring-firedrill, ADR-0018).

`manage.py fire_drill` raises the iptv_fire_drill gauge; Prometheus scrapes it from
web's /metrics, the FireDrill rule fires, and Alertmanager emails it through Mailpit
in dev. This script waits for that email (and, with --resolved, the "resolved" one
after `fire_drill --stop`), then prints when it arrived. Exit status 1 on timeout.

Usage: python3 monitoring/tests/fire_drill.py --mailpit http://mail.localhost:8080 \
           --since <unix time> [--resolved] [--timeout 300]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime


def messages(mailpit: str, query: str) -> list[dict[str, object]]:
    url = f"{mailpit}/api/v1/search?{urllib.parse.urlencode({'query': query, 'limit': 50})}"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310 - local Mailpit
            return list(json.load(response).get("messages") or [])
    except (urllib.error.URLError, ConnectionError, TimeoutError, ValueError):
        return []


def created(message: dict[str, object]) -> float:
    value = str(message.get("Created", ""))
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--mailpit", required=True, help="Mailpit's base URL")
    parser.add_argument("--since", type=float, required=True, help="unix time the drill began")
    parser.add_argument("--resolved", action="store_true", help="wait for the resolved email")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    status = "RESOLVED" if args.resolved else "FIRING"
    query = f'subject:"FireDrill" subject:"{status}"'
    deadline = time.monotonic() + args.timeout
    print(f"waiting up to {args.timeout} s for the {status} FireDrill email in {args.mailpit} ...")
    while time.monotonic() < deadline:
        found = [m for m in messages(args.mailpit, query) if created(m) >= args.since - 1]
        if found:
            first = min(found, key=created)
            to = ", ".join(str(item.get("Address")) for item in first.get("To") or [])  # type: ignore[union-attr]
            print(
                f"PASS: {first.get('Subject')!r} to {to} arrived "
                f"{created(first) - args.since:.0f} s after the drill began"
            )
            return 0
        time.sleep(5)
    print(f"FAIL: no {status} FireDrill email within {args.timeout} s", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
