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
- **Ingest.** Watcher or reconciliation scan → guessit parse → ffprobe → TMDB match (auto-accept only at ≥ 0.85 with a ≥ 0.10 lead, otherwise a `MatchReview`) → en+ar enrichment and images → transcode → title `ready` once `compat_mp4` exists. Per-library `processing_policy` (`ingest|on_demand|passthrough`) alters this flow.
- **Celery queues.** `worker` consumes default/scan/metadata/images/notify. Transcoders detect their hardware at start and subscribe only to the matching `transcode.{nvenc,qsv,vaapi,cpu}` queues. Rendition ladders and encoder presets live in `streaming/ffmpeg/profiles.yaml`, not in code.

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
- Planned, not yet in the Makefile: `make api-client` (M2: regenerates the Orval client, which is never hand-edited, and `make ci` fails if it's stale), `make seed` (M3), `make sample-media` (M4), `make loadtest`, `make backup`, `make restore-test`, `make deploy`.

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
  - TMDB calls replay from VCR cassettes in `backend/tests/cassettes/` when `TMDB_API_KEY` is unset.
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
