# Plan: the proof of concept (slices 2–4)

Status: approved by the orchestrator (owner decision 2026-10-03: POC first, commercial parts last). Builders implement it; deviations need a written reason in an ADR.

**POC goal (SPEC §0, minus billing):** an admin creates a customer with Xtream credentials. A movie or episode file is placed in a library; it is scanned, matched (TMDB, or fixtures without a key), and made playable (direct play, or one compat-MP4 transcode). The customer logs in from an IPTV app with `https://tv.<domain>`, sees Movies and Series with artwork, and plays. Concurrency limits and admin "kill session" work.

Builds on M2 (`docs/plans/m2-m3.md`, ADR-0004/0005) and M3-lite (`CustomerAccess`, `playback.entitlements`, ADR-0006), the cores P2 (`apps/library/parsing.py`, `fingerprint.py`, `apps/metadata/{scoring,images}.py`, `apps/metadata/tmdb/`, `apps/search/normalize.py`) and P3 (`apps/media/{probe,profiles,planner,ffmpeg,progress,verify,hwdetect}.py`, `streaming/ffmpeg/profiles.yaml`), and the pre-work (`streaming/` + ADR-0007 tokens and edge, `compat/` schemas and fixtures, `backend/tests/data/filenames.csv`, `scripts/sample_media.sh`). Read their code before writing yours; wire them in, don't rewrite them.

**Deferred until after the POC:**
- Meilisearch indexing and search (§7.8);
- recommendations (§7.9);
- the HLS ladder, UHD, thumbnails and subtitle extraction (§7.3; compat MP4 only for now);
- TVDB ordering;
- live TV (M12);
- the portal;
- everything commercial.

## Slice 2: library → metadata → catalog (M4, M5, M6-lite)

### Storage and services
- **Media libraries (read-only):** dev bind-mounts `./media` (git-ignored, filled by `scripts/sample_media.sh` through a new `make sample-media`) at `/media:ro`. Prod mounts `MEDIA_ROOT` (env, default `/srv/media`) at `/media:ro`. Library paths are container paths under `/media`; the admin and the API only ever show library-relative paths.
- **Data (writable):** the named volume `media-data` at `/data`, holding `images/` now and `renditions/` from slice 3.
- **New services:**
  - **`media` image stage:** the app runtime plus an FFmpeg build (jellyfin-ffmpeg or a pinned static GPL build with checksum; a separate process only, never linked). The worker, watcher and transcoder use it.
  - **`watcher`:** `python manage.py watch_libraries`, using watchdog with the 60 s stable-size rule (§7.1); it enqueues `scan_path`.
  - **`nginx-stream`:** the edge from `streaming/`. In slice 2 it serves public immutable images at `media.<DOMAIN>/images/...` from `/data/images:ro`; slice 3 adds `/v/<token>/`. Traefik routes `media.<DOMAIN>` to it (dev labels plus the prod overlay with TLS).
- **Celery queues:** `scan` (scans, probes) and `metadata` (matching, enrichment), on `worker`; `images` on `worker`.

### Models
**catalog** (POC subset of SPEC §6; every model on `BaseModel`):
- **Integer ids:** `xc_id BIGINT UNIQUE` from one Postgres sequence `catalog_xc_id_seq`, on Movie, Series and Episode.
- **Genre:** `tmdb_id`, `name_en`, `name_ar`.
- **Movie:** the §6 fields needed now:
  - identity: xc_id, tmdb_id, imdb_id;
  - titles and text: title, title_ar, original_title, alt_titles, overview, overview_ar, tagline;
  - dates and runtime: year, release_date, runtime_min;
  - ratings: rating, vote_count, certification;
  - other: original_language, trailer_youtube_key, popularity;
  - state: status `processing|review|ready|hidden|license_expired`;
  - rights: rights_holder, license_ref, license_expires_at;
  - `metadata_locked_fields`, and M2Ms `categories` and `genres`.
- **Series / Season / Episode:** Episode is unique on `(season, number)`, with `absolute_number`, `air_date` and `still`. Specials are season 0.
- **Person / Credit:** cast (top 15), director, writer.
- **MediaImage:** owner via a generic relation or a typed FK pair; kind, language, sizes as JSON of storage keys, width, height, blurhash, `is_primary`.
- **MediaFile:** owner movie or episode; multi-episode files link to several episodes via M2M. Fields:
  - library FK and `storage_key` (relative, never serialized);
  - size, mtime, xxhash64;
  - container, duration_ms, bitrate, video summary fields, hdr;
  - `parse_result`, `probe` (JSON), `match_confidence`, `is_primary`;
  - removed_at (soft remove);
  - unique `(library, storage_key)`.
