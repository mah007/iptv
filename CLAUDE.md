# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Smart IPTV
The full specification is `docs/SPEC.md`. Read it before every milestone. Progress lives in `docs/PROGRESS.md`, and decisions in `docs/adr/NNNN-title.md`; ADR-0001 holds pinned versions and ADR-0002 the identity, routing and network foundations.

`docs/SPEC.md` exists locally but is excluded from git (`.git/info/exclude`) because the GitHub repo is public. Never commit or publish it until the owner decides. The owner dropped hosted CI: where the spec says "CI", "GitHub Actions" or a release workflow, follow ADR-0003's mapping (checks go into `make ci`).

Spec map, for reading one section mid-milestone: §1 rules · §3 architecture · §5 repo layout · §6 data model · §7 services (7.1 scan, 7.2 parse/match, 7.3 transcode, 7.4 entitlements/playback, 7.5 Xtream, 7.6 billing, 7.7 notifications, 7.8 search, 7.9 recommendations) · §8 admin UI · §9 portal · §10 REST API · §11 security · §12 Nginx edge · §13 Docker · §14 observability · §15 tests and gates · §16 milestones and acceptance criteria.

## What this is
A self-hosted OTT/VOD subscription platform for **owned or licensed content** only. It has:

- a Django control plane;
- an Nginx streaming plane;
- an Xtream-compatible API for IPTV apps;
- a React admin SPA and customer portal (ar/en, RTL).

## Architecture (what spans many files)
- **Hosts.** Traefik routes `app.` (portal), `api.` (`/api/v1`), `tv.` (Xtream), `admin.` (admin SPA) and, in the small tier, `media.` (Nginx edge). Django mirrors this with `config/urls_api.py`, `urls_xtream.py` and `urls_internal.py` (`/internal/stream-auth` and `/metrics`, internal network only).
- **Playback.** Xtream `/movie|/series/{u}/{p}/{xc_id}.{ext}`, or web `POST /api/v1/playback/start`, runs the entitlement checks in the fixed fail-fast order of §7.4, takes a concurrency slot via an atomic Redis Lua script, and returns a 302 to `media.*/v/{token}/…`. At the edge, njs verifies the HMAC token (current and previous `kid`), then `auth_request` to `/internal/stream-auth` (cached 60 s per token) refreshes the heartbeat and checks `kick:{session}`. A Celery sweeper closes sessions with stale heartbeats and records them in Postgres.
- **Two Redis instances, not interchangeable.** `redis-state` (noeviction, AOF) holds sessions, slots, kicks, `ent:{user}` entitlements and the Celery broker. `redis-cache` (allkeys-lru) holds only recomputable data, such as TMDB responses and Xtream catalog JSON.
- **Writers invalidate derived caches.** Rebuild `ent:{user}` on any change to subscription, plan, access rules or user status. Invalidate `xc:{plan_hash}:{locale}:{action}[:{category}]` on any catalog, plan or category change. Subscriptions change only through `subscriptions.activate(...)`, which snapshots the plan, rebuilds the entitlement, audits and notifies.
- **Ingest.** Watcher or reconciliation scan → guessit parse → ffprobe → TMDB match (auto-accept only at ≥ 0.85 with a ≥ 0.10 lead, otherwise a `MatchReview`) → en+ar enrichment and images → transcode → title `ready` once `compat_mp4` exists. Per-library `processing_policy` (`ingest|on_demand|passthrough`) alters this flow. A file no provider knows becomes a manual movie from the review queue (ADR-0026: `metadata_source=manual`, every field locked, refresh refused with `MANUAL_METADATA`, frame artwork when none is uploaded).
- **Live TV (ADR-0017).**
  - `live` (`manage.py run_live`) runs one FFmpeg per watched channel into the `live-data` volume: an HLS window plus the catch-up archive.
  - `live-relay` (`apps.live.relay`, ASGI without Django) serves `live.ts`, the live playlist and catch-up windows behind the edge.
  - Tokens carry the rendition `live` or `archive`, with the channel UUID as the title.
  - Live knobs are `live.*` settings. Source URLs are encrypted, and `apps/live/egress.py` guards every URL an admin supplies.
  - The portal plays live and catch-up through `playback/start` (`title_type` `live`/`catchup`; ADR-0023). The optional MediaMTX ingest asks `/internal/mediamtx-auth` about every sign-in; its users are `live.ingest_*` settings.
