---
name: milestone
description: Run one Smart IPTV milestone (M1–M15, M11b) end to end with the project's discipline. The steps are orient, plan, ADR, build in verified steps, prove each acceptance criterion, record progress and stop at a checkpoint. Use when the user says "start M3", "continue", "next milestone", "where are we", or asks for a checkpoint or acceptance report.
argument-hint: "<milestone, e.g. 1, 7 or 11b; empty = next>"
---

# Milestone $ARGUMENTS

## 1. Orient
- Read `docs/PROGRESS.md`; if it's missing, M1 hasn't started. Then run `git log --oneline -20` and `git tag -l 'm*-done'`.
- With no argument, or on "continue", take the milestone after the highest `m{N}-done` tag, unless PROGRESS.md shows one still in progress.
- Don't start a milestone while an earlier one has failing acceptance criteria. Say so instead.
- Read §1 and §2 of the spec, the §16 acceptance row (verbatim), and every section mapped below, in full.

| M | Spec sections to read |
|---|---|
| 1 | §2, §4, §5, §13, §15 gates, §16 |
| 2 | §5 (`core`, `audit`), §6 conventions and ops, §10 errors, §11 log redaction, §14 metrics |
| 3 | §6 accounts and billing, §7.4 entitlement object, §7.6, §11 passwords/MFA/sessions, §11.1 |
| 4 | §6 library, §7.1, §7.2 steps 1–3, §13 sample media |
| 5 | §1.3, §7.2 steps 4–9, §15 parser and cassette tests |
| 6 | §6 catalog and engagement, §7.8, §7.9, §10 catalog/search/engagement |
| 7 | §3 playback path, §7.4, §11 signed URLs, §12 |
| 8 | §6 Rendition/TranscodeJob/tracks, §7.3, §13 transcoder image |
| 9 | §7.5 (use the `xtream-contract` skill), §11 rate limits and redaction, §15 contract tests |
| 10 | §7.5, §9 Devices & TV apps, `docs/client-setup/` |
| 11 | §8 (all of it), §8.4, §10 Admin |
| 11b | §9, §7.6 providers, §10 Me/Playback/Billing |
| 12 | §6 live, §7.5 live/EPG rows |
| 13 | §14 |
| 14 | §11, §17 |
| 15 | §12 tiers and CDN, §15 load tests, §17 |

## 2. Plan (print it before writing code)
- Files to create or change, grouped by app or package.
- Decisions, each marked ADR or no ADR. Write an ADR for any new dependency or service, a data-model shape, a security mechanism, a public contract (REST, Xtream, token format), or a deviation from the spec.
- Every dependency you'll add, checked first with the `dep-check` skill.
- Re-check the stack pinned in ADR-0001 with `dep-check` and plan security or end-of-life upgrades; this replaces Renovate (ADR-0003).
- Risks, and for each acceptance criterion the exact command or test that will prove it.

## 3. Build
- Work in small steps. After each one, run the narrowest gate that covers the change, and run `make fmt lint typecheck test` before each commit. Fix failures before moving on. Run `make ci` before pushing.
- Migrations: one per logical change, named, reversible.
- Commit per coherent step, using Conventional Commits scoped to the app (`feat(playback): …`, `fix(xtream): …`).
- If the spec is wrong, or a library disagrees with it, stop and report it with a proposed alternative. Don't diverge silently.
- Ask before deleting data or volumes, breaking a public contract, adding a paid service or non-permissive dependency, or anything irreversible.

## 4. Prove acceptance
- Run `make ci`, the full quality gate (there's no hosted CI; see ADR-0003), and cite its result.
- Run every criterion for real: commands, URLs, tests. Show evidence such as output excerpts, test names and status codes, not assertions.
- If a criterion can't be verified on this machine, mark it NOT VERIFIED and give the reason. Never mark it as passing.

## 5. Record
- `docs/PROGRESS.md`: milestone status, what's done, what's next, known issues, verify commands.
- `docs/adr/NNNN-kebab-title.md`: Context, Decision, Alternatives, Consequences.
- Update CLAUDE.md if commands or architecture facts changed.
- Tag `m{N}-done` only when `make ci BUILD_FLAGS="--pull --no-cache"` and every criterion pass. Ask before pushing anything the user hasn't asked to push.

## 6. Checkpoint, then stop and wait for "continue"
```
## M{N} checkpoint: <name>
### Built
- <bullet per capability, with key files>
### Verify
<exact commands and URLs, copy-pasteable>
### Acceptance (§16)
| Criterion | Result | Evidence |
|---|---|---|
| … | PASS / FAIL / NOT VERIFIED | test name, command output, URL |
### Decisions
- ADR-NNNN <title>: <one line>
### Open questions
- <only things that need the user's call>
```

## Gate additions by milestone
Wherever the spec says "in CI", wire the check into `make ci` (ADR-0003 has the mapping):
- **M2:** fail `make ci` when the generated OpenAPI client (`make api-client`) is stale.
- **M9:** Xtream contract tests (schemas and golden fixtures in `compat/`) and the IPTVnator end-to-end run.
- **M14:** pip-audit, pnpm audit and Semgrep in `make ci`; a weekly scheduled `make restore-test` on staging.
