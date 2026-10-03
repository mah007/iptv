# ADR-0001: Stack and pinned versions

- **Status:** Accepted; supply-chain notes amended by [ADR-0003](0003-no-hosted-ci.md) (no hosted CI)
- **Date:** 2026-10-03
- **Milestone:** M1

## Context
SPEC §4 names the stack and asks us to verify each component's latest stable version and licence at M1, then pin them. SPEC §1.2 allows only permissive licences (or LGPL used unmodified) in code we import. Several of the spec's version lines are no longer the newest supported ones, so each choice is made explicitly here.

Versions were checked on 2026-10-03 with `.claude/skills/dep-check` (PyPI, npm, GitHub releases, endoflife.date).

## Decision

### Runtimes and services
| Component | Pinned | Support ends | Licence | Notes |
|---|---|---|---|---|
| Python | 3.13.16 | 2029-10 | PSF | As specified. 3.14 adds `uuid.uuid7()`; revisit when the ecosystem settles. |
| Django | 5.2.17 LTS | 2028-04 | BSD-3 | LTS over 6.1 for support length and third-party compatibility. Upgrade target: 6.2 LTS (Apr 2027). |
| PostgreSQL | 18.6 | 2030-11 | PostgreSQL | **Deviation:** the spec says 17. A greenfield database avoids a major upgrade later, gains a year of support, and adds native `uuidv7()`. All required extensions (pg_trgm, unaccent, btree_gist) are available. |
| Valkey | 9.1.2 | 2031-05 | BSD-3 | Used for both `redis-state` and `redis-cache`. Redis 8 is AGPL/SSPL/RSAL, while Valkey is BSD and protocol-compatible (the spec allows Valkey). |
| Meilisearch | 1.54.3 | — | MIT (community edition) | The enterprise features are BUSL; we use community features only. |
| Traefik | 3.7.13 | — | MIT | |
| Nginx | 1.30.5 (stable) | — | BSD-2 | Frontend static hosting now. The media edge and njs (1.0.1) are pinned in M7. |
| Node.js | 24.21.0 LTS | 2028-04 | MIT | **Deviation:** the spec says 22, which reaches end-of-life in 2027-04. 24 is a mature LTS, matches the dev host and still bundles corepack. Node 26 just became LTS; revisit in 2027. |
| pnpm | 12.8.1 | — | MIT | Its default release-age cooldown is respected: no exemptions in `pnpm-workspace.yaml`. |
| uv | 0.12.22 | — | Apache-2.0/MIT | |

### Backend (locked in `backend/uv.lock`)
`django 5.2.17`, `celery 5.6.3`, `kombu 5.6.2`, `redis 6.4.0`, `psycopg 3.3.6` (LGPL, used unmodified), `uvicorn 0.54.0`, `uvicorn-worker 0.4.0`, `gunicorn 26.2.0`.

Dev only: `pytest 9.1.1`, `pytest-django 4.14.0`, `pytest-cov 7.1.0`, `ruff 0.16.10`, `mypy 1.19.1`, `django-stubs 5.2.9`, `celery-types 0.26.0`, `watchfiles 1.3.0`.

- **redis-py 6.4, not 8.x.** Stable kombu 5.6 requires `redis<6.5`. Pinning redis-py 8 made the resolver quietly select `kombu 5.7.0a1`, an alpha. `[tool.uv] prerelease = "explicit"` now makes any such resolution fail instead.
- **mypy 1.19, not 2.x.** django-stubs 5.2.x (for Django 5.2) caps mypy below 1.20.
- DRF, drf-spectacular, structlog, argon2-cffi and the rest are pinned when introduced (M2/M3), each with a `dep-check` run.

### Frontend (locked in `frontend/pnpm-lock.yaml`)
`react 19.3.0`, `vite 8.3.2`, `tailwindcss 4.3.3`, `@tanstack/react-router 1.170.41`, `@tanstack/react-query 5.104.1`, `i18next 26.4.2`, `react-i18next 17.0.15`, `vitest 5.0.3`, `eslint 10.11.0`, `typescript-eslint 8.71.0`, `prettier 3.9.9`, plus shadcn-style primitives (`class-variance-authority`, `clsx`, `tailwind-merge`, `@radix-ui/react-slot`, `lucide-react`).

