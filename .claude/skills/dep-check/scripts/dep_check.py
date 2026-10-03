#!/usr/bin/env python3
"""Report the latest stable version and licence of dependencies, with a licence-gate verdict.

Usage:
    dep_check.py pypi:django npm:@tanstack/react-query gh:traefik/traefik eol:python ...

Sources:
    pypi:<name>       PyPI JSON API (version, upload date, licence, requires_python)
    npm:<name>        npm registry `latest` dist-tag (version, licence, engines.node)
    gh:<owner>/<repo> GitHub latest release + repo licence (set GITHUB_TOKEN or log in with gh)
    eol:<product>     endoflife.date support cycles (runtimes and servers: python, nodejs,
                      django, postgresql, redis, valkey, nginx, traefik, ...)

Gate verdicts follow SPEC §1.2:
    OK      MIT/BSD/Apache-2.0/ISC/Zlib/MPL-2.0 and similar permissive licences
    LGPL    allowed only if used unmodified
    BLOCK   GPL/AGPL/SSPL/BUSL/non-commercial: never import; run as a separate process at most
    CHECK   licence unknown or unrecognised: read it before pinning

Exit status is 1 when any package is BLOCK, so the script can back a quality gate.
Stdlib only; needs Python 3.11+ (datetime.UTC).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime

TIMEOUT_S = 20
UA = "smart-iptv-dep-check/1.0"

PERMISSIVE = re.compile(
    r"\b(MIT|BSD|APACHE|ISC|ZLIB|0BSD|PSF|PYTHON|UNLICENSE|CC0|BLUEOAK|HPND|MPL|MOZILLA)\b"
)
BLOCKED = re.compile(
    r"(\bAGPL|\bSSPL|\bBUSL|\bBSL-1\.1|\bNC\b|NON-?COMMERCIAL|COMMONS CLAUSE|ELASTIC)"
)
GPL = re.compile(r"(?<![LA])\bGPL|GENERAL PUBLIC LICENSE")
LGPL = re.compile(r"\bLGPL|LESSER GENERAL PUBLIC|LIBRARY GENERAL PUBLIC")


@dataclass
class Row:
    spec: str
    version: str = "?"
    released: str = ""
    licence: str = "?"
    gate: str = "CHECK"
    notes: str = ""


def gate_for(licence: str) -> str:
    if not licence or licence == "?":
        return "CHECK"
    # SPDX "A OR B": we may pick the most permissive option.
    parts = re.split(r"\s+OR\s+", licence, flags=re.IGNORECASE)
    if len(parts) > 1:
        verdicts = [gate_for(p.strip("() ")) for p in parts]
        for best in ("OK", "LGPL", "CHECK", "BLOCK"):
            if best in verdicts:
                return best
    up = licence.upper()
    if BLOCKED.search(up):
        return "BLOCK"
    if LGPL.search(up):
        return "LGPL"
    if GPL.search(up):
        return "BLOCK"
    if PERMISSIVE.search(up):
        return "OK"
    return "CHECK"


def _github_token() -> str | None:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token
    try:
        out = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() or None


GH_TOKEN = _github_token()


def get_json(url: str, *, github: bool = False) -> dict | list:
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if github:
        headers["Accept"] = "application/vnd.github+json"
        if GH_TOKEN:
            headers["Authorization"] = f"Bearer {GH_TOKEN}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return json.load(resp)


def pypi(name: str, row: Row) -> None:
    data = get_json(f"https://pypi.org/pypi/{urllib.parse.quote(name)}/json")
    info = data["info"]
    row.version = info["version"]
    if data.get("urls"):
        row.released = data["urls"][0]["upload_time_iso_8601"][:10]
    classifiers = [
        c.split("::")[-1].strip()
        for c in info.get("classifiers", [])
        if c.startswith("License ::")
    ]
    raw = (info.get("license") or "").strip()
    short_raw = raw if raw and len(raw) <= 80 and "\n" not in raw else ""
    row.licence = (
        info.get("license_expression") or " / ".join(classifiers) or short_raw or "?"
    )
    if info.get("requires_python"):
        row.notes = f"python {info['requires_python']}"


def npm(name: str, row: Row) -> None:
    data = get_json(
        f"https://registry.npmjs.org/{urllib.parse.quote(name, safe='@')}/latest"
    )
    row.version = data["version"]
    lic = data.get("license")
    if isinstance(lic, dict):
        lic = lic.get("type")
    row.licence = lic or "?"
    node = (data.get("engines") or {}).get("node")
    if node:
        row.notes = f"node {node}"


def github(repo: str, row: Row) -> None:
    rel = get_json(f"https://api.github.com/repos/{repo}/releases/latest", github=True)
    row.version = rel["tag_name"]
    row.released = (rel.get("published_at") or "")[:10]
    meta = get_json(f"https://api.github.com/repos/{repo}", github=True)
    row.licence = ((meta.get("license") or {}).get("spdx_id")) or "?"
    if row.licence == "NOASSERTION":
        row.licence = "?"


def eol(product: str, row: Row) -> None:
    cycles = get_json(f"https://endoflife.date/api/{urllib.parse.quote(product)}.json")
    today = datetime.now(tz=UTC).date().isoformat()
    supported = []
    for c in cycles:
        eol_val = c.get("eol")
        if eol_val is True or (isinstance(eol_val, str) and eol_val < today):
            continue
        flag = " LTS" if c.get("lts") else ""
        until = eol_val if isinstance(eol_val, str) else "n/a"
        supported.append(f"{c['cycle']}{flag} → {c.get('latest', '?')} (EOL {until})")
    if cycles:
        row.version = str(cycles[0].get("latest", "?"))
        row.released = str(cycles[0].get("latestReleaseDate", ""))
    row.licence = "n/a"
    row.gate = "-"
    row.notes = "; ".join(supported[:4]) or "no supported cycle"


HANDLERS = {"pypi": pypi, "npm": npm, "gh": github, "eol": eol}


def check(spec: str) -> Row:
    row = Row(spec)
    kind, _, name = spec.partition(":")
    handler = HANDLERS.get(kind)
    if not handler or not name:
        row.notes = "bad spec; use pypi:/npm:/gh:/eol:"
        return row
    try:
        handler(name, row)
    except urllib.error.HTTPError as exc:
        hint = (
            " (GitHub rate limit? set GITHUB_TOKEN)"
            if kind == "gh" and exc.code == 403
            else ""
        )
        row.notes = f"HTTP {exc.code}{hint}"
        return row
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as exc:
        row.notes = f"lookup failed: {exc}"
        return row
    if row.gate != "-":
        row.gate = gate_for(row.licence)
    return row


def main(argv: list[str]) -> int:
    if not argv or argv[0] in {"-h", "--help"}:
        print(__doc__)
        return 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(check, argv))
    print("| Package | Latest | Released | Licence | Gate | Notes |")
    print("|---|---|---|---|---|---|")
    for r in rows:
        lic = r.licence.replace("|", "/")
        print(
            f"| `{r.spec}` | {r.version} | {r.released} | {lic} | {r.gate} | {r.notes} |"
        )
    blocked = [r.spec for r in rows if r.gate == "BLOCK"]
    unknown = [r.spec for r in rows if r.gate == "CHECK"]
    if blocked:
        print(f"\nBLOCK (never import; separate process at most): {', '.join(blocked)}")
    if unknown:
        print(f"CHECK (read the licence before pinning): {', '.join(unknown)}")
    return 1 if blocked else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
