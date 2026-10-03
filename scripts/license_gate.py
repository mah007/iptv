#!/usr/bin/env python3
"""Licence gate (SPEC §1.2): production dependencies must be permissively licensed.

Allowed: MIT, BSD, Apache-2.0, ISC, Zlib, MPL-2.0, PSF and similar, plus LGPL used
unmodified. OFL-1.1 is allowed too: it is the licence of the self-hosted fonts (Inter,
IBM Plex Sans Arabic), and it permits bundling them in commercial software as long as
the fonts aren't sold on their own. Anything else (GPL, AGPL, SSPL, BUSL,
non-commercial, unknown) fails.

Usage:
    python license_gate.py python
        Inspect the interpreter's installed distributions. Run it with the
        production virtualenv's python.
    pnpm -r licenses list --prod --json | python license_gate.py npm
        Inspect pnpm's report for the frontend production dependencies.
    python license_gate.py --self-test
        Check the classifier itself against known expressions.

Licence expressions are evaluated SPDX-style: `A OR B` takes the most permissive
option, `A AND B` the most restrictive, `X WITH exception` stays X. Text that
doesn't parse as an expression is judged conservatively: any forbidden licence
anywhere in it fails. Several licence classifiers on one Python package count
as AND.

Stdlib only. Exit status 1 lists every offending package.
"""

from __future__ import annotations

import json
import re
import sys
from importlib import metadata

ALLOWED = re.compile(
    r"\b(MIT|MIT-0|BSD|0BSD|APACHE|ISC|ZLIB|MPL|MOZILLA PUBLIC|PSF|PYTHON SOFTWARE|"
    r"PYTHON-2\.0|UNLICENSE|CC0|BLUEOAK|HPND|POSTGRESQL|"
    # Font licence (SIL Open Font License 1.1), e.g. @fontsource packages.
    r"OFL-1\.1|SIL OPEN FONT LICENSE(,? VERSION)? 1\.1)\b"
)
LGPL = re.compile(r"\bLGPL|LESSER GENERAL PUBLIC|LIBRARY GENERAL PUBLIC")
FORBIDDEN = re.compile(
    r"\bAGPL|\bSSPL|\bBUSL|\bBSL-1\.1|\bNC\b|NON-?COMMERCIAL|COMMONS CLAUSE|PROPRIETARY|"
    r"(?<![LA])\bGPL|(?<!LESSER )(?<!LIBRARY )GENERAL PUBLIC LICENSE"
)
RANK = {"ok": 0, "lgpl": 1, "fail": 2}
OPERATORS = ("AND", "OR", "WITH")

# Packages whose metadata carries no usable licence string, verified by hand.
# Format: name -> (licence, reason). Keep this list short and justified.
VERIFIED: dict[str, tuple[str, str]] = {}


class _ParseError(ValueError):
    pass


def _classify(name: str) -> str:
    upper = name.upper()
    if FORBIDDEN.search(upper):
        return "fail"
    if LGPL.search(upper):
        return "lgpl"
    return "ok" if ALLOWED.search(upper) else "fail"


def _tokens(expression: str) -> list[str]:
    parts = re.split(r"(\(|\)|\bAND\b|\bOR\b|\bWITH\b)", expression)
    return [part.strip() for part in parts if part and part.strip()]


def _evaluate(tokens: list[str]) -> str:
    """Recursive descent: or_expr := and_expr (OR and_expr)*; and_expr := factor (AND factor)*."""
    pos = 0

    def peek() -> str | None:
        return tokens[pos] if pos < len(tokens) else None

    def take() -> str:
        nonlocal pos
        if pos >= len(tokens):
            raise _ParseError
        pos += 1
        return tokens[pos - 1]

    def or_expr() -> str:
        result = and_expr()
        while peek() == "OR":
            take()
            result = min(result, and_expr(), key=RANK.__getitem__)
        return result

    def and_expr() -> str:
        result = factor()
        while peek() == "AND":
            take()
            result = max(result, factor(), key=RANK.__getitem__)
        return result

    def factor() -> str:
        token = take()
        if token == "(":
            result = or_expr()
            if take() != ")":
                raise _ParseError
            return result
        if token in (*OPERATORS, ")"):
            raise _ParseError
        result = _classify(token)
        if peek() == "WITH":
            take()
            exception = take()
            if exception in (*OPERATORS, "(", ")"):
                raise _ParseError
        return result

    result = or_expr()
    if pos != len(tokens):
        raise _ParseError
    return result