- **TypeScript 6.0.3, not 7.x and not 5.x.** TypeScript 7 (the native compiler) is out, but typescript-eslint supports only `<6.1`. 6.0 is the newest release the lint toolchain supports. Revisit when typescript-eslint supports 7.

### Supply chain
- Trivy 0.75.0 runs from `aquasec/trivy:0.75.0@sha256:af6acf9a6b85dfe389a1941505c0ce9efef52a4719635e1a962f022a3d855daa` (`TRIVY_IMAGE` in the Makefile; `make scan`, part of the `make ci` gate). Update both together.
- The licence gate (`scripts/license_gate.py`, `make licenses`) checks production dependencies of both stacks, and fails on GPL/AGPL/SSPL/BUSL/non-commercial or unknown licences, and on an empty input.
- Production images apply distro security updates at build time. The app image removes pip, whose vendored packages were the only Trivy findings.

## Alternatives considered
- **Follow SPEC §4 versions literally** (Node 22, PostgreSQL 17, TypeScript 5): rejected. Each is behind a better-supported line, and the spec itself asks to verify versions at M1.
- **Django 6.1:** newer, but supported only until 2027-12 and less proven with the third-party apps we need.
- **Redis 8:** licence (AGPL/SSPL/RSAL) is acceptable only as a separate process, but Valkey removes the question entirely.
- **Allow pnpm cooldown exemptions** for the very newest eslint/lucide-react: rejected, since the cooldown is a supply-chain control.

## Consequences
- Upgrade checkpoints: Django 6.2 LTS (2027-04), Node 26 (2027), TypeScript 7 (when typescript-eslint supports it), redis-py 8 (when kombu 5.7 is final), Python 3.14 (native uuid7).
- Every new dependency goes through `dep-check` and an ADR note; the licence gate in `make ci` enforces it.

## M2/M3 additions
Checked on 2026-10-03 with `.claude/skills/dep-check`. Every package passes the licence gate (61 Python packages, including dev ones, against the backend virtualenv).

### Backend, M2 (locked in `backend/uv.lock`)
| Package | Pinned | Licence | Notes |
|---|---|---|---|
| djangorestframework | 3.18.1 | BSD-3-Clause | Requires Django ≥ 5.2. |
| drf-spectacular | 0.30.0 | BSD-3-Clause | OpenAPI 3.1 output; `make api-client` runs it with `--validate --fail-on-warn`. |
| django-filter | 26.1 | BSD-3-Clause | **Not 26.2:** 26.2 was published on the day of the check. We wait out the same release-age cooldown pnpm applies to npm packages; take 26.2 at the next milestone's re-check. |
| structlog | 26.1.0 | MIT OR Apache-2.0 | |
| django-prometheus | 2.5.0 | Apache-2.0 | The 2.6.0.dev releases are pre-releases; `prerelease = "explicit"` keeps them out. |
| prometheus-client | 0.26.0 | Apache-2.0 AND BSD-2-Clause | Declared directly because `apps.core.metrics` imports it. |

Transitive, via drf-spectacular: PyYAML 6.0.3, jsonschema 4.26.0, jsonschema-specifications 2025.9.1, referencing 0.37.0, rpds-py 2026.6.3, attrs 26.1.0, inflection 0.5.1 (all MIT) and uritemplate 4.2.0 (BSD-3-Clause OR Apache-2.0).

Dev only: `djangorestframework-stubs 3.16.9` (MIT), which brings `types-PyYAML`. mypy runs its `mypy_drf_plugin`.
- **Not 3.17 or 3.18:** those require django-stubs ≥ 6.0 (Django 6.x), and we pin django-stubs 5.2 for Django 5.2 LTS. 3.16.9 is the newest release that supports django-stubs 5.2. Its stubs track DRF 3.16; the APIs we use are unchanged in 3.18.
- django-filter and django-prometheus ship no type information, so mypy ignores missing imports for those two only.

### Frontend, M2 (locked in `frontend/pnpm-lock.yaml`)
`orval 8.39.0` (MIT), a dev dependency of `@smart-iptv/api`. It generates the TanStack Query hooks (fetch client) from `openapi/admin.yaml` (ADR-0004).

M3 dependencies are added to this section when they are introduced.
