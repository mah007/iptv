#!/usr/bin/env python3
"""Licence gate (SPEC §1.2): production dependencies must be permissively licensed.

Allowed: MIT, BSD, Apache-2.0, ISC, Zlib, MPL-2.0, PSF and similar, plus LGPL used
unmodified. Anything else (GPL, AGPL, SSPL, BUSL, non-commercial, unknown) fails.

Usage:
    python license_gate.py python
        Inspect the interpreter's installed distributions. Run it with the
        production virtualenv's python.
    pnpm -r licenses list --prod --json | python license_gate.py npm
        Inspect pnpm's report for the frontend production dependencies.

Stdlib only. Exit status 1 lists every offending package.
"""

from __future__ import annotations

import json
import re
import sys
from importlib import metadata

ALLOWED = re.compile(
    r"\b(MIT|MIT-0|BSD|0BSD|APACHE|ISC|ZLIB|MPL|MOZILLA PUBLIC|PSF|PYTHON SOFTWARE|"
    r"PYTHON-2\.0|UNLICENSE|CC0|BLUEOAK|HPND|POSTGRESQL)\b"
)
LGPL = re.compile(r"\bLGPL|LESSER GENERAL PUBLIC|LIBRARY GENERAL PUBLIC")
FORBIDDEN = re.compile(
    r"\bAGPL|\bSSPL|\bBUSL|\bBSL-1\.1|\bNC\b|NON-?COMMERCIAL|COMMONS CLAUSE|"
    r"(?<![LA])\bGPL|GENERAL PUBLIC LICENSE"
)

# Packages whose metadata carries no usable licence string, verified by hand.
# Format: name -> (licence, reason). Keep this list short and justified.
VERIFIED: dict[str, tuple[str, str]] = {}


def verdict(licence: str) -> str:
    """Return 'ok', 'lgpl' or 'fail' for a licence string or SPDX expression."""
    if not licence.strip():
        return "fail"
    # "A OR B": we may choose the most permissive option.
    options = re.split(r"\s+OR\s+", licence.strip("() "), flags=re.IGNORECASE)
    if len(options) > 1:
        verdicts = {verdict(option) for option in options}
        return "ok" if "ok" in verdicts else "lgpl" if "lgpl" in verdicts else "fail"
    upper = licence.upper()
    if LGPL.search(upper):
        return "lgpl"
    if FORBIDDEN.search(upper):
        return "fail"
    return "ok" if ALLOWED.search(upper) else "fail"


def python_licences() -> dict[str, str]:
    result: dict[str, str] = {}
    for dist in metadata.distributions():
        meta = dist.metadata
        name = meta["Name"]
        classifiers = [
            c.split("::")[-1].strip()
            for c in meta.get_all("Classifier") or []
            if c.startswith("License ::")
        ]
        raw = (meta.get("License") or "").strip()
        short_raw = raw if raw and len(raw) <= 100 and "\n" not in raw else ""
        result[name] = meta.get("License-Expression") or " / ".join(classifiers) or short_raw
    return result


def npm_licences(report: dict[str, list[dict[str, object]]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for licence, packages in report.items():
        for package in packages:
            name = str(package["name"])
            if name.startswith("@smart-iptv/"):
                continue  # our own workspace packages
            result[name] = licence
    return result


def check(licences: dict[str, str]) -> int:
    if not licences:
        print("No packages found: the gate was given nothing to check.")
        return 1
    failures, lgpl = [], []
    for name, licence in sorted(licences.items(), key=lambda item: item[0].lower()):
        if name in VERIFIED:
            continue
        result = verdict(licence)
        if result == "fail":
            failures.append(f"  {name}: {licence or 'no licence metadata'}")
        elif result == "lgpl":
            lgpl.append(f"  {name}: {licence}")
    print(f"Checked {len(licences)} packages.")
    if lgpl:
        print("LGPL (allowed only unmodified; never vendor or patch):")
        print("\n".join(lgpl))
    if failures:
        print("NOT ALLOWED (SPEC §1.2):")
        print("\n".join(failures))
        return 1
    print("Licence gate passed.")
    return 0


def main(argv: list[str]) -> int:
    if argv == ["python"]:
        return check(python_licences())
    if argv == ["npm"]:
        return check(npm_licences(json.load(sys.stdin)))
    sys.stderr.write(__doc__ or "")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
