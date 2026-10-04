# ADR-0014: The HLS ladder, UHD, subtitles, thumbnails and rendition retention

- **Status:** Accepted
- **Date:** 2026-10-04
- **Milestone:** M8 (on top of M8-lite, ADR-0010)

## Context
ADR-0010 made a matched file playable: a compat MP4 (or the direct-play source) per file, made by a transcoder that detects its encoders. SPEC §7.3 and §16 M8 ask for the rest of media processing:
- an adaptive HLS ladder (CMAF/fMP4, 6 s independent segments, rungs never above the source, audio groups per language, WebVTT subtitle groups, a master with CODECS, RESOLUTION, FRAME-RATE and AVERAGE-BANDWIDTH);
- a UHD version for plans with 2160p;
- trickplay sprites with `thumbs.vtt`, and a poster frame when TMDB has none;
- embedded and sidecar subtitles converted to UTF-8 WebVTT, sidecar encodings detected (Windows-1256 Arabic is common);
- tracks and the title endpoints of SPEC §10 (`admin/titles/{id}/tracks|reprocess|images|rematch`);
- retention of superseded renditions.

Several constraints are fixed:
- the token format and edge scope rule (ADR-0007) are shared by njs, Django and the test vectors. A token for rendition `R` reaches `R.<ext>` or anything under `R/`;
- a plan's `max_quality` is enforced by `start_playback`'s check 7 on the rendition heights;
- media requests never touch Postgres;
- the web process mounts neither the media volume nor the libraries in production;
- the server is CPU only (one transcoder, one job at a time).

P3 had already built the pure cores: the planner, `hls_command`, `uhd_command`, sprite, poster and subtitle commands, and HLS verification.

## Decision

