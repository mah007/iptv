# Progress

| Milestone | Status | Tag |
|---|---|---|
| M1 Infrastructure & repo | Done | `m1-done` |
| M2 Backend foundation | Next (built together with M3 as one slice, plan in `docs/plans/m2-m3.md`) | — |
| M3–M15 | Planned | — |

## M1: Infrastructure & repo (2026-10-03)

### Done
- **Monorepo** laid out per SPEC §5:
  - `backend/` (Django 5.2 LTS, 15 apps registered, each with a `migrations/` package);
  - `frontend/` (pnpm workspace: `packages/ui`, `apps/admin`, `apps/portal`);
  - `docker/`, `scripts/`, `docs/`.
- **Backend:**
  - settings split into base/dev/test/prod, with fail-loud environment parsing;
  - custom `accounts.User` with UUIDv7 keys from migration 0001;
  - PostgreSQL extensions (pg_trgm, unaccent, btree_gist);
  - host-based URLconfs (`api.`, `tv.`, internal);
  - public liveness and internal readiness (Postgres, redis-state, redis-cache, Meilisearch);
  - Celery with the SPEC §13 queues and a beat → worker heartbeat.
- **Frontend:**
  - `@smart-iptv/ui` design system: Tailwind v4 tokens from §8.1, Button, Card and Badge, AppShell, i18next with Arabic RTL and English, light and dark themes;
  - admin and portal shells (TanStack Router + Query);
  - ESLint rules that block hard-coded UI text and physical (non-RTL) Tailwind classes, with warnings failing the gate;
  - Arabic/English translation-key parity tests.
- **Stack:** `make up` boots Traefik, PostgreSQL 18, two Valkey instances (noeviction+AOF / LRU), Meilisearch, web, worker, beat and frontend, all with healthchecks. The data stores sit on an internal network with no egress.
- **Production images:**
  - app: non-root uid 10001, Gunicorn + Uvicorn workers, no pip;
  - frontend: unprivileged nginx with immutable assets and SPA fallback.
- **Quality gate `make ci`** (no hosted CI; owner's decision, ADR-0003). It runs:
  1. stack and smoke test;
  2. lint, including a missing-migrations check;
  3. types;
  4. tests;
  5. production images, started and health-checked with `check --deploy` using an env generated from `.env.example`;
  6. Trivy CVE and secret scans;
  7. the licence gate, with a self-test.

  It checks the committed tree and dumps service logs on failure.
- **Decisions:**
  - [ADR-0001](adr/0001-stack-and-versions.md): stack and versions;
  - [ADR-0002](adr/0002-foundations-identity-routing-networks.md): identity, routing, networks;
  - [ADR-0003](adr/0003-no-hosted-ci.md): no hosted CI, local gate.

### Verify
```bash
make secrets   # .env with generated secrets; picks port 8080 if 80 is busy
make ci        # the full gate; tagging used BUILD_FLAGS="--pull --no-cache"
```
Then open the admin and the portal: `http://admin.localhost:<port>` and `http://app.localhost:<port>`.

### Acceptance (SPEC §16)
| Criterion | Result | Evidence |
|---|---|---|
| Monorepo scaffold | PASS | Layout per §5; 15 backend apps registered |
| `make up` boots Traefik, Postgres, both Redis, Meilisearch, web (hello + health), worker, beat, frontend shells | PASS | All 9 services healthy. `make ci` also passed starting from a completely empty Docker (no images, volumes or cache) in 138 s |
| Quality gate green: lint, type, test, build, Trivy, licence gate (spec: "CI green"; hosted CI dropped by the owner, ADR-0003) | PASS | `make ci BUILD_FLAGS="--pull --no-cache"` passed for `70706d9` in 102 s: smoke 9/9, 44 backend tests (98.4% coverage), 10 frontend tests, both production images healthy with `check --deploy` clean, Trivy clean (CVEs and secrets), licence gate 27 + 25 packages |
| `.env.example` | PASS | Generated into `.env` (mode 600, git-ignored) by `make secrets`, and exercised by `make smoke-images` |
| ADR-0001 stack | PASS | Versions verified 2026-10-03, with deviations explained |

### Notes
- `docs/SPEC.md` is kept out of git (via `.git/info/exclude`) until the owner decides whether the spec may be public, because the repository is public.
- Dev runs plain HTTP on `*.localhost`; the production TLS entrypoint, ACME and HSTS arrive with the production overlay (M13–M15).
- Traefik reads a read-only Docker socket in dev; a socket proxy replaces it in M14.
- Access logs stay off until the redacting log pipeline exists (M2), because Xtream URLs carry credentials.

### Next: M2 + M3 as one vertical slice
Backend foundation (redacted logging, problem+json, OpenAPI with the generated client, settings registry, audit, metrics), then accounts, RBAC, admin MFA, plans and subscriptions, with the matching admin pages. The first admin demo follows that slice.