def verdict(licence: str) -> str:
    """Return 'ok', 'lgpl' or 'fail' for a licence string or SPDX expression."""
    if not licence.strip():
        return "fail"
    try:
        return _evaluate(_tokens(licence))
    except _ParseError:
        return _classify(licence)


def python_licences() -> list[tuple[str, str]]:
    result = []
    for dist in metadata.distributions():
        meta = dist.metadata
        classifiers = [
            c.split("::")[-1].strip()
            for c in meta.get_all("Classifier") or []
            if c.startswith("License ::")
        ]
        raw = (meta.get("License") or "").strip()
        short_raw = raw if raw and len(raw) <= 100 and "\n" not in raw else ""
        licence = meta.get("License-Expression") or " AND ".join(classifiers) or short_raw
        result.append((meta["Name"], licence))
    return result


def npm_licences(report: dict[str, list[dict[str, object]]]) -> list[tuple[str, str]]:
    """One entry per (package, licence): versions under different licences all get checked."""
    result = []
    for licence, packages in report.items():
        for package in packages:
            name = str(package["name"])
            if name.startswith("@smart-iptv/"):
                continue  # our own workspace packages
            result.append((name, licence))
    return result


def check(entries: list[tuple[str, str]]) -> int:
    if not entries:
        print("No packages found: the gate was given nothing to check.")
        return 1
    failures, lgpl = [], []
    for name, licence in sorted(entries, key=lambda entry: entry[0].lower()):
        if name in VERIFIED:
            continue
        result = verdict(licence)
        if result == "fail":
            failures.append(f"  {name}: {licence or 'no licence metadata'}")
        elif result == "lgpl":
            lgpl.append(f"  {name}: {licence}")
    print(f"Checked {len(entries)} packages.")
    if lgpl:
        print("LGPL (allowed only unmodified; never vendor or patch):")
        print("\n".join(lgpl))
    if failures:
        print("NOT ALLOWED (SPEC §1.2):")
        print("\n".join(failures))
        return 1
    print("Licence gate passed.")
    return 0


SELF_TEST_CASES = {
    "MIT": "ok",
    "(MIT OR Apache-2.0)": "ok",
    "MIT OR GPL-3.0-only": "ok",
    "Apache-2.0 AND BSD-2-Clause": "ok",
    "MPL-2.0 AND (Apache-2.0 OR MIT)": "ok",
    "LGPL-3.0-only": "lgpl",
    "GNU Lesser General Public License v3 (LGPLv3)": "lgpl",
    "LGPL-2.1-only AND GPL-3.0-only": "fail",
    "MIT AND (LGPL-2.1-only OR GPL-3.0-only)": "lgpl",
    "(LGPL-2.1-only OR GPL-3.0-only) AND AGPL-3.0-only": "fail",
    "GPL-2.0-only WITH Classpath-exception-2.0": "fail",
    "GPL-3.0-only": "fail",
    "AGPL-3.0-or-later": "fail",
    "SSPL-1.0": "fail",
    "BSD License AND Other/Proprietary License": "fail",
    "CC-BY-NC-4.0": "fail",
    "Mozilla Public License 2.0 (MPL 2.0)": "ok",
    "OFL-1.1": "ok",
    "SIL Open Font License 1.1": "ok",
    "(MIT AND OFL-1.1)": "ok",
    "OFL-1.1 AND GPL-3.0-only": "fail",
    "UNKNOWN": "fail",
    "": "fail",
}


def self_test() -> int:
    wrong = {
        expr: (verdict(expr), want)
        for expr, want in SELF_TEST_CASES.items()
        if verdict(expr) != want
    }
    for expr, (got, want) in wrong.items():
        print(f"self-test: {expr!r} -> {got}, expected {want}")
    if wrong:
        return 1
    print(f"Licence gate self-test passed ({len(SELF_TEST_CASES)} cases).")
    return 0


def main(argv: list[str]) -> int:
    if argv == ["--self-test"]:
        return self_test()
    if argv == ["python"]:
        return check(python_licences())
    if argv == ["npm"]:
        return check(npm_licences(json.load(sys.stdin)))
    sys.stderr.write(__doc__ or "")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
