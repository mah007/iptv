#!/usr/bin/env python3
"""The dependency vulnerability gate (ADR-0021): reads pip-audit's or pnpm audit's JSON on
stdin and fails on any known vulnerability in a production dependency, except those listed
in scripts/audit-ignore.toml with a reason and an expiry date. An expired exception fails
too, so every one is looked at again.

    ... pip-audit -f json | python3 scripts/audit_deps.py pip
    ... pnpm audit --prod --json | python3 scripts/audit_deps.py npm
    python3 scripts/audit_deps.py --self-test

Standard library only: it runs on the host.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tomllib
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

IGNORE_FILE = Path(__file__).with_name("audit-ignore.toml")


@dataclass(frozen=True)
class Finding:
    package: str
    version: str
    ids: frozenset[str]
    summary: str
    fixed_in: str


@dataclass(frozen=True)
class Exception_:
    id: str
    package: str
    reason: str
    expires: date


def load_exceptions(text: str) -> list[Exception_]:
    data = tomllib.loads(text)
    found = []
    for entry in data.get("ignore", []):
        missing = {"id", "package", "reason", "expires"} - entry.keys()
        if missing:
            raise ValueError(f"audit-ignore entry {entry!r} lacks {sorted(missing)}")
        if len(str(entry["reason"]).strip()) < 20:
            raise ValueError(f"audit-ignore {entry['id']}: give a real reason")
        expires = entry["expires"]
        if not isinstance(expires, date):
            expires = date.fromisoformat(str(expires))
        found.append(Exception_(entry["id"], entry["package"], entry["reason"], expires))
    return found


def pip_findings(report: dict) -> list[Finding]:
    if "dependencies" not in report:
        raise ValueError("not pip-audit JSON (no 'dependencies')")
    found = []
    for dep in report["dependencies"]:
        if "skip_reason" in dep:
            raise ValueError(f"pip-audit skipped {dep['name']}: {dep['skip_reason']}")
        for vuln in dep.get("vulns", []):
            ids = frozenset([vuln["id"], *vuln.get("aliases", [])])
            found.append(
                Finding(
                    dep["name"].lower(),
                    dep["version"],
                    ids,
                    (vuln.get("description") or "").strip().split("\n")[0][:120],
                    ", ".join(vuln.get("fix_versions", [])) or "no fix yet",
                )
            )
    return found


def npm_findings(report: dict) -> list[Finding]:
    if "advisories" not in report or "metadata" not in report:
        raise ValueError("not pnpm audit JSON (no 'advisories')")
    found = []
    for advisory in report["advisories"].values():
        ids = {str(advisory.get("github_advisory_id") or advisory.get("id"))}
        ids.update(advisory.get("cves", []))
        versions = sorted({f["version"] for f in advisory.get("findings", [])}) or ["?"]
        found.append(
            Finding(
                advisory["module_name"].lower(),
                ", ".join(versions),
                frozenset(ids),
                str(advisory.get("title", ""))[:120],
                str(advisory.get("patched_versions") or "no fix yet"),
            )
        )
    return found


def judge(
    findings: list[Finding], exceptions: list[Exception_], today: date
) -> tuple[list[Finding], list[str]]:
    """The findings no exception covers, and the problems with the exceptions themselves."""
    problems = [
        f"exception {item.id} ({item.package}) expired on {item.expires}: look at it again"
        for item in exceptions
        if item.expires < today
    ]
    live = [item for item in exceptions if item.expires >= today]
    open_ = [
        finding
        for finding in findings
        if not any(e.package.lower() == finding.package and e.id in finding.ids for e in live)
    ]
    return open_, problems


def run(kind: str, raw: str, exceptions: list[Exception_], today: date) -> int:
    try:
        report = json.loads(raw)
    except json.JSONDecodeError:
        print(f"audit: no {kind} report (the audit itself failed?)", file=sys.stderr)
        return 2
    findings = pip_findings(report) if kind == "pip" else npm_findings(report)
    open_, problems = judge(findings, exceptions, today)
    label = "Python (pip-audit)" if kind == "pip" else "frontend (pnpm audit --prod)"
    for problem in problems:
        print(f"  FAIL  {problem}")
    for finding in open_:
        print(
            f"  FAIL  {finding.package} {finding.version}: {', '.join(sorted(finding.ids))}"
            f" (fixed in {finding.fixed_in}) {finding.summary}"
        )
    accepted = len(findings) - len(open_)
    if open_ or problems:
        print(f"{label}: {len(open_)} known vulnerabilities, {accepted} accepted until expiry.")
        print("Upgrade, or add a dated exception with a reason to scripts/audit-ignore.toml.")
        return 1
    print(f"{label}: no known vulnerabilities" + (f" ({accepted} accepted)." if accepted else "."))
    return 0


def self_test() -> None:
    today = date(2026, 10, 14)
    pip = {
        "dependencies": [
            {"name": "Django", "version": "5.2.1", "vulns": [
                {"id": "PYSEC-1", "aliases": ["CVE-2026-1"], "fix_versions": ["5.2.2"],
                 "description": "SQL injection"}]},
            {"name": "idna", "version": "3.7", "vulns": []},
        ]
    }
    npm = {
        "advisories": {"1": {"module_name": "lodash", "github_advisory_id": "GHSA-a",
                             "cves": [], "title": "Prototype pollution",
                             "patched_versions": ">=4.17.21",
                             "findings": [{"version": "4.17.20"}]}},
        "metadata": {},
    }
    covered = [Exception_("CVE-2026-1", "django", "Not reachable: we never call it.", today)]
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        _self_test_runs(pip, npm, covered, today)
    try:
        load_exceptions(
            '[[ignore]]\nid = "X"\npackage = "y"\nreason = "short"\nexpires = 2026-12-31\n'
        )
    except ValueError:
        pass
    else:
        raise AssertionError("a short reason must be refused")
    load_exceptions(IGNORE_FILE.read_text(encoding="utf-8"))
    print("audit_deps self-test: ok")


def _self_test_runs(pip: dict, npm: dict, covered: list[Exception_], today: date) -> None:
    assert run("pip", json.dumps(pip), [], today) == 1
    assert run("pip", json.dumps(pip), covered, today) == 0
    expired = [Exception_("CVE-2026-1", "django", "Not reachable: we never call it.",
                          date(2026, 10, 13))]
    assert run("pip", json.dumps(pip), expired, today) == 1
    assert run("npm", json.dumps(npm), [], today) == 1
    assert run("npm", json.dumps({"advisories": {}, "metadata": {}}), [], today) == 0
    assert run("pip", "", [], today) == 2


def main() -> int:
    if sys.argv[1:] == ["--self-test"]:
        self_test()
        return 0
    if len(sys.argv) != 2 or sys.argv[1] not in {"pip", "npm"}:
        print(__doc__, file=sys.stderr)
        return 2
    exceptions = load_exceptions(IGNORE_FILE.read_text(encoding="utf-8"))
    return run(sys.argv[1], sys.stdin.read(), exceptions, datetime.now(UTC).date())


if __name__ == "__main__":
    sys.exit(main())
