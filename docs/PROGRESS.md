# Progress

| Milestone | Status | Tag |
|---|---|---|
| M1 Infrastructure & repo | Done | `m1-done` |
| M2 Backend foundation | Done | `m2-done` |
| M3 Accounts (POC scope "M3-lite": access profiles instead of plans and subscriptions) | Done for the POC; plans, subscriptions and billing move to the final commercial slice | — |
| POC slice 2: M4 library, M5 metadata, M6-lite catalog | Done ([ADR-0009](adr/0009-library-ingest-and-media-storage.md)) | — |
| POC slice 3: M7 direct play, M8-lite transcoding | Done on the dev stack ([ADR-0010](adr/0010-renditions-transcoder-and-edge-playback.md)) | — |
| POC slice 4: M9 Xtream API and the IPTV-app demo | Done on the dev stack ([ADR-0008](adr/0008-xtream-api.md), [ADR-0011](adr/0011-xtream-catalog-playback-and-sign-in-limits.md)); deployment to tv.mah007.net next | — |
| M10–M15, then the commercial slice | Planned | — |

**Owner decision (2026-10-03): proof of concept first.** An admin creates a customer with device credentials, media is scanned, matched and transcoded, and an IPTV app logs in and plays. Everything commercial (plans, subscriptions, billing, payments, invoices, trials, notifications) is the last slice.

## POC slices 3 and 4: transcoding, edge playback, Xtream on the real catalog (2026-10-04)

### Done
- **Transcoding (M8-lite)** ([ADR-0010](adr/0010-renditions-transcoder-and-edge-playback.md)):
  - Rendition and TranscodeJob.
  - Compatible sources direct-play through a read-only symlink; the rest get a compat MP4: a remux, or an encode on the transcoder service.
  - The transcoder detects its hardware and consumes only the matching `transcode.*` queues.
  - Jobs report progress over SSE, verify, move atomically, retry (GPU failures fall back to CPU), and can be cancelled or re-prioritised from the admin API.
  - Benchmarks are in [docs/benchmarks/transcode.md](benchmarks/transcode.md).
- **Edge playback:** nginx-stream serves `/v/<token>/` with njs token checks and the Redis-only stream-auth. Range requests get 206; tampered, out-of-scope and expired tokens get 403.
- **Xtream on the real catalog** ([ADR-0011](adr/0011-xtream-catalog-playback-and-sign-in-limits.md)):
  - ready titles in visible categories;
  - play URLs through `start_playback`;
  - `active_cons`;
  - failed sign-ins limited per IP and per username;
  - a Traefik rate limit on the tv router.
- **Fix:** Celery queues were all bound to the same routing key, so every message reached every queue. Each queue now binds under its own name.

### Evidence (dev stack)
- **Transcoding:** The Matrix (HEVC 1080p) → H.264 High/AAC faststart MP4. The H.264 MP4 plays as is. MKVs remuxed in under 1 s. A 2160p HDR source tone-mapped to 1080p. Every sample movie and series is ready.
- **Edge:** a signed URL gives 200 and 206. Tampered and expired tokens get 403 with `X-Reason`.
- **Stream limits:** `max_streams=1` refuses a second device with `CONCURRENCY_LIMIT`. Under `kick_oldest`, the first device is stopped within 28 s.
- **Admin kill:** the stream stops within 54 s.
- **Xtream contract:** `make compat` 161 passed; `make compat-live` 195 passed, 3 skipped (live/EPG checks wait for M12).
- **IPTVnator:** `make e2e-iptvnator` 7/7. It signs in, browses, plays Wadjda at 1080p, and plays a series episode at 720p.
- **Benchmark** (a hard 60 s 1080p HEVC clip): libx264 1.03×, NVENC 7.88×, QSV 4.12×, VAAPI 7.09× real time. The server is CPU-only.

## POC phase 1: ingest, playback and Xtream cores, admin pages (2026-10-04)

### Done
- **Slice 2: library → metadata → catalog** ([ADR-0009](adr/0009-library-ingest-and-media-storage.md)):
  - libraries over `/media` (read-only), with a watcher and reconciliation scans;
  - the scan, probe, parse, match and enrich pipeline as Celery tasks, with Redis locks and SSE progress;
  - TMDB matching with auto-accept or a review queue, plus an offline fixture mode labelled synthetic;
  - artwork served by the nginx-stream edge on `media.<DOMAIN>` with immutable caching;
  - catalog models with `xc_id`, categories, and the admin API.
- **Playback core (M7):**
  - signed media tokens with key rotation;
  - an atomic Lua concurrency script (reject or kick the oldest);
  - sessions and the SPEC §7.4 checks in order;
  - the Redis-only `/internal/stream-auth`;
  - the sweeper;
  - admin sessions with kill and an SSE feed.