- **MatchReview** as in §6.

**library:**
- **Library:** name, kind, path, processing_policy (default `ingest`), `default_categories`, `scan_interval_min` (15), enabled, last_scan_at, stats.
- **ScanJob:** trigger, status, counts, started_at, finished_at, log (redacted).

### Pipeline
1. **Scan** (`library.services.scan_library` / `scan_path`, Celery `scan` queue):
   - a Redis lock per library;
   - a walk, diffed by path, size, mtime and xxhash64 into new/changed/moved/removed (§7.1);
   - progress published to Redis channel `admin.scan.<library_id>` and stored on the ScanJob;
   - beat runs reconciliation every `scan_interval_min`.
2. **Probe** (P3 `probe`), then **parse** (P2 `parsing`), then **classify** (library kind plus the parse result; ambiguous cases go to review).
3. **Match** (P2 `scoring` plus the TMDB client):
   - a provider id gives confidence 1.0;
   - auto-accept uses the settings `metadata.match_auto_accept` and `metadata.match_margin`;
   - otherwise create a MatchReview (top 5 with breakdowns).
4. **Fixture mode:** without `TMDB_API_KEY` the client serves P2's synthetic fixtures (labelled synthetic in the admin). With a key: the real API, rate-limited and cached in redis-cache.
5. **Enrich:** details, `ar-SA` translations, genres → categories via a default mapping table (`metadata.genre_category_map` setting), the certification country setting, credits, images through P2 `images` into `/data/images` and MediaImage rows.
6. **Status:** a title becomes `ready` when it has a playable file. Slice 2 defines "playable" as compat-direct-play per P3 `planner`; slice 3 adds renditions. Until then it is `processing`.

### Admin API (`/api/v1/admin/`, RBAC: `library.view`/`library.manage`/`library.review`, audited, OpenAPI-annotated)
- `libraries`: CRUD (path validated as an existing readable directory under `/media`), `/{id}/scan`, `/{id}/scan/stream` (SSE over ASGI: `text/event-stream` from the Redis channel; heartbeats every 15 s), `scans`.
- `titles`: movies and series, list (filters: status, category, year, missing Arabic overview; ordering; select_related/prefetch with query-count tests) and detail (metadata, images, credits, files with library-relative paths, probe summary). Also PATCH for metadata fields, which records `metadata_locked_fields`, and `refresh-metadata` (respects locks).
- `review-queue`: list, `/{id}/resolve` {tmdb_id}, `/{id}/skip`, and a TMDB search proxy for manual matching.
- `categories`: CRUD and reorder.
- `make api-client` afterwards.

### Admin pages (frontend builder; generated hooks only)
- **Libraries & scans:** library cards; "Scan now" with live progress (SSE); scan history; add-library form.
- **Movies and Series:** a poster grid with search, filters and status badges, plus a list view.
- **Title detail (lite):** EN/AR metadata with locked-field icons, poster/backdrop, files with codec and quality badges, categories.
- **Review queue:** split view with the parsed filename and probe summary on one side, candidates with score bars on the other; keys 1–5 to choose, `s` to skip.
- **Categories:** list, AR/EN names, reorder.
- Build any missing kit pieces (PosterImage with blurhash, QualityBadges, ProgressBar, RelativeTime) in `packages/ui` following its conventions.

### Acceptance (orchestrator)
- `make sample-media && make up`. Creating a library over `/media/movies` and `/media/series` and scanning it finds every sample file with the right parse.
- The Matrix and Breaking Bad match automatically (fixtures), with Arabic overviews where the fixture has them. A deliberately ambiguous file lands in review, and resolving it works.
- Posters are served from `media.<DOMAIN>/images/...` with immutable caching.
- `make ci` is green, including the parser test against `filenames.csv`.

## Slice 3: playback (M7) and compat-MP4 transcoding (M8-lite)

