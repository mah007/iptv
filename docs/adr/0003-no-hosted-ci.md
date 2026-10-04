# ADR-0003: No hosted CI; `make ci` is the quality gate

- **Status:** Accepted
- **Date:** 2026-10-03
- **Milestone:** M1

## Context
The spec assumes hosted CI. SPEC §4 lists GitHub Actions, §16 makes "CI green" an M1 acceptance criterion, and several later requirements say "enforced in CI". M1 shipped a GitHub Actions workflow running the same `make` targets developers use. Its first run never started, because the GitHub account is locked for billing, and the owner decided hosted CI isn't worth it for this project.

The checks themselves still matter. Only where they run changes.

## Decision
- **No hosted CI:** no GitHub Actions or similar service. `.github/workflows/ci.yml` is removed.
- **`make ci` is the quality gate.** It runs, stopping at the first failure (recipes use `set -eu -o pipefail`):
  1. `up`: build and start the stack, waiting up to 5 minutes for every healthcheck;
  2. `smoke`: routing, isolation and readiness through Traefik;
  3. `lint`: Ruff, a missing-migrations check, and ESLint with warnings as errors plus Prettier;
  4. `compat`: the Xtream contract's schemas and golden fixtures, offline;
  5. `typecheck`;
  6. `api-client-check`: the OpenAPI schemas and both generated clients are current;
  7. `test`: pytest with 85% coverage and Vitest, including Arabic/English key parity;
  8. `media-ready`: sample libraries scanned, matched and transcoded;
  9. `compat-live`: the Xtream contract against the running tv host, play URLs included;
  10. `e2e-iptvnator`, `e2e-admin`, `e2e-portal`: browser journeys in Playwright with the host's Chrome (IPTVnator; the admin SPA; the portal), the last two with axe;
  11. `build`: production images, with `--pull`;
  12. `smoke-images`: start the production images with an env generated from `.env.example`, run `check --deploy`, wait for their healthchecks;
  13. `scan`: Trivy, pinned by digest, for fixable HIGH/CRITICAL CVEs and secrets in the images, plus a secret scan of the repository;
  14. `licenses`: the licence gate, after its self-test.
- **It checks the committed tree.** It refuses uncommitted changes unless `ALLOW_DIRTY=1`, refuses a test subset (`t=`), names the commit it passed for, and prints service status and logs when it fails.
- **Run it before pushing to `main`.** Before tagging `m{N}-done`, run `make ci BUILD_FLAGS="--pull --no-cache"`. Milestone acceptance cites its result.
- Where the spec says "CI", read `make ci`:

| Spec requirement | Where it runs |
|---|---|
| Lint, types, tests, build (§15, §16) | `make ci`, today |
| Trivy image scan (§11, §15) | `make scan` inside `make ci`, today |
| Licence gate (§1.2) | `make licenses` inside `make ci`, today |
| OpenAPI client freshness (§8.4) | Added to `make ci` in M2 |
| Xtream contract tests and the IPTVnator end-to-end run (§7.5) | Added to `make ci` in M9 |
| Admin and portal Playwright journeys with axe (§8, §9, §15) | Added to `make ci` in M11 and M11b |
| pip-audit, pnpm audit, Semgrep (§11, §15) | Added to `make ci` in M14 (Bandit rules already run via Ruff's `S` rules) |
| Weekly restore test (§11) | M14: a weekly scheduled job (cron or systemd timer) on the staging host runs `make restore-test` |
| Release workflow (§5 `release.yml`) | Not created; releases run by hand through `make deploy` (§13, planned for M13–M15) |
| Renovate (§11) | Deferred: without automated tests on its PRs it adds noise. Dependencies are re-checked with `dep-check` at each milestone instead |

## Alternatives considered
- **Keep GitHub Actions:** rejected by the owner, as not worth it (and blocked by the account's billing lock).
- **Another hosted or self-hosted CI** (GitLab CI, Woodpecker, a self-hosted runner): the same cost/benefit question, plus a service to run. Revisit if the team grows.
- **A git pre-push hook that runs `make ci`:** rejected. The gate takes minutes and needs the stack, so every push would stall; developers run it deliberately instead.

## Consequences
- Nothing enforces the gate automatically; the milestone checkpoint requires its output as evidence.
- **Mostly kept: proof on a clean machine.** `smoke-images` exercises the env template and secrets script, and the tagging run builds without cache. What's left is the dev stack's own volumes and `.env`; before a release, also run the gate on a fresh machine or VM. (A second checkout on the same machine shares the Compose project's volumes, so generating new secrets there breaks database authentication.)
- **Gained:** no CI minutes or billing exposure, no third-party actions in the supply chain, and one command that's identical for everyone.