- **Xtream API core (M9)** ([ADR-0008](adr/0008-xtream-api.md)):
  - login and catalog actions, M3U, XMLTV and play redirects, built from DTOs and validated against `compat/`;
  - constant-time authentication;
  - a scope-keyed catalog cache;
  - no credentials in any log.
- **Admin pages:**
  - MFA sign-in, dashboard, customers, the create-customer wizard with a one-time credential and QR, devices, admins and roles, audit, settings;
  - Arabic and English, light and dark, mobile.
- **Admin-chosen credentials** (owner request): admins may type the customer's account username and each device's Xtream username and password, or leave them generated.
  - Characters are limited to those that need no URL encoding inside IPTV apps.
  - Usernames are unique regardless of case.
  - The minimum password length is a setting.

### Evidence
- 1881 backend tests pass, 96.35% coverage.
- Ruff, mypy, `makemigrations --check` clean.
- 229 frontend tests pass; ESLint and Prettier clean.
- Slice 2 end to end on the dev stack: The Matrix, Breaking Bad, Inception and Wadjda matched with Arabic overviews; the ambiguous file resolved from the review queue; the poster served with `Cache-Control: public, max-age=31536000, immutable`.

## M2 + M3-lite: backend foundation, accounts, admin UI kit (2026-10-03)

### Done
- **M2 foundation** ([ADR-0005](adr/0005-m2-foundations.md)):
  - structlog JSON logs with credential and token redaction;
  - RFC 9457 problem+json errors;
  - OpenAPI 3.1 (drf-spectacular) with a generated TanStack Query client in `frontend/packages/api` (`make api-client`; the gate fails on a stale client);
  - a typed settings registry with feature flags, editable through the admin API;
  - an append-only audit log (database trigger) with an admin API;
  - Prometheus metrics; health endpoints.
- **Admin API transport** ([ADR-0004](adr/0004-same-origin-api-session-auth.md)): same-origin at `admin.<DOMAIN>/api`, with session cookie and CSRF.
- **Accounts, "M3-lite"** ([ADR-0006](adr/0006-accounts-and-authentication.md)):
  - users, RBAC with seeded roles and permissions;
  - a manual access profile per customer (`CustomerAccess`: expiry, streams, devices, quality, concurrency policy, content kinds, categories);
  - devices with Xtream credentials (Argon2id, shown once on create and reset);
  - IP, network and country access rules;
  - admin sign-in with TOTP MFA (seeds Fernet-encrypted at rest) and django-axes lockout;
  - entitlements cached in Redis, plus an expiry job;
  - admin API for customers, devices, credentials, roles, admins, categories, audit, settings and dashboard KPIs;
  - `seed_demo` and the DEBUG-only `totp_code` commands.
- **Admin UI kit and shell** (`@smart-iptv/ui`):
  - the component set for the admin pages;
  - self-hosted Inter and IBM Plex Sans Arabic (OFL-1.1, now allowed by the licence gate);
  - light and dark themes, Arabic RTL and English.
- **POC groundwork, built ahead of its slices:**
  - ingest cores: filename parsing (guessit, LGPL, unmodified), fingerprints, TMDB client with rate limit, cache and an offline fixture mode, match scoring, artwork pipeline, Arabic search normaliser, ffprobe parsing, transcode planner and `streaming/ffmpeg/profiles.yaml`, hardware detection;
  - the Nginx media edge with njs token checks ([ADR-0007](adr/0007-media-token-and-edge.md));
  - the Xtream contract kit in `compat/` (JSON Schemas, valid and invalid fixtures, a fixture server, the IPTVnator end-to-end driver);
  - `backend/tests/data/filenames.csv` and `scripts/sample_media.sh`;
  - client setup guides (ar/en), a STRIDE threat model draft, monitoring configuration.

### Verify
```bash
make up
make seed args=--reset-admin-password   # demo data; prints a new admin password once
make ci
```
Admin API: `http://admin.localhost:<port>/api/v1/` (OpenAPI schema at `/api/v1/schema`).

### Acceptance
| Criterion (SPEC §16 M2) | Result | Evidence |
|---|---|---|
| Settings split, core models (UUIDv7, timestamps) | PASS | M1 settings; `BaseModel` |
| structlog with redaction, problem+json, OpenAPI | PASS | `apps/core` tests; the gate's API-client check |
| Celery queues, settings registry, feature flags, audit, health, metrics | PASS | `apps/core`, `apps/audit` tests |
| Quality gate | PASS | `make ci` passed on this tree in 138 s: smoke, lint (including missing migrations), mypy on 194 files, API client up to date, 1413 backend tests (96.6% coverage; 11 skipped, needing FFmpeg or a standalone Redis), 183 frontend tests, both production images healthy with `check --deploy`, Trivy clean, licence gate (23 self-test cases; 61 Python and 145 npm packages) |

M3 is accepted only for the POC scope: plans, `activate()`, grace jobs and the date property tests come with the commercial slice.

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
