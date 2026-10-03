# ADR-0009: Library ingest, media storage and the FFmpeg image

- **Status:** Accepted
- **Date:** 2026-10-04
- **Milestone:** M4, M5, M6-lite (POC slice 2, `docs/plans/poc.md`)

## Context
Slice 2 turns files in admin libraries into a browsable catalogue: scan, probe, parse, match against TMDB (or offline fixtures), enrich in English and Arabic, store artwork, and decide each title's status. The plan fixed the shape. This record pins the details that span services, images and apps: where media and derived data live, how FFmpeg gets into the images without touching our licence posture, how the pipeline runs on Celery, how the edge serves artwork, and how catalogue writes reach the Xtream cache.

## Decision

### 1. Storage
- **Libraries are read-only folders under `/media`** (`LIBRARY_ROOT`). Dev bind-mounts `./media` (git-ignored, filled by `make sample-media`); production mounts `${MEDIA_ROOT:-/srv/media}`. `web`, `worker` and `watcher` mount it read-only. A library path must be an existing readable directory under `/media` and may not nest in or contain another library.
- **`MediaFile.storage_key` is library-relative and never serialized.** The admin API shows library-relative paths only; container paths stay internal.
- **Derived data lives on the named volume `media-data` at `/data`** (`DATA_ROOT`): `images/` now, `renditions/` from slice 3. The worker writes it; the edge mounts it read-only. A one-shot `media-init` service creates the folders and gives them to the app user (uid 10001 in production, the host user in dev), because named volumes start root-owned.

### 2. FFmpeg in the images
- **jellyfin-ffmpeg 8.1.3-1, the Debian 13 (trixie) amd64 package**, downloaded by URL in a `scratch` stage and installed only after its SHA-256 matches the pinned value. It matches the Python image's Debian, and it bundles the Intel iHD VA-API driver and the oneVPL runtime (VAAPI, QSV); NVENC comes from the NVIDIA container runtime.
- **GPL, so process boundary only.** We run `ffprobe`/`ffmpeg`/`vainfo` as subprocesses (`apps/media`), never link or import them.
- **A `media` build target** (runtime plus FFmpeg) runs `worker` and `watcher`, and later the transcoder; `web` and `beat` stay on the smaller `runtime`. The `dev` target has FFmpeg too, so the FFmpeg integration tests (`apps/media/tests/test_integration.py`) run in `make test` instead of skipping. `make build` builds `smart-iptv/media` and `make scan` (Trivy) covers it; its Python dependencies are the `app` image's, which `make licenses` gates.

### 3. Pipeline
- **Queues:** `scan` (scans, probes), `metadata` (matching, enrichment), `images` (artwork), all on `worker` (`CELERY_TASK_ROUTES`).
- **Scan** (`library.services.run_scan`): a Redis lock per library (`lock:scan:<library>`; a busy scan retries every 30 s), a walk diffed by path, size, mtime and xxhash64 into new, changed, moved (same hash at a new path) and removed (soft: `removed_at`). Progress goes to the `ScanJob` row and to the Redis channel `admin.scan.<library_id>` at most once a second; the log on the job is bounded and holds library-relative paths only.
- **Per file:** probe (P3) → parse (P2 guessit wrapper) → classify by library kind → match. A provider id in the name is confidence 1.0; otherwise P2 scoring, auto-accepted at `metadata.match_auto_accept` with `metadata.match_margin` lead, else a `MatchReview` with the top five candidates and their breakdowns.
- **Enrich once per TMDB id** under a Redis lock (`lock:tmdb:<kind>:<id>`): details, `ar-SA` translations, genres mapped to categories by the `metadata.genre_category_map` setting (categories created on first use), certification for `metadata.certification_country`, top-15 cast plus directors and writers, then artwork on the `images` queue. Admin-edited fields join `metadata_locked_fields` and survive refreshes.
- **Status:** a title is `ready` when it has an active playable file. In slice 2 "playable" means compat direct play per P3's planner (`MediaFile.direct_play`); slice 3 adds ready compat MP4 renditions. With files but none playable it is `processing`; with no files left, `hidden`; past its licence date, `license_expired`. An admin's "hidden" locks the status.
- **Watcher** (`manage.py watch_libraries`, watchdog): inotify on every enabled library, or polling with `LIBRARY_WATCHER_POLLING=true` for network shares. New or changed files are queued as `scan_path` once their size has been stable for `library.watcher_stable_s` (60 s); deletions and moves at once. It follows the library table every minute and beats `hb:watcher` for its healthcheck. Beat runs `reconcile_libraries` every minute, which starts a full scan of each library whose `scan_interval_min` has passed.
- **Fixture mode:** without `TMDB_READ_ACCESS_TOKEN` or `TMDB_API_KEY`, the TMDB client serves P2's offline fixtures, and titles carry `metadata_source = synthetic`, which the admin labels. With a credential: the real API behind a shared token bucket and a 24 h response cache in redis-cache. The `worker` alone gets the `egress` network, because the `backend` network is internal.

