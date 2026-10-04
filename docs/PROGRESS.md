# Progress

| Milestone | Status | Tag |
|---|---|---|
| M1 Infrastructure & repo | Done | `m1-done` |
| M2 Backend foundation | Done | `m2-done` |
| M3 Accounts, plans, subscriptions | Done: plans, `activate()`, grace/expiry jobs, renewal property tests, invoices, payments, notifications ([ADR-0012](adr/0012-billing-subscriptions-and-notifications.md)) | `m3-done` |
| M4 Library scanner, M5 Metadata | Done ([ADR-0009](adr/0009-library-ingest-and-media-storage.md)); live TMDB on the server since 2026-10-04 | `m4-done` (M5 waits for TheTVDB) |
| M6 Catalog & search | Done: customer catalog API, collections, home rows, Meilisearch with the Arabic normaliser and fallback, engagement ([ADR-0013](adr/0013-customer-api.md)) | `m6-done` |
| M7 Direct-play streaming | Done ([ADR-0007](adr/0007-media-token-and-edge.md), [ADR-0010](adr/0010-renditions-transcoder-and-edge-playback.md)) | `m7-done` |
| M8 Transcoding | Done: compat MP4, HLS fMP4 ladder with per-plan ceilings, UHD/HDR, trickplay, subtitles (cp1256-safe), retention ([ADR-0014](adr/0014-hls-uhd-subtitles-thumbnails.md)); GPU ladder benchmarks pending | — |
| M9 Xtream API | Done ([ADR-0008](adr/0008-xtream-api.md), [ADR-0011](adr/0011-xtream-catalog-playback-and-sign-in-limits.md)) | `m9-done` |
| M11 Admin UI | Done: every §8 page that has an API, billing pages, collections, storage, title media panels, live views with a polling fallback; `make e2e-admin` with axe ([ADR-0015](adr/0015-admin-ui.md)) | — |
| M11b Customer portal | Done: portal UI with the Shaka player, progress and resume, account, devices and TV apps, checkout; `make e2e-portal` with axe ([ADR-0016](adr/0016-customer-portal.md)); provider sandboxes pending keys | — |
| M12 Live TV & EPG, M13 Monitoring & logging | In progress ([ADR-0017](adr/0017-live-tv-and-epg.md), [ADR-0018](adr/0018-observability.md) when they land) | — |
| M10 client matrix (needs the owner's devices), M14, M15 | Planned | — |

**Owner decision (2026-10-03): proof of concept first.** An admin creates a customer with device credentials, media is scanned, matched and transcoded, and an IPTV app logs in and plays. Everything commercial (plans, subscriptions, billing, payments, invoices, trials, notifications) is the last slice.

## Acceptance and tags (2026-10-04)

`make ci ALLOW_DIRTY=1 BUILD_FLAGS="--pull --no-cache"` passed for `81b1158` in 647 s (the only uncommitted file was the owner's `.claude/settings.json`). It covered:
- stack and smoke, lint, the offline Xtream contract, types, both API clients current;
- 2362 backend tests at 95.4% coverage; Vitest admin 125, ui 155, portal 45;
- `make media-ready`, `make compat-live` (195 passed, 3 skipped), IPTVnator;
- `make e2e-admin` (88 passed), `make e2e-portal` (3 passed);
- production images built without cache, image smoke, Trivy and the licence gate.

Tagged at `81b1158`, every §16 criterion passing:

| Tag | Evidence beyond the gate |
|---|---|
| `m3-done` | The commercial slice completed M3 ([ADR-0012](adr/0012-billing-subscriptions-and-notifications.md)): plans, `activate()`, expiry and grace jobs, renewal property tests (hypothesis). This supersedes the POC-scope note under M2 + M3-lite. |
| `m4-done` | Watcher, reconciliation and move detection, stable-file checks, probe, guessit parsing, scan SSE ([ADR-0009](adr/0009-library-ingest-and-media-storage.md)); `make sample-media`. |
| `m6-done` | Catalog API, categories and collections, Meilisearch with the Arabic normaliser and fallback, home rows, engagement ([ADR-0013](adr/0013-customer-api.md)). |
| `m7-done` | Edge with njs tokens and the cached `auth_request` heartbeat, start/stop, Lua concurrency, kick, sweeper ([ADR-0007](adr/0007-media-token-and-edge.md), [ADR-0010](adr/0010-renditions-transcoder-and-edge-playback.md)). Progressive MP4 checked with the FFmpeg tools ffplay is built on: an Xtream `/movie/…` request gave a 302 to the signed edge; ffprobe read H.264 1080p, AAC, AC-3 and mov_text; ffmpeg decoded 10 s, then seeked to 15 s over an HTTP range request, without errors. |
| `m9-done` | All VOD and series actions with exact types, per-device credentials, per-plan cache, M3U, redaction, contract suite and IPTVnator green ([ADR-0008](adr/0008-xtream-api.md), [ADR-0011](adr/0011-xtream-catalog-playback-and-sign-in-limits.md)). |

Not tagged yet:
- **M5:** the TheTVDB fallback for TV.
- **M8:** GPU benchmark reports (CPU done; NVENC and QSV wait for an idle desktop).
- **M11:** the global Devices and Lockouts pages need APIs (Live TV and EPG pages come with M12).
- **M11b:** checkout against Stripe and Moyasar sandboxes needs keys.

## M11: admin UI (2026-10-04)

### Done ([ADR-0015](adr/0015-admin-ui.md))
- **Commercial pages:** plans, subscriptions (extend, cancel), payments, invoices, notifications and templates; a customer's billing and watch-history panels; the invite action.
- **Catalog:** collections; a category checklist on plans and access rules.
- **Title media panels** on movies and series: files and renditions, running jobs, audio and subtitle tracks (upload, edit, delete), reprocess with a choice of outputs, artwork (stored, TMDB alternatives, upload, primary) and re-match. Each episode has its own media sheet.
- **Storage page** (`GET admin/storage`, cached 30 s): usage by library, growth over 90 days, the largest titles, and orphan cleanup that always dry-runs first.
- **Settings:** every registry key has en/ar labels; an untranslated row fails the E2E suite.
- **Live views:** sessions and transcode jobs poll every 5 s while the SSE feed is refused.
- **Shortcuts:** `g b` (subscriptions) and `g p` (payments) join the go-to keys.

### Evidence (dev stack)
- `make e2e-admin`: 88 passed in 3.2 min. MFA sign-in; create a customer, reset a device, scan, resolve a review by keyboard, stop a live session; axe on every page and detail in English/light and Arabic/dark with nothing serious or critical; no sideways scroll at 390 px in Arabic; settings translated; palette and shortcuts.
- Vitest: admin 125, ui 155. Backend: 2362 passed (lane 0).

## M11b: customer portal (2026-10-04)

### Done ([ADR-0016](adr/0016-customer-portal.md))
- **Sign-in and passwords:**
  - username, email or phone sign-in, forgot password, and the reset and invitation pages (`/reset-password`, `&welcome=1`);
  - an expired session returns to sign-in and comes back to the page;
  - sign-out clears the cache.
- **Home:**
  - a hero carousel (pauses on hover or focus, honours reduced motion);
  - continue watching with progress bars;
  - the API's rows (recently added, popular, because you watched, top picks, categories, collections) with "See all";
  - rows mount as they scroll near; blurhash placeholders, lazy AVIF/WebP.
- **Browse and titles:**
  - movies and series with genre, category, year and sort filters in the URL, infinite scroll;
  - title pages: backdrop, logo, meta, overview (ar/en), facts, cast linking to people, trailer (YouTube no-cookie), My List, thumbs, Play/Resume/Start over;
  - series seasons and episodes with progress; collection and person pages.
- **Search:** debounced 150 ms; Arabic and English, typos forgiven; grouped movies, series, episodes and people; empty states.
- **Player (Shaka 5.2.12, HLS build):**
  - HLS first, MP4 fallback;
  - quality within the plan's ceiling, audio and subtitle menus, subtitle size and background;
  - sprite previews on the seek bar, the resume prompt, a next-episode countdown;
  - keyboard shortcuts, full screen, PiP;
  - progress every 15 s and on pause, a keepalive stop on exit;
  - clear messages for every §7.4 code.
- **Engagement and account:**
  - My List, watch history with removal;
  - profile (name, language, time zone, marketing), password by email link;
  - devices and TV apps: add with generated or chosen credentials shown once with a QR code, setup guides, reset, rename, remove;
  - subscription and the renew banner;
  - invoices (view, print);
  - plans and checkout (manual instructions; providers only when enabled).
- **Backend:**
  - `manage.py e2e_portal_account`;
  - "started" now scales for titles under five minutes (`min(30 s, 10 %)`), so 30 s samples resume and continue.

### Evidence (dev stack)
- `make e2e-portal`: 3 passed.
  - The journey signs in, searches «المصفوفة», opens The Matrix, plays more than 5 s (HLS 1080p), finds it in Continue watching, and signs out.
  - axe found nothing on sign-in, home, search, title and player.
  - Ten pages each in Arabic/light and English/dark at 390 px: no axe violations, no horizontal scroll.
- Vitest (portal): 45 passed, covering:
  - the locale parity;
  - the progress reporter, VTT sprites, shortcuts and error mapping;
  - pages against a mocked API: sign-in redirect, session expiry, home rows, Arabic search debounce, title page and My List, the player page's resume and errors, Add TV app with QR, chosen-credential checks, manual checkout, invitation and expired links.
- Backend (lane 1): engagement and the e2e command, 34 passed.
- Checked by hand in Chrome (en/ar, light/dark, 390 px and desktop):
  - HLS ABR to 1080p; Arabic sidecar subtitles right to left;
  - sprite previews; next episode auto-plays;
  - Add TV app → QR → remove; manual checkout → invoice → print frame.

## POC deployed (2026-10-04)

The proof of concept runs on the owner's single-host server (runbook: [docs/runbooks/deploy.md](runbooks/deploy.md)). An admin signs in with MFA and creates a customer with an IPTV login; media is scanned, matched and transcoded; and an IPTV app logs in and plays.

### Evidence (production server, from outside)
- Every host answers over Let's Encrypt TLS with HSTS: `tv.` (Xtream), `admin.`, `media.`.
- The edge refuses an unsigned `/v/` request with 403. `/internal/*` answers 404 from outside. A wrong Xtream login gets `{"user_info":{"auth":0}}`.
- The live Xtream contract suite, run against the public URL: **195 passed, 0 failed, 3 skipped**. That includes movie and episode play redirects to the media edge.
- The sample libraries were scanned and transcoded on the server's CPU, and a movie and a series were ready in 21 s.

### Fixed during the deploy
On a deploy that adds tables, the watcher started before `migrate` and crash-looped. Migrations now run as a one-shot `migrate` service that every app service waits for.

### Operator notes
The owner admin is created with `manage.py create_owner` (no demo data). The initial admin and demo IPTV passwords are in a root-only file on the server, never in the repository or logs.

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