### 1. One job per output
`TranscodeJob.profile` gains `hls`, `uhd`, `thumbnails` and `subtitles` beside `compat_mp4`.
- **Concurrency:** the partial unique constraint still allows one active job per file and profile, so outputs of one file run independently.
- **Runners:** each profile has a runner in `apps.media.outputs`. A runner works in `.tmp-<job>`, verifies, moves the output into place with `rename` and records its renditions. `services.run_job` keeps the lease, retries, cancels and progress of ADR-0010 for all of them.
- **Priorities** (`PROFILE_PRIORITY`, 0-9, higher first): compat 5, subtitles and thumbnails 6 (seconds of work that complete a ready title), the ladder 3 and UHD 2 (hours of work that must not hold back the next title's compat MP4).
- **Routing:**
  - the ladder goes to the best live H.264 backend;
  - a UHD encode goes to the best live HEVC backend;
  - copies, thumbnails and subtitles go to CPU.
- **What `prepare_file` queues:**
  - subtitles whenever a text track waits for conversion (every library policy);
  - thumbnails once a playable rendition exists (`queue_followups` after the compat MP4);
  - in `ingest` libraries, the ladder (`library.hls_enabled`, default on) and the UHD version (`library.uhd_enabled`, default on).
- **Failed outputs:** a failed output of the current source is not queued again by planning, only by an admin's retry or Reprocess. Before, a file whose compat MP4 always failed was re-planned every 5 minutes.

### 2. The HLS ladder: fMP4 (CMAF) segments
- **Rungs:** `profiles.yaml` holds 1080p 6000k, 720p 3000k, 540p 2000k (the default) and 360p 730k, as boxes. The source is fitted, never upscaled, and a native rung covers sources between boxes.
- **Encoding:** one ffmpeg run decodes once and `split`s into every rung, at average bitrate with a 1.07× maxrate and a fixed 2 s GOP. Every rung therefore cuts its 6 s segments at the same instants.
- **Audio:** one AAC stereo rendition per source track (named after the track or its language), plus E-AC-3 copied when the source has it.
- **Segments:** fMP4 (CMAF), `independent_segments`, VOD playlists. Every target client (hls.js, Shaka, Safari, ExoPlayer, AVPlayer) plays fMP4. It shares the init and segment format with the HEVC UHD rung, which TS cannot carry for Apple players. And the same files could later back DASH.
- **Masters:** we write them ourselves (`apps.media.hls`), not ffmpeg.
  - CODECS comes from the init segments: `avc1` from avcC, `hvc1` from hvcC (ISO/IEC 14496-15 annex E).
  - BANDWIDTH and AVERAGE-BANDWIDTH are the peak and mean segment bitrates of the rung plus the largest of its audio group.
  - Every master also carries RESOLUTION, FRAME-RATE and VIDEO-RANGE (SDR, PQ or HLG).
  - So a master can be rewritten in milliseconds (a new subtitle, another default track) without touching a segment.
- **Rows:** each rung is an `hls_variant` row (`name` `v0`…, its size, `details` with the box height, codecs and bandwidths). The ladder itself is the `hls_master` row named `hls` (the audio renditions in `details`).

### 3. Presentations, one per quality ceiling (the token stays as it is)
A token's scope is a directory, so the quality ceiling is enforced by directories:

```
<asset>/hls/master.m3u8        the full SDR ladder           token rendition `hls`
<asset>/hls720/master.m3u8     rungs whose box is <= 720p    `hls720`  (only when the ladder is taller)
<asset>/hls480/master.m3u8     rungs whose box is <= 480p    `hls480`
<asset>/hls2160/master.m3u8    the ladder plus the UHD rung  `hls2160`
<asset>/hls2160/uhd/           the UHD rung (HEVC fMP4), reachable from hls2160 only
```

- **Links:** capped and UHD presentations are folders of relative symlinks into `hls/` (`hls720/v1 -> ../hls/v1`, every `a*`). A `hls720` token cannot name a 1080p rung, because no link to one exists under its folder (404), and `hls/` is outside its scope (403). The edge follows links (`disable_symlinks off`), and links stay inside the asset.
- **Playback:** every presentation is an `hls_master` row whose `height` is its ceiling. `catalog.playable` offers them as `PlayableRendition(kind=HLS, name=…)`, and `start_playback` (unchanged check 7, `prefer=hls`) picks the tallest that the plan allows.
- **Token name:** `PlayableRendition.name` is the token's rendition when it is not the kind (`token_rendition`). The entry is `<name>/master.m3u8`.
- **Writing:** `apps.media.presentations.publish(file)` writes every master and link from the rows. It is idempotent and called after each output and after a track edit.

### 4. UHD
- **When:** for sources at least `uhd.min_source_height` (1440) tall, in `ingest` libraries.
- **Keeping the source:** a source that is already HEVC or AV1 at 25 Mbit/s or less in MP4 or MKV is kept. `uhd.<ext>` is a link to it, like `source.<ext>`, and the job only packages its video as the UHD rung (copied, `hvc1`-tagged).
- **Encoding:** otherwise the job encodes HEVC Main10 at 16.8 Mbit/s (`uhd.mp4`) and packages that.
- **HDR:** HDR stays HDR in UHD (PQ or HLG signalling is kept, `VIDEO-RANGE=PQ`). The SDR ladder and the compat MP4 are tone-mapped (ADR-0010).
- **CPU-only hosts:** an HEVC UHD encode on CPU takes many hours per film, so it is refused unless `library.uhd_cpu_encode` is on. The UHD row is then `failed` with `no_hevc_encoder`, visible to the admin, and no job is queued.
- **The progressive UHD version:** it is a separate version. `playable_title(..., uhd=True)` offers only it, and the default lookup never offers it in place of the compat MP4 that IPTV apps expect. Listing it as a separate Xtream stream is Xtream catalog work (a follow-up).

### 5. Thumbnails
- **Sprites:** one 160×90 tile every 10 s, 10×10 tiles per JPEG sheet, and `thumbs/thumbs.vtt` with `#xywh=` cues.
  - The input is the compat MP4 when there is one (SDR, 2 s GOPs), else the direct-play source.
  - Inputs of 2 minutes or more decode key frames only (`-skip_frame nokey`); shorter ones decode fully.
  - `fps=…:eof_action=pass` gives one tile per started interval, so even a short clip gets one.
- **Served under the token, not as public images.** Sprites show the film every 10 s; SPEC §12 serves media only through signed URLs; and the edge needs no new public location. Every presentation and progressive scope holds `thumbs -> ../thumbs` and `subs -> ../subs` links. So the playback URL's own token reaches `<scope>/thumbs/thumbs.vtt` and `<scope>/subs/<key>.vtt`, which the customer playback API returns (C1, ADR-0013).
- **Fallback artwork:** a movie without a TMDB poster gets the frame at 10%, cropped to 2:3. An episode without a still gets the whole frame. Both are stored through the image pipeline (WebP and AVIF, blurhash) as `source=upload`, `source_path=frame`. A TMDB picture added later becomes primary.

### 6. Subtitles and tracks
- **Models:** `AudioTrack` and `SubtitleTrack` (SPEC §6) live in `apps.media`.
  - Rows follow the probe and the sidecars (`subtitles.sync_tracks`).
  - An admin's language, title, default and forced edits survive re-probes. There is one default per kind and file.
  - Image subtitles (PGS, VobSub) are `unsupported`: burn-in candidates only.
- **Sidecars:**
  - files beside the video named after it (`Film.ar.srt`, `Film.en.forced.ass`, `Film.English.SDH.vtt`);
  - files in a `Subs/` folder beside it (named after it, or any file there when the video is alone);
  - language, forced, SDH and default are read from the name's tokens;
  - at most 4 MB each.
- **Uploads:** stored as bytes on the row. The web process has no media volume, so the subtitles job reads them from there.
- **Encodings:** valid UTF-8 is kept as is and BOMs are honoured. Otherwise charset-normalizer (MIT) tries the code pages usual for the track's language first (Arabic: cp1256, then ISO-8859-6), then detects openly. The sample Inception sidecar (cp1256, CRLF, no BOM) comes out as UTF-8 with every letter intact.
- **Conversion:** ffmpeg turns the UTF-8 text into WebVTT and SRT under `subs/<key>` (`3.eng` embedded, `x1.ara` external), with a one-segment playlist `subs/<key>.m3u8`. Every master lists the ready tracks as a `subs` group, the default flagged.
- **The compat MP4** muxes sidecars and uploads in as `mov_text` beside the embedded text tracks. IPTV apps, which play the MP4, show them. A sidecar that cannot be decoded is left out rather than failing the MP4.
- **Downloading** subtitles stays off: nothing reads `features.subtitle_download`.
- **ffsubsync** auto-retiming is not adopted. It pulls numpy, scipy and a VAD into the worker image for one feature, and an offset can be corrected by replacing the upload.

### 7. Retention and disk use
- **What is removed:** `apps.media.cleanup` deletes what no row explains:
  - **orphaned** asset folders (no such file);
  - **removed** files' folders after `library.rendition_retention_days` (default 7). Their rows go too, and subtitle tracks return to `pending`, so a file that comes back is planned from scratch;
  - **superseded** entries in a live asset: an old ladder's rungs, a UHD version no longer made, subtitles of gone tracks, presentations that no longer apply;
  - **leftovers:** `.tmp-*` of jobs that are not running, `.old-*`, `.link-*`.
- **Grace:** nothing younger than an hour is touched, so an output moved into place but not yet recorded is safe.
- **Runs:** beat runs it every 6 hours. The admin can run a dry run or a real run (`POST renditions/cleanup`, on the worker), and the last report is kept in redis-cache.
- **Disk use:** every rendition records its `size`. The admin API adds `disk_bytes`, which is 0 for links to the source, and a per-file total.

### 8. Admin API (`/api/v1/admin/`)
- **Title media:**
  - `titles/{id}/renditions` (each file's renditions, tracks and running jobs) and `DELETE …/renditions/{rendition}`;
  - `titles/{id}/reprocess`;
  - `titles/{id}/tracks` (GET, a multipart upload POST, PATCH and DELETE of a track);
  - `titles/{id}/images` (stored images and TMDB alternatives; pick, upload, primary, delete);
  - `titles/{id}/rematch` (a TMDB id, or automatic matching again).

  `{id}` is a movie's, a series' or an episode's.
- **Cleanup:** `renditions/cleanup`.
- **RBAC:** `library.view` reads, `library.manage` acts, and a rematch also accepts `library.review`. Every change is audited.
- **The media volume:** the web process never touches it. Reprocess, presentation rewrites, image storing and cleanup are Celery tasks on the worker.

### 9. Edge and dev stack
- **Edge:** no njs or token change. `EDGE_CORS_ORIGINS` now lists the portal and admin origins, so their players can fetch HLS, subtitles and thumbnails.
- **Dev transcoder:** it reloads only on `apps/media` changes (tests excluded). Edits elsewhere used to restart it, killing the running encode.
- **Dev web:** a reload waits at most 5 s for open SSE streams instead of hanging.

## Alternatives considered
- **MPEG-TS segments:** cannot carry HEVC for Apple players, and need a second packaging for a UHD rung. Rejected.
- **One master with every rung, the ceiling enforced only in the playlist:** a token for `hls/` reaches every rung, so a 720p plan could fetch 1080p by guessing a URL. Rejected.
- **A token rendition that names a list of directories, or a ceiling field in the token:** changes the token grammar shared by njs, Django and the vectors (ADR-0007), for what links already give. Rejected.
- **Separate encodes per ceiling:** multiplies encode time and storage for identical rungs. Rejected.
- **Hard links instead of symlinks:** they cannot link folders. Copies double storage. Rejected for local storage.
- **Public trickplay under `/images/`:** simpler for players, but it exposes frames of the film without a token. Rejected (§5).
- **Tracks in the catalog app:** catalog owns titles and files. The track rows are produced and consumed by the media pipeline, so they live with it.
- **Writing uploads to the media volume from the web process:** it has none in production. Rejected.
- **Auto-retrying a failed output on every plan:** it loops forever on a broken file. Rejected for an explicit retry or Reprocess.

## Consequences
- **S3 origin mode (M15):** presentations are links, and links do not exist in object storage. Publishing to S3 must upload the targets under each presentation's prefix (or the edge must map them). Sprite, subtitle and segment names are stable across a reprocess, so the slice cache must be purged on republish (ADR-0007 already says rendition objects are immutable).
- **A 720p or 480p plan:**
  - gets HLS through its capped presentation;
  - over Xtream (MP4) it still gets `QUALITY_NOT_ALLOWED` for a 1080p title, because only a 1080p compat MP4 exists.
  - A 720p compat MP4 is the follow-up if such plans are sold to IPTV-app users.
- **On the CPU-only server:** the ladder roughly doubles the per-title encode work of the compat MP4 (see docs/benchmarks/transcode.md). `library.hls_enabled` turns it off. Titles are still `ready` as soon as the compat MP4 exists, because the ladder runs at a lower priority.
- **UHD for Xtream:** a separate Xtream stream for the UHD version needs Xtream catalog changes (`playable_title(uhd=True)` is ready for them).
- **Track edits** reach the HLS masters at once and the compat MP4 at its next Reprocess.
- **Deploying M8 on an existing catalogue:** run `manage.py plan_media` once (on the worker). Beat's reconciliation only plans files without a playable rendition, so titles that are already ready get their ladder, thumbnails and subtitles only when planned again. Planning queues only missing outputs.
- **A sidecar added later** (an `.srt` dropped beside an existing film) is found on the file's next plan (a rescan of a changed file, `plan_media`, or Reprocess → subtitles). The watcher reacts to video files only.
- **Revisit:**
  - hardware tone mapping (`tonemap_opencl`/`tonemap_vaapi`) for HDR sources on GPU hosts;
  - DASH from the same fMP4 segments if a client needs it;
  - ffsubsync if mistimed sidecars become common.