### 4. Artwork on the edge
- Artwork is stored as `images/{owner}/{kind}/{size}.{hash16}.{ext}` (WebP, from P2's `images`), written to a dot-file and renamed. The name changes whenever the content does.
- `nginx-stream` (the `streaming/` edge, ADR-0007) serves `media.<DOMAIN>/images/` from `/data` read-only with `Cache-Control: public, max-age=31536000, immutable`, GET/HEAD only, never dot-files. Artwork is public like TMDB's CDN; `/v/<token>/` media keeps needing a signed token. `docker/nginx-stream/50-images.sh` adds the location to the rendered edge config until slice 3 moves it into `streaming/nginx/snippets/`. Traefik routes `media.<DOMAIN>` to the edge (TLS in the production overlay).
- The API returns absolute URLs built from `MEDIA_BASE_URL` (default `<scheme>://media.<DOMAIN>[:port]`).

### 5. Catalogue identity and change notification
- **Xtream ids:** `Movie`, `Series` and `Episode` take `xc_id` from one Postgres sequence, `catalog_xc_id_seq`, so ids never collide across kinds (SPEC §7.5). Categories keep their own sequence.
- **Every catalogue write goes through `apps.catalog.signals.notify_catalog_changed`:** titles created, enriched, edited or changing status; files linked, moved or removed (even when the status stays, since a series gains or loses episodes); artwork stored; categories created, edited, reordered or deleted. After the commit it sends `catalog_changed` and calls `apps.xtream_api.cache.invalidate_on_commit()`, which retires every cached Xtream catalogue response.

### 6. Permissions
`library.review` is split: `library.view` (libraries, scans, titles, categories, review queue), `library.manage` (create and change libraries, run scans, edit titles and categories) and `library.review` (resolve or skip matches). Migration `accounts.0004` gives existing roles that held `library.review` the two new permissions, so nobody loses access.

## Alternatives considered
- **A static FFmpeg build (johnvansickle, BtbN).** Simpler to unpack, but neither bundles the Intel media driver and oneVPL runtime, so QSV/VAAPI would need distro packages that lag. jellyfin-ffmpeg is built for exactly this use and versioned per Debian release.
- **Distro `ffmpeg` (Debian 13: 7.1).** Older, and without the Jellyfin patches for hardware tone mapping we want in M8.
- **FFmpeg in every image.** Adds about 100 MB and a GPL binary to `web`, which never runs it.
- **Serving artwork through Django or presigned URLs.** Django must never serve bytes; presigning public posters adds cost and breaks CDN caching for no privacy gain.
- **A generic owner relation (contenttypes) on `MediaImage`, `Credit` and `MediaFile`.** Typed nullable FKs (one owner, enforced by check constraints on images and credits) and an episode M2M on files keep joins, `select_related` and integrity simple.
- **Separate id sequences per kind.** Xtream apps key VOD and series ids in one space in places (favourites, resume), so one sequence is safer.
- **A receiver in `apps.xtream_api` connected to `catalog_changed`.** Equivalent, but the invalidation would then depend on app loading order and on a module someone could forget to import; calling it from the one notifier is explicit.

## Consequences
- Slice 3 adds `renditions/` on the same volume, the `/v/` locations, the transcoder service on the `media` image, and extends "playable" to files with a ready compat MP4.
- The images are amd64 only (the jellyfin-ffmpeg package). An arm64 server needs the arm64 package and checksum as build args.
- Fixture mode keeps the whole pipeline testable offline; switching to a real TMDB credential re-matches nothing by itself. Refresh titles to replace synthetic metadata.
- Network shares need `LIBRARY_WATCHER_POLLING=true`; the reconciliation scan is the safety net either way.