- **Edges and scale (ADR-0022).**
  - `apps.edges.Edge` rows are `local` (nginx-stream), `standalone` (another host) or `cdn` (Bunny in front of one of ours). `edges.choose()` picks one per session at playback start; the local edge is the fallback.
  - Standalone edges use `edges.<domain>` (`config/urls_edge.py`, bearer secret per edge): the control channel (keys, the S3 read-only key, the CDN secret) and stream-auth (Redis only, `edges:auth`).
  - Renditions publish to the S3 origin with `apps/media/s3.py` (our SigV4) on the `publish` queue. The token title is then the published key `<asset>-g<gen>`.
  - The read replica is read only by code marked `@replica.reporting` or using `replica.cursor()`.
- **Celery queues.** `worker` consumes default/scan/metadata/images/notify/publish. Transcoders detect their hardware at start and subscribe only to the matching `transcode.{nvenc,qsv,vaapi,cpu}` queues. Rendition ladders and encoder presets live in `streaming/ffmpeg/profiles.yaml`, not in code.
- **Imports (ADR-0025).** The `importer` service (rclone, a separate process) consumes only the `import` queue and is the only service that mounts the libraries read-write (`make hardening-test` enforces it). Jobs stage in `<library>/.smart-iptv-import/<job>/` and move in atomically, then `scan_path` runs the usual pipeline; the job's rights fill the matched title's empty fields (`apps/imports/rights.py`). rclone gets credentials only from a per-run 0600 config in a private tmp folder, never argv or env. Every import is admin-started for chosen files or one link: no automation, no torrents or indexers.

## Hard rules
- No content-acquisition features (torrent, Usenet, indexers, scrapers). Media enters only through admin libraries.
- Dependencies imported into our code must be MIT/BSD/Apache-2/ISC/zlib/MPL-2, or LGPL used unmodified. No GPL/AGPL code imported or copied (`tmdbsimple` is GPL-3, so we write our own TMDB client). Run GPL/AGPL tools (FFmpeg, Grafana, Loki, k6) only as separate processes. Never copy from Dispatcharr, Jellyfin, Kyoo, iptv-proxy, m3u-editor or Xtream-UI.
- Metadata comes from TMDB (primary) and TheTVDB (TV fallback), with TMDB attribution shown. Never use OMDb or IMDb datasets. Subtitle downloading stays off behind a feature flag.
- Transcode at ingest and direct-play at runtime. Real-time transcoding is only a capped fallback.
- Media requests never touch Postgres: the edge verifies the HMAC token, and the cached `auth_request` touches Redis only.
- Never log credentials or tokens; redaction runs in structlog, Nginx, Traefik and Alloy, and a test fails if a raw password reaches a log. Never expose storage paths: `storage_key` is never serialized, and admin views show library-relative paths. Never commit secrets; only `.env.example`.
- Xtream JSON must match the PHP-panel types in SPEC §7.5 exactly. The easy ones to break:
  - strings for `category_id`, timestamps, counts and ports;
  - ints for `stream_id`/`series_id`/`season_number`;
  - `""`, never `null`;
  - `backdrop_path` always an array;
  - `episodes` always an object keyed by season-number strings, including `"0"` (empty is `{}`);
  - failed auth is `{"user_info":{"auth":0}}` with HTTP 200, identical for an unknown user and a wrong password.

  Contract tests (schemas and golden fixtures in `compat/`) guard it.
- No TODO stubs or fake implementations on the critical path.
- Before pinning a dependency, check its latest stable version and licence. If a library behaves differently from the spec, report it and propose an alternative; don't silently work around it.
- Load-test only on the local network, never against a CDN or public egress.

