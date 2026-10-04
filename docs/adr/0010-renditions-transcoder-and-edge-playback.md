# ADR-0010: Renditions, the transcoder and playback through the edge

- **Status:** Accepted
- **Date:** 2026-10-04
- **Milestone:** M7 (wiring) and M8-lite (POC slice 3, `docs/plans/poc.md`)

## Context
Slice 2 catalogues files. P3 built pure cores for probing, planning, ffmpeg commands, progress and verification. P4 built `start_playback` and the token edge (ADR-0007). Slice 3 has to join them, so that a matched file becomes a playable title and an IPTV app's play URL streams bytes. The hard rules still apply:
- media requests never touch Postgres;
- storage paths are never exposed;
- transcoding happens at ingest, with real time only as a capped fallback;
- GPL ffmpeg runs only as a process.

## Decision

### 1. Data (SPEC §6), in `apps.media`
- **`Rendition`:**
  - `media_file`, `kind` (`compat_mp4|source|hls_variant|hls_master|uhd`), `storage_key`, `container`, `width`, `height`, `bitrate`, `codec`, `size`, `status` (`pending|running|ready|failed`), `encoder_used`, `duration_s`, `error`, `ready_at`, and `source_hash` (the file's xxhash64 when it was made);
  - one row per file and kind (HLS variants excepted);
  - a changed source (new hash) drops its renditions and they are planned again.
- **`TranscodeJob`:**
  - `media_file`, `rendition`, `profile` (`compat_mp4`), `remux`, `backend`, `encoder`, `priority` (0-9, higher first), `status` (`queued|running|done|failed|cancelled`), `progress`, `fps`, `speed`, `eta_s`, `worker_host`, `attempts`, `error` (a stable code), `error_tail` (the last 50 stderr lines, paths redacted), `dispatches`, `started_at` and `finished_at`;
  - a partial unique constraint allows one active job per file and profile.
- **`storage_key` is the asset directory key:** the media file's UUID hex (`[A-Za-z0-9_-]{1,64}`, the token's `title` field). It is never a path and never serialized to clients.

### 2. Storage layout (the edge's local mode, ADR-0007)
```
/data/renditions/<file uuid hex>/compat.mp4      produced by a job (H.264/AAC, faststart)
/data/renditions/<file uuid hex>/source.<ext>    symlink -> /media/<library>/<file>, direct play
```
- **Direct play is a symlink.** A source that already plays everywhere gets an absolute symlink to the library file, created by the worker with `symlink(tmp)` then `rename`. That source is a faststart MP4 with H.264 the compat profile could copy and AAC (or no) audio (P3 `is_direct_playable`). Every file in a `passthrough` library gets one too.
- **The edge mounts `/media` read-only at the same path,** so nginx (`disable_symlinks off`, its default) follows the link. URLs name only `/v/<token>/source.mp4`; the library path never leaves the server.
- **A job's output is written to `.tmp-<job>/compat.mp4` in the asset folder, verified, then `rename`d into place.** Same filesystem, so the swap is atomic. The edge never serves a partial file, because tails cannot name dot-segments.

### 3. Pipeline
- **Trigger:**
  - a `post_save` receiver on `MediaFile` queues `prepare_media_file` (queue `scan`, on the worker, after the commit) whenever a file is saved `matched`. That covers automatic matches, review resolutions and rescans of linked files. The task is idempotent.
  - Beat's `reconcile_media` (every 5 min) plans matched files that have no rendition yet, and requeues running jobs whose transcoder died (no lease).
- **`prepare_file`:**
  1. probe the source;
  2. plan it (P3 `plan_processing`, 1080p ceiling);
  3. direct play or `passthrough` → a `source` rendition, ready at once, with no job;
  4. an `ingest` library → a compat job;
  5. an `on_demand` library waits for a play request.