### Playback (SPEC §3, §7.4, ADR-0007)
- **Token signer:** `playback/tokens.py` mirrors `streaming/tools/sign_token.py` byte for byte, with tests cross-checking against the njs verifier through the edge container. Keys come from secret files (`MEDIA_TOKEN_KEYS`: a current and a previous kid) that `make secrets` generates for dev and prod.
- **Concurrency:** `playback/concurrency.lua` plus a wrapper, atomic per §7.4 (prune > 90 s, refresh, add, `kick_oldest` evicts and sets `kick:<old>`). Tested against real Valkey, including a contention test with no over-allocation.
- **Sessions:** the session id is `sha256(user|device|title_ref)` with a 90 s reuse window. `PlaybackSession` rows are created at start and closed by the sweeper.
- **`playback.services.start_playback(user, device, title, rendition_pref)`:** entitlement checks in §7.4 order with stable error codes, a slot, a session and a token, returning the signed edge URL. This is used by the Xtream play URLs (slice 4) and the admin preview.
- **`/internal/stream-auth`:** an async view, Redis only (never Postgres). It validates the token (defence in depth), checks `kick:<session>`, refreshes `conc`/`sess` (TTL 120 s), adds bytes, and returns 204 or 403 with `X-Reason`.
- **Sweeper:** beat every 60 s closes sessions whose heartbeat is older than 120 s and writes `ended_at`, `bytes_sent` and `end_reason`.
- **`access_expired` signal handler:** kicks the user's sessions.
- **Edge:** `nginx-stream` gains the `/v/<token>/` locations from `streaming/` in local-disk mode over `/data/renditions` and `/media` (direct play of compatible sources), never exposing paths.
- **Admin:**
  - `sessions` list;
  - `/{id}/kill`, which sets `kick:` so playback stops on the next range or segment request, within 60 s;
  - `sessions/stream` (SSE diff every 2 s);
  - Live Sessions page (§8.3.4): table, live timers, kill.

### Transcoding (M8-lite; P3 core)
- Models `Rendition` and `TranscodeJob` per §6. Only the `compat_mp4` kind is produced for now.
- **`transcoder` service:** the `media` image, consuming `transcode.<backend>` queues from P3 `hwdetect` (`worker:caps:<host>` in Redis, TTL 60 s). The server is CPU-only. The dev GPU overlays `docker/compose.gpu-nvidia.yml` (nvidia runtime) and `docker/compose.gpu-intel.yml` (`/dev/dri`) are opt-in.
- **Flow:**
  1. plan with P3 `planner`; when the source is compat-direct-playable, mark it playable with no job;
  2. otherwise run ffmpeg with progress, persisted every 5 s and published every 1 s;
  3. verify with P3 `verify`;
  4. move atomically into `/data/renditions/<file>/compat.mp4`;
  5. retry up to 3 times with backoff and an `error_tail`;
  6. the title becomes `ready`.
- **On-demand fallback** from the planner: a remux job, or `TITLE_PREPARING` (503) with an ETA.
- **Admin:** a `transcode-jobs` list with retry, cancel and priority; the Transcode Jobs page (§8.3.10: status columns, progress, fps, speed, ETA, encoder badge).
- **Benchmark:** `docs/benchmarks/transcode.md` with real numbers for CPU here and on the server, plus NVENC/QSV on the dev machine where available.

### Acceptance
- A playback start for a ready title returns a signed URL. The edge streams it with Range (206) and refuses tampered or expired tokens.
- With `max_streams=1`, a second stream is refused (`CONCURRENCY_LIMIT`), or the oldest is kicked under `kick_oldest`.
- Admin kill stops playback within 60 s.
- The Matrix (HEVC sample) transcodes to a compat MP4 that plays in ffplay/VLC; H.264 samples direct-play.
- `make ci` is green.

## Slice 4: Xtream API (M9) and the POC demo
- `apps/xtream_api` on `tv.<DOMAIN>` (`config/urls_xtream.py`), per SPEC §7.5 and `.claude/skills/xtream-contract/SKILL.md`. It includes:
  - login payload and auth failure (identical for unknown user and wrong password, HTTP 200, constant time; Argon2 verify cached via an HMAC key in redis-state for 5 min);
  - `get_vod_categories`, `get_series_categories` and `get_live_categories` (empty for now);
  - `get_vod_streams`, `get_vod_info`, `get_series`, `get_series_info`;
  - `get.php` M3U (VOD and series);
  - `/movie/<u>/<p>/<xc_id>.<ext>` and `/series/...`, which call `start_playback` and 302 to the signed edge URL.
- Types exactly per the schemas in `compat/`. A per-entitlement-hash plus locale catalog cache in redis-cache, invalidated on catalog, category and access changes (orjson). Credential redaction in every log.
- `make ci` gains the compat contract suite against live responses (`compat/validate.py --live`).
- **POC demo:**
  - redeploy `tv.mah007.net` with sample media;
  - create a customer in the admin;
  - log in from IPTV Smarters/TiviMate (and IPTVnator per `compat/iptvnator.md`) with `https://tv.mah007.net`;
  - browse with artwork and play a movie and an episode;
  - show the concurrency refusal and the admin kill.