## Commands
Everything runs in containers through the Makefile (`make` lists targets). The stack must be up for backend tests: they use the real Postgres, Valkey and Meilisearch.
- `make secrets` (creates `.env`; never read or print it), `make up` (build, wait for health, migrate), `make smoke`, `make down`, `make ps`, `make logs s=web`, `make shell`
- `make test`: pytest with 85% coverage enforced, plus Vitest.
- `make lint`, `make fmt`, `make typecheck`, `make build` (production images), `make scan` (Trivy) and `make licenses` (licence gate); run the last two after `make build`.
- `make ci`: the full quality gate (ADR-0003 lists its steps). It checks the committed tree: commit first, or use `ALLOW_DIRTY=1` for a check of uncommitted work. There's no hosted CI, so run it before pushing; before tagging `m{N}-done` run `make ci BUILD_FLAGS="--pull --no-cache"`. Don't add GitHub Actions or another hosted CI.
- Single backend test: `make test-backend t="apps/core/tests/test_routing.py -k internal"`. Passing `t` skips the coverage gate.
- Parallel backend test runs (several agents at once) must each pass a lane, `lane=1`, `2` or `3`: a lane has its own Postgres test database and Redis indexes. Two runs in the same lane drop and flush each other's data.
- Single frontend test: `docker compose --project-directory . -f docker/compose.yml -f docker/compose.dev.yml run --rm frontend pnpm --filter @smart-iptv/admin exec vitest run src/app.test.tsx`
- Host shortcuts (faster, outside containers): in `backend/` run `uv run ruff check .` and `uv run mypy .`; in `frontend/` run `pnpm lint`, `pnpm typecheck` and `pnpm test`.
- `make api-client` regenerates the OpenAPI schema and the Orval client in `frontend/packages/api` (never hand-edited); `make ci` fails if it's stale. Run it after any admin API change.
- `make seed args=--reset-admin-password` loads demo data and prints a new admin password once. TOTP codes in dev come from the DEBUG-only `manage.py totp_code`.
- `make sample-media` generates legal synthetic media into `./media`; `make media-ready` waits until a sample movie and series are scanned and transcoded; `make live-ready` makes the synthetic live channel (the dev MediaMTX loops a test pattern; a generated guide; 3 minutes of catch-up), which compat-live, the IPTVnator journey, e2e-admin and e2e-portal play.
- Imports (ADR-0025): `make import-test` (a throwaway rclone SFTP/HTTPS server on the egress network, a folder and a link through the importer, scanned; in `make ci`). Settings `library.import_max_concurrent`, `library.import_reserve_gb`, `library.import_bandwidth_mbit`. Backend tests start real `rclone serve sftp|webdav|s3` on loopback (`apps/imports/tests/conftest.py`); the egress guard needs `allow_loopback` there. Runbook `docs/runbooks/import-media.md`.
- Xtream contract: `make compat` (schemas and golden fixtures, no stack), `make compat-live` (the running tv host, play URLs included, and a 720p contract account whose `.mp4` must be a capped compat MP4: it waits for the remux) and `make e2e-iptvnator` (IPTVnator in Docker, driven by Playwright with the host's Chrome). All three run in `make ci`.
- Live ingest sign-ins (ADR-0023): `make mediamtx-test` runs the production MediaMTX against a throwaway web process (`/internal/mediamtx-auth`) and checks publishing, reading and refusals in about 15 s; in `make ci`. The dev MediaMTX keeps `authMethod: internal`.
- Browser E2E: `make e2e-admin` and `make e2e-portal` (Playwright with the host's Chrome against the dev stack, axe included; they need `make media-ready`). Both run in `make ci`. Run admin Vitest in a fresh `compose run` container: inside the running frontend container it gets OOM-killed.
- Visual regression (ADR-0024): `make visual` compares key admin and portal pages (en/light, ar/dark; 1440 and 390 px) with the baselines in `frontend/apps/*/e2e/visual/baselines/`; pages replay recorded API fixtures under a fixed clock, so only the SPAs come from the stack. In `make ci`. `make visual UPDATE=1` rewrites changed baselines; `make visual RECORD=1` re-records the fixtures from the dev stack first (needs `media-ready` and `live-ready`) after an API change on those pages. The portal E2E's two-factor journey reads codes with `totp_code` like the admin's.
- Transcoding with a GPU in dev: `make up GPU=nvidia,intel` adds the opt-in overlays; the server is CPU-only.
- Servers: `scripts/install.sh` installs, updates (`--update`) or reconfigures (`--reconfigure`) a production host interactively (`--yes` unattended, `--dry-run` changes nothing). Test changes to it with `--dry-run` in an `ubuntu:24.04` container and ShellCheck (`docker run --rm -v "$PWD/scripts:/mnt:ro" koalaman/shellcheck:stable -x /mnt/install.sh`).
- Monitoring (ADR-0018): `make up MONITORING=1` adds the opt-in overlay (Prometheus, Grafana, Loki, Alloy, Alertmanager, exporters; `docker/compose.monitoring*.yml`); pass `MONITORING=1` to down/ps/logs too, or put it in `.env`. While iterating on a shared stack, start single services with `docker compose ... -f docker/compose.monitoring.yml -f docker/compose.monitoring.dev.yml up -d <service>`. `make monitoring-test` (configs, promtool rule tests, dashboard queries, the Alloy/Loki redaction pipeline; in `make ci`), `make monitoring-smoke` and `make monitoring-firedrill` (overlay up; the drill's email lands in Mailpit), `make monitoring-dashboards` (dashboards are generated by `monitoring/grafana/build_dashboards.py`; never edit the JSON). Alert recipients, severities and the edge capacity are registry settings (group `monitoring`), the Telegram bot under `notifications`; `manage.py set_setting KEY --stdin|--env NAME` sets one from a script. Alertmanager fetches its fallback channels from Django (`/internal/alert-fallback`) every minute.
- Configuration (ADR-0019): every operator setting is a registry key edited in the admin (Settings, Settings → Integrations with a Test per integration, Settings → System read-only). Secrets are `sensitive=True` keys, Fernet-encrypted in `core.Setting.ciphertext`, read only with `get_secret` (`get_setting` refuses them), audited without values. `SettingDef(env=...)` names the `.env` fallback (the Django setting, else the variable); the API reports the source. `.env` is bootstrap only. `manage.py settings_report [--count]` lists settings still coming from `.env` (names only). A key made sensitive later needs a data migration using `apps.core.migration_helpers.encrypt_rows`.
- Backups and hardening (ADR-0020): `make backup` (a backup now, through the `backup` service), `make restore-test` (the restore drill: latest backup plus WAL into a throwaway server, counts compared; in `make ci`), `make hardening-test` (cap_drop, no-new-privileges, read-only roots, digest pins and socket mounts on the rendered prod config with every overlay; in `make ci`), `make crowdsec-test` (CrowdSec overlay up: a banned address gets 403 and the admin switch works). The `backup` agent (`apps/ops/agent.py`, stdlib only) only executes jobs Django queues (`apps/ops/backups.py`); schedules, retention and the bucket are `backups.*` settings, personal-data retention `privacy.*`. Key and password rotation is `scripts/rotate-keys.sh` (audited `keys.rotate`); test it only in a throwaway compose project whose services set `traefik.enable=false`, never on the shared stack.
- Database and Valkey users (ADR-0020): app services connect as `iptv_app` (`DB_ROLE=app` in `docker/app.env`: no DDL, the audit log append-only) and Valkey user `app` (dangerous commands denied). Migrations run as the owner: `make up`/`make migrate` do it; by hand `docker compose ... run --rm migrate`, or `exec -e DB_ROLE=owner web python manage.py migrate`. `manage.py migrate` as `iptv_app` is refused. Grants are reapplied after every migrate (`apps/ops/grants.py`); a new table needing `audit_purge`-style protection gets a `SECURITY DEFINER` function, not a grant. Tests run as the owner and Valkey `default`.
- Scale (ADR-0022):
  - `make edge-test`: every edge mode in throwaway `prework-*` containers, the standalone edge and CDN pulls included; in `make ci`.
  - `make s3-dev`: SeaweedFS as the S3 origin, saved in the settings, everything published. `make edge-dev`: a standalone edge on 127.0.0.1:18090, its own compose project.
  - `make up REPLICA=1`: the read replica overlay (or `REPLICA=1` in `.env`).
  - `make rollout [s=frontend]` and `make rollout-test`: zero-downtime proof (`scripts/rollout.sh`).
  - `make loadtest [tiers=100,500,1000]`: Locust, local only. It switches web to production gunicorn, uses throwaway customers and a memory guard, and puts everything back.
  - `make deploy DEPLOY_HOST=user@host`: `install.sh --update` over SSH, for a committed and pushed tree only.
  - Migrations must stay backward compatible: the old web serves while they run.
- Application security (ADR-0021):
  - `make semgrep` (the project's own rules in `.semgrep/rules.yml`, with their tests), `make audit` (pip-audit and `pnpm audit --prod`; dated exceptions only in `scripts/audit-ignore.toml`) and `make zap` (ZAP baseline against the dev hosts; accepted alerts with reasons in `security/zap/plan.yaml`): all three in `make ci`.
  - A view that changes something sensitive sets `step_up_methods` (admins confirm with a code or key; customers with their password before TV logins); protective actions (block, revoke, kill, suspend) never do. Tests get step-up for free unless marked `@pytest.mark.step_up`.
  - HTML responses carry their own CSP (`apps.core.security.document_csp()`); the SPAs' policy lives in `frontend/packages/ui/csp/policy.js`; no raw HTML in React (ESLint). Libraries that add `<style>` at run time need the nonce (`cspNonce()`); the admin and portal E2E fail on any CSP violation.
  - `manage.py reset_admin_mfa <user>` resets an admin's second factors from the host; `set_setting security.admin_ip_allowlist --stdin < /dev/null` clears the allowlist.
  - IP geolocation is DB-IP Lite in the `geoip-data` volume (worker downloads, web reads); tests build their own databases (`apps/core/tests/geoip_data.py`) and never download.

## Gotchas learned building M1
- Dev runs on `HTTP_PORT` from `.env`. It's 8080 on this machine, where Apache holds port 80, so URLs are `http://admin.localhost:8080`.
- Don't add pnpm `minimumReleaseAgeExclude` entries. If pnpm refuses a too-new release, widen the range and let it pick an older one.
- uv uses `prerelease = "explicit"`. kombu 5.6 (stable) needs `redis<6.5`, so redis-py stays on 6.4 until kombu 5.7 is final. redis-py types sync calls as maybe-awaitable, so `cast()` results.
- Traefik registers recreated containers asynchronously; `make smoke` retries for 30 s, and new smoke checks should do the same.
- Production images drop pip and apply distro updates. Gunicorn 26 needs `--no-control-socket`, because `/app` is read-only to uid 10001.
- Every Django app keeps a `migrations/` package. Without one, `makemigrations` ignores the app's new models, so `migrate` never creates their tables; `make lint` runs `makemigrations --check`.
- pnpm denies dependency install scripts unless `allowBuilds` in `pnpm-workspace.yaml` says otherwise; deny by default and allow only what's proven necessary.

## Conventions
- **Python:** 3.13, uv, Ruff, mypy (strict in playback/xtream_api/billing), pytest. Service functions live in `services.py`, not in views or serializers. Use `select_related`/`prefetch_related`, and no N+1 (assert query counts in tests). Thresholds, TTLs and feature flags are typed settings (the settings registry and the `Setting` model), not constants. API errors are RFC 9457 problem+json with a stable `code` (`CONCURRENCY_LIMIT`, `SUBSCRIPTION_EXPIRED`, …).
- **Data:** every model inherits `apps.core.models.BaseModel` (UUIDv7 `id` plus `created_at`/`updated_at`). Catalog items and episodes also get an integer `xc_id`, because Xtream clients need ints. Money is stored in integer minor units with a `currency` column.
- **Routing:** public endpoints go in the host's URLconf (`config/urls_api.py` or `urls_xtream.py`); internal ones go only in `config/urls_internal.py`. `HostURLConfMiddleware` keeps them apart.
- **Tests:**
  - TMDB and TheTVDB calls answer from JSON fixtures (`backend/apps/metadata/fixtures/{tmdb,tvdb}/`, through a fixture transport) when no key is set. `apps.metadata.tmdb.recording` and `manage.py record_tvdb_fixtures` record real ones, which stay out of the public repo.
  - Parser cases live in `backend/tests/data/filenames.csv`.
  - Test the concurrency Lua script against real Redis (Testcontainers), not a mock.
  - Coverage must be ≥ 85% on core apps.
- **Frontend:** TypeScript strict, TanStack Query/Router/Table, shadcn/ui-style components in `packages/ui` (`@smart-iptv/ui`), logical CSS properties only (RTL), all strings via i18next (ar + en). There must be no hard-coded user-facing text. ESLint enforces both: `i18next/no-literal-string`, plus a rule against physical Tailwind classes such as `ml-*` or `text-left`. App strings go in `src/locales/{en,ar}.json`; shared strings live in the `ui` namespace.
- **Commits:** Conventional Commits. Tag `m{N}-done` when acceptance passes.

## Workflow per milestone
1. Read the spec and progress, then plan (files, decisions, risks).
2. Write an ADR if the decision is significant.
3. Implement in small steps, running lint, type checks and tests after each.
4. Update `docs/PROGRESS.md` and the docs.
5. Stop at the checkpoint: what was built, how to verify it, the acceptance checklist, and open questions. Wait for "continue".

Ask before deleting data or volumes, breaking a public contract, adding paid services or non-permissive dependencies, or doing anything irreversible.
