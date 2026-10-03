# Progress

| Milestone | Status | Tag |
|---|---|---|
| M1 Infrastructure & repo | Built and verified locally; GitHub CI blocked by an account billing lock | — |
| M2 Backend foundation | Next | — |
| M3–M15 | Planned | — |

## M1: Infrastructure & repo (2026-10-03)

### Done
- **Monorepo** laid out per SPEC §5:
  - `backend/` (Django 5.2 LTS, 15 apps registered);
  - `frontend/` (pnpm workspace: `packages/ui`, `apps/admin`, `apps/portal`);
  - `docker/`, `scripts/`, `docs/`, `.github/`.
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
  - ESLint rules that block hard-coded UI text and physical (non-RTL) Tailwind classes.
- **Stack:** `make up` boots Traefik, PostgreSQL 18, two Valkey instances (noeviction+AOF / LRU), Meilisearch, web, worker, beat and frontend, all with healthchecks. The data stores sit on an internal network with no egress.
- **Production images:**
  - app: non-root uid 10001, Gunicorn + Uvicorn workers, no pip;
  - frontend: unprivileged nginx with immutable assets and SPA fallback.
- **CI** (`.github/workflows/ci.yml`):
  - `make up`, `make smoke`, lint, types and tests;
  - production image build, Trivy (fixable HIGH/CRITICAL fail the build), licence gate.
- **Decisions:** [ADR-0001](adr/0001-stack-and-versions.md) (stack and versions) and [ADR-0002](adr/0002-foundations-identity-routing-networks.md) (identity, routing, networks).

### Verify
```bash
make secrets   # .env with generated secrets; picks port 8080 if 80 is busy
make up        # builds, waits for every healthcheck, migrates
make smoke     # routing, isolation, readiness through Traefik
make test      # pytest (44 tests, 98% coverage) + Vitest (7 tests)
make lint && make typecheck
make build && make licenses
```
Then open the admin and the portal: `http://admin.localhost:<port>` and `http://app.localhost:<port>`.

### Acceptance (SPEC §16)
| Criterion | Result | Evidence |
|---|---|---|
| Monorepo scaffold | PASS | Layout per §5; 15 backend apps registered |
| `make up` boots Traefik, Postgres, both Redis, Meilisearch, web (hello + health), worker, beat, frontend shells | PASS | All 9 services healthy; `make smoke` passes, including right after forced recreation |
| CI green: lint, type, test, build, Trivy, licence gate | PASS locally / CI NOT VERIFIED | Every step passes locally with the same `make` targets. GitHub refused to start run #1 ("account is locked due to a billing issue"), so the workflow has not executed yet |
| `.env.example` | PASS | Generated into `.env` (mode 600, git-ignored) by `make secrets` |
| ADR-0001 stack | PASS | Versions verified 2026-10-03, with deviations explained |

### Known issues and notes
- **GitHub Actions cannot start** until the account's billing lock is cleared (GitHub → Settings → Billing and plans). Then re-run CI, and tag `m1-done` once it's green.
- `docs/SPEC.md` is kept out of git (via `.git/info/exclude`) until the owner decides whether the spec may be public, because the repository is public.
- Dev runs plain HTTP on `*.localhost`; the production TLS entrypoint, ACME and HSTS arrive with the production overlay (M13–M15).
- Traefik reads a read-only Docker socket in dev; a socket proxy replaces it in M14.
- Access logs stay off until the redacting log pipeline exists (M2/M13), because Xtream URLs carry credentials.

### Next: M2 (backend foundation)
- structlog with credential redaction;
- problem+json errors;
- DRF + drf-spectacular (OpenAPI 3.1) and the Orval client (`make api-client`);
- settings registry and feature flags, audit app, Prometheus metrics.