- **Routing:**
  - remuxes (video copied) always go to `transcode.cpu`;
  - encodes go to the best backend that a live transcoder offers for H.264 (`worker:caps:*`, `best_backend` over `profiles.yaml`'s preference), else CPU;
  - priority maps to Celery's Redis priority (`9 - priority`).
  - Changing the priority of a queued job sends a new message and bumps `dispatches`. A message with an older `dispatches` value is dropped, so no message ever needs deleting.
- **`run_job` (transcoder):**
  1. take a lease in redis-state (`transcode:lease:<job>`, NX, 120 s, refreshed with progress);
  2. probe and plan again;
  3. build P3's command for the job's backend and device;
  4. run it, with progress written to the row every 5 s and published to `admin.transcode` every 1 s;
  5. verify (`expected_compat`: duration within 0.5 s, stream counts and codecs, faststart);
  6. rename the output into place;
  7. mark the rendition `ready`, then `refresh_status_of_files`, which sets the title `ready` and calls `notify_catalog_changed`, which retires the Xtream cache.
- **Failures:**
  - a failed attempt is retried after `TRANSCODE_RETRY_BACKOFF_S × 2^(attempt-1)` (60 s, then 120 s), on CPU when it failed on a GPU, up to `TRANSCODE_MAX_ATTEMPTS` (3);
  - after the last attempt the job is `failed` and its rendition `failed`.
  - A cancel sets `transcode:cancel:<job>`; the running reporter sees it within a second and kills ffmpeg.
  - A redelivered message (Redis visibility timeout) finds the job no longer `queued` and is dropped.
- **"Playable"** (`catalog.services.playable_files`) means direct-play sources, or files with a ready `compat_mp4` or `source` rendition.

### 4. The transcoder service
- **What it runs:** `manage.py run_transcoder` on the `media` image.
  1. Run P3 `hwdetect.detect`, which test-encodes every candidate encoder.
  2. Write the capabilities, including render devices, to `TRANSCODER_CAPS_FILE`.
  3. Register `worker:caps:<host>` (TTL 60 s).
  4. `exec` a Celery worker named `transcoder@<host>` on the matching `transcode.*` queues, best first. A `worker_ready` hook re-registers every 20 s and deletes the key on shutdown.
- **CPU only by default** (the server).
- **Dev GPU overlays** are opt-in (`make up GPU=nvidia`, `GPU=intel` or both, or `GPU=` in `.env`):
  - `docker/compose.gpu-nvidia.yml` uses `runtime: nvidia` with `compute,video,utility`;
  - `docker/compose.gpu-intel.yml` passes `/dev/dri` and adds the host's `render`/`video` groups (`RENDER_GID`/`VIDEO_GID`).
  - On the dev machine, NVENC, QSV, VA-API and libx264 all pass detection. Numbers are in `docs/benchmarks/transcode.md`.

### 5. Playback lookup (the contract with the Xtream app)
`apps.catalog.playable.playable_title(kind, xc_id)` and `playable_title_by_id(kind, id)` return a `PlayableTitle`, or None for an unknown id or a title that is not `ready` (for an episode, its series). The renditions are the ready `compat_mp4`/`source` rows of the title's active files, primary first. They also carry the category ids, the adult flag (any adult category), the runtime (the longest rendition, else the metadata) and the licence expiry. The lookup takes three queries at most. An episode of a ready series whose own files are still being prepared has no renditions, which `start_playback` answers with TITLE_PREPARING. Callers may then call `apps.media.services.request_on_demand(kind, id)`, which:
- makes sure an urgent job is on its way (priority 8, a remux when only the container or audio is wrong);
- returns a rough ETA.

### 6. Admin API
`/api/v1/admin/transcode-jobs`:
- a list, filtered by status, backend or file, with a constant query count;
- `{id}/retry`, `{id}/cancel` and `{id}/priority`, all audited;
- `transcode-jobs/stream`, server-sent events: a snapshot of the active jobs, then the transcoders' `job` updates.

Reading needs `library.view` or `library.manage`; acting needs `library.manage`. No new permission was needed.

### 7. Celery queues bind under their own names
Queues used to be declared as `Queue(name)`. Celery bound each of them on the `default` direct exchange with the routing key `default`, so every message reached every bound queue (each task ran once per queue). Queues are now bound under their own names on a new direct exchange, `tasks`. A new name means the brokers' stale bindings for `default` no longer route anything, with no manual broker cleanup on deploy. A test pins it.

## Alternatives considered
- **Copy (or hard-link) direct-play sources into `/data`:** doubles storage, or fails across mounts. Rejected.
- **Serving `/media` from a second edge location keyed by a path in the token:** the token would carry library paths. Rejected.
- **A per-backend worker service per GPU instead of runtime detection:** more compose services, and wrong on hosts where a driver is missing. SPEC asks for detection.
- **Deleting a queued Celery message to change its priority:** Redis lists cannot do it safely. The `dispatches` generation makes old messages harmless instead.
- **Building HLS now:** out of POC scope. The compat MP4 plays in every IPTV app.

## Consequences
- **Production overlay:** the transcoder and the edge mount `${MEDIA_ROOT}` read-only at `/media`. The transcoder writes into the `media-data` volume (`renditions/`, owned by uid 10001 through `media-init`).
- **A moved source file** (same hash, new path) keeps its renditions: the rescan saves the file, and `prepare_file` points its `source.*` symlink at the new path.
- **`on_demand` libraries:** their titles stay `processing`, so they are not listed until something plays them. Listing not-yet-ready titles is a catalogue decision for later. The POC libraries use `ingest`.
- **Not built yet:** real-time HLS (`playback.realtime_transcode_enabled`) and orphaned-rendition cleanup. Both are later milestones (M8 full).
- **Tests:** the pipeline tests run real ffmpeg on tiny generated clips. The playable lookup asserts at most 3 queries; the job list asserts a constant query count.
