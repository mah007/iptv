# Progress

| Milestone | Status | Tag |
|---|---|---|
| M1 Infrastructure & repo | Done | `m1-done` |
| M2 Backend foundation | Done | `m2-done` |
| M3 Accounts, plans, subscriptions | Done: plans, `activate()`, grace/expiry jobs, renewal property tests, invoices, payments, notifications ([ADR-0012](adr/0012-billing-subscriptions-and-notifications.md)) | `m3-done` |
| M4 Library scanner, M5 Metadata | Done ([ADR-0009](adr/0009-library-ingest-and-media-storage.md)); live TMDB on the server since 2026-10-04; the TheTVDB fallback in Q1 ([ADR-0023](adr/0023-follow-ups-tvdb-admin-portal-live-legal-ingest.md)) | `m4-done`; M5 ready for `m5-done` |
| M6 Catalog & search | Done: customer catalog API, collections, home rows, Meilisearch with the Arabic normaliser and fallback, engagement ([ADR-0013](adr/0013-customer-api.md)) | `m6-done` |
| M7 Direct-play streaming | Done ([ADR-0007](adr/0007-media-token-and-edge.md), [ADR-0010](adr/0010-renditions-transcoder-and-edge-playback.md)) | `m7-done` |
| M8 Transcoding | Done: compat MP4, HLS fMP4 ladder with per-plan ceilings, UHD/HDR, trickplay, subtitles (cp1256-safe), retention ([ADR-0014](adr/0014-hls-uhd-subtitles-thumbnails.md)); per-encoder benchmarks for CPU, NVENC, QSV and VAAPI. GPU is optional (owner decision 2026-10-16) | `m8-done` |
| M9 Xtream API | Done ([ADR-0008](adr/0008-xtream-api.md), [ADR-0011](adr/0011-xtream-catalog-playback-and-sign-in-limits.md)) | `m9-done` |
| M11 Admin UI | Done: every §8 page that has an API, billing pages, collections, storage, title media panels, live views with a polling fallback; `make e2e-admin` with axe ([ADR-0015](adr/0015-admin-ui.md)); Devices, Lockouts, subscription status, field codes and the own-password card in Q1 ([ADR-0023](adr/0023-follow-ups-tvdb-admin-portal-live-legal-ingest.md)) | ready for `m11-done` |
| M11b Customer portal | Done: portal UI with the Shaka player, progress and resume, account, devices and TV apps, checkout; `make e2e-portal` with axe ([ADR-0016](adr/0016-customer-portal.md)); live TV, my streams, my password and legal pages in Q1; provider sandboxes pending keys | — |
| M12 Live TV & EPG | Done: licensed channels and groups, XMLTV import (partitioned guide), live and EPG Xtream actions, `xmltv.php`, live M3U, `/live`, short and `/timeshift` URLs, packager and relay behind the edge, catch-up with an admin-set disk budget, ErsatzTV and MediaMTX sync, optional MediaMTX ingest, admin Live TV and EPG pages; G-11 and G-13 closed ([ADR-0017](adr/0017-live-tv-and-epg.md)); portal live TV and MediaMTX sign-ins through Django in Q1 | — |
| M13 Monitoring & logging | Done: opt-in overlay (Prometheus 3.13 LTS, Grafana, Loki, Alloy, Alertmanager, exporters), 7 dashboards, 27 alerts delivered by Django per the admin's settings with an Alertmanager fallback, admin-only Grafana through the admin's sign-in, Loki redaction tested in `make ci`, dev fire drill passed ([ADR-0018](adr/0018-observability.md)); production fire drill waits for SMTP or Telegram | — |
| CFG Dynamic configuration | Done: secret settings encrypted at rest with the `.env` fallback and its source shown, Integrations page (TMDB, TheTVDB, SMTP, Stripe, Moyasar, Telegram) with server-side Tests, System page, branding (logos, contacts, language, time zone) in both apps, emails and invoices, Alertmanager's fallback fetched from Django, installer writes settings instead of `.env` ([ADR-0019](adr/0019-dynamic-configuration.md)) | — |
| M14b Operations security & backups | Done: pgBackRest with continuous WAL archiving and restic (local, optional S3), weekly restore drill with alerts, admin Backups page, point-in-time restore and restore points before updates, personal-data retention jobs, app database role and Valkey users, socket proxies, every container capability-dropped, read-only and digest-pinned (`make hardening-test`), CrowdSec with a Traefik bouncer switched from the admin, TLS options, ufw policy, `scripts/rotate-keys.sh`, seven runbooks; G-35 to G-39 and G-41 to G-43 closed ([ADR-0020](adr/0020-operations-security-and-backups.md)) | — |
| M14a Application security | Done: strict nonce CSP on both SPAs and per-host headers, admin security keys and passkeys (WebAuthn) with an optional per-role requirement, step-up for sensitive actions (admins: code or key; customers: password before TV logins), idle timeout from `/auth/me`, admin IP allowlist, sharing detection with DB-IP Lite (scores, flags, evidence, warn/reset/suspend), per-account and progressive Xtream rate limits, *revoke by app*, payment confirmation and disputes, `make semgrep`/`audit`/`zap` in `make ci`, Renovate config, threat model v2 with a status for every gap ([ADR-0021](adr/0021-application-security.md)) | — |
| M15 Scale & CDN | Done: the Edges page and per-session selection, standalone edges (`install.sh --edge`, own Let's Encrypt TLS, control channel, stream-auth over TLS), the private S3 origin (our SigV4, generations, height-tagged rungs), Bunny token auth (verified against vectors only), the read replica with a rehearsed failover, zero-downtime updates (`scripts/rollout.sh`, `make deploy`), load tests at 100/500/1,000 (5,000 extrapolated) and a web connection pool; G-44 fixed, G-17 fixed in part ([ADR-0022](adr/0022-scale-edges-origin-replica-and-rollouts.md)) | — |
| Q1 Follow-ups | Done: TheTVDB fallback, admin Devices and Lockouts, subscription status, own passwords, my streams, portal live TV and catch-up, legal pages, MediaMTX sign-ins in the admin, capped MP4s for capped plans, the settings-cache healer ([ADR-0023](adr/0023-follow-ups-tvdb-admin-portal-live-legal-ingest.md)) | — |
| V1 Last open items | Done: visual regression in `make ci` (48 baselines), G-17's byte flagging, G-25's setup cards, optional customer two-factor sign-in, TheTVDB's DVD and absolute orders for TMDB series ([ADR-0024](adr/0024-visual-regression-byte-flagging-setup-cards-customer-2fa-tvdb-orders.md)) | — |
| I1 Import from storage | Done: admin-started imports from S3-compatible buckets, SFTP, WebDAV, Google Drive, Dropbox and OneDrive (rclone in the new `importer` service, the only one that writes the libraries) or one direct HTTPS link; space and type checks first, staged atomic moves, resume, checksums, progress, cancel, one retry, rights recorded and applied to matched titles, `make import-test` in `make ci` ([ADR-0025](adr/0025-imports-from-storage-and-direct-links.md)) | — |
| M10 client matrix (needs the owner's devices) | In progress: on an LG TV (webOS), the owner's own test (2026-10-16), Xtream sign-in and movie playback work. Still to test: series, seeking and subtitles on LG; Smarters (Android, iOS), TiviMate, XCIPTV, OTT Navigator, UHF; then the ar/en setup guides | — |


## I1: import from storage (2026-10-16)

### Done ([ADR-0025](adr/0025-imports-from-storage-and-direct-links.md))
- **Why:** the owner's films are 100+ GB each; the server now pulls them itself, at datacentre speed, from storage the owner controls or from one link the owner pastes. Every import is started by an admin for chosen files or one URL, and records who, what, from where and under which rights (rights holder and licence reference required). No torrents, indexers, search-and-download or automation.
- **Sources** (Libraries → Import → Add source; credentials Fernet-encrypted, write-only, step-up to add or change them):
  - S3-compatible: AWS, Backblaze B2, Cloudflare R2, Wasabi, MinIO, other;
  - SFTP: password or key, the host key pinned by the admin or by the first successful Test;
  - WebDAV;
  - Google Drive, Dropbox, OneDrive: the token from `rclone authorize`; OneDrive's drive found on the first Test; refreshed tokens are written back.
  
  Each has a Test (reachable, sign-in refused, not found, unreachable, timeout, certificate not trusted, not allowed) and a folder browser with sizes, both run in the importer and polled. Hosts and URLs go through the egress guard: the static check when saved, the full check with DNS before each run.
- **Engine:** rclone v1.75.2 (MIT, latest stable, pinned by the release's SHA-256), a separate process.
  - The new `importer` service: base, dev and prod compose, a Celery worker on the `import` queue only (4 threads), `backend` and `egress` networks, `cap_drop: ALL`, `no-new-privileges`, and a read-only root with a tmpfs `/tmp` in production.
  - It is the only service with the media root read-write; `make hardening-test` now fails any other.
  - Credentials reach rclone only through a per-run 0600 config in a private tmp folder: never argv, never env, never logs. Messages are scrubbed of every secret.
- **Jobs:**
  1. **Plan:** list the items and keep videos and subtitle sidecars only (other files in a folder are counted as skipped; a chosen file of another type is refused). Refuse names already in the library, and refuse a copy that with the other running imports and `library.import_reserve_gb` (10 GB) wouldn't fit. Nothing is copied first.
  2. **Copy** into `<library>/.smart-iptv-import/<job>/`: rclone with retries, checksums where the backend has them, `library.import_bandwidth_mbit`, and JSON progress (bytes, speed, ETA) every 2 s. Links use our own guarded downloader instead, with HTTP Range resume and an optional checksum.
  3. **Move** atomically (a hard link, never overwriting; subtitles first), then queue `scan_path`.
  4. **Rights:** the job's rights fill the matched title's empty, unlocked fields (`apps.imports.rights`, called from `accept_movie` and `link_episode_file`).
  
  `library.import_max_concurrent` (1, at most 2) at once; one retry on transient errors, resuming; cancel stops within seconds and deletes the staged data. Audited as `import.start`, `import.finish` and `import.cancel` (and `import.source.*`), without credentials. The sweeper (beat, every minute, in the importer) requeues the job of a stopped importer, removes orphan staging folders and beats `hb:importer`.
- **Admin:** Libraries → **Import** (`/libraries/import`): the importer's state and limits, the queue (progress, speed, ETA, Cancel), sources (Test, Browse, Edit, Delete), the history with details, and the **Direct link** dialog. In en/ar, both themes, RTL. `library.view` reads and `library.manage` changes. Three settings in Settings → Library with en/ar labels.
- **`make import-test`** (in `make ci`): a throwaway container from the dev image serves synthetic FFmpeg clips over SFTP and HTTPS (rclone serve; a throwaway CA the dev importer trusts with DEBUG only). A folder (clip, subtitle, a poster that stays behind) and a link go through the real importer into a library and are probed by the scanner. Then everything is removed. No image pulled, no internet.
- **Server:** `install.sh` (install and `--update`) gives uid 10001 write access to `MEDIA_ROOT` with an ACL (default ACL included, ownership untouched; it installs `acl` if missing and warns where ACLs aren't supported).
- **Docs:** [runbook](runbooks/import-media.md) (S3, B2, R2, Wasabi, MinIO, SFTP, WebDAV, Drive, Dropbox and OneDrive with `rclone authorize`, links, sizing, the rsync alternative, troubleshooting), README, CLAUDE.md.

### Evidence (dev stack, lane 2)
- **Backend:**
  - One full run in lane 2: 3,476 passed and 2 failed, at 92.79% coverage. The two failures were exhaustive lists the new code had to join: the declared queues (`import`) and key rotation's encrypted fields (`ImportSource.credentials_encrypted`, `ImportJob.url_encrypted`). Both were fixed, and their files then passed (20 tests).
  - `apps/imports`: 74 tests against real `rclone serve sftp|webdav|s3` on loopback and a local HTTPS server with a throwaway certificate. They cover Tests and failure codes, host-key pinning, the browser, credentials absent from argv and the environment, folder and file imports, the type, target and space checks, cancel, retry once, recovery, token write-back, OneDrive discovery (rclone's answers simulated), drop and resume with Range, checksums, the guard (loopback, metadata, internal names, an HTTP redirect, DNS to loopback), TLS, and the API (RBAC, step-up, no secrets in responses or audit, query counts). Rights at match time are covered too.
  - Ruff, `ruff format --check`, mypy and `makemigrations --check` are clean.
- **Frontend:** Vitest admin 199 passed (7 new: queue and history, offline importer, adding an S3 source with a server refusal, browse and import, a direct link, read-only, the Libraries link). ESLint, Prettier and tsc are clean, and `make api-client` regenerated (`api-client-check` up to date).
- **`make import-test`:** passed in 4 to 9 s (35 s once, when a code edit had just restarted the dev importer).
- **`make e2e-admin`:** 134 passed, 1 skipped (monitoring off).
  - `/libraries/import` passes axe in en/light and ar/dark, and has no sideways scroll at 390 px.
  - The new journey: add a WebDAV source (step-up), Test it (refused: `.invalid` never resolves, so no internet), queue a direct link, read its failure and rights in the history, then delete the source.
  - A first full run found that the fixture's step-up handler broke whenever the dialog really appeared: the apostrophe in its role-name regex broke Playwright's selector parser. It is fixed, and the journey now locks the step-up first, so the handler runs every time.
- **`make visual`:** 24 + 24 passed; no baseline changed (the Libraries and Import pages aren't among the visual pages, and the settings fixture is unchanged).
- **`make semgrep`:** clean. Its `storage_key` rule caught a serializer `source="storage_key"`; it now reads a `relative_path` property, as `MediaFile` does.
- **`make hardening-test`:** passes with the new media-writers rule, which fails a test service that mounts `/media` writable.
- **Images:** the production `smart-iptv/importer` image builds (567 MB). Trivy: no fixable HIGH or CRITICAL findings, rclone's Go binary included. rclone runs as uid 10001, and the image has no FFmpeg.
- **Installer:** ShellCheck is clean (`install.sh`, `import_test.sh`, `smoke_images.sh`). `--update --dry-run` in `ubuntu:24.04` shows the new ACL step. The ACL step itself, run in a Debian container, left existing and later root-created folders writable for uid 10001.

### For the production server
`install.sh --update` covers it:
- it builds the new `smart-iptv/importer` image (the runtime plus the 85 MB rclone binary) and starts the `importer` service (`compose up --remove-orphans` picks it up);
- migration `imports/0001` adds three new tables, backward compatible;
- the new `import` queue and the `imports-sweep` beat entry come with the code; the three `library.import_*` settings start at their defaults;
- the new `grant_media_writes` step gives uid 10001 write access to `MEDIA_ROOT` with an ACL.

No `.env` change. Optional: tune the three settings (Settings → Library); create read-only keys at the storage providers before adding sources.

### Still open
- Google Drive, Dropbox and OneDrive (including the OneDrive drive discovery) follow rclone's documented flows, but aren't verified against real accounts: this machine has none, and the tests never use the internet. The owner's first Test of such a source is the check.
- A media folder on a file system without ACLs needs its permissions for uid 10001 set by hand (the installer warns).

## V1: the last open items (2026-10-16)

### Done ([ADR-0024](adr/0024-visual-regression-byte-flagging-setup-cards-customer-2fa-tvdb-orders.md))
- **Visual regression (SPEC §15), `make visual`, in `make ci`:**
  - pages: admin dashboard, customers, a customer, a movie, live TV and settings; portal sign-in, home, a title, the player's chrome, account and live TV;
  - each in English (light) and Arabic (dark), at 1440 and 390 px: 48 baselines in `frontend/apps/*/e2e/visual/baselines/` (6.9 MB);
  - pages replay API fixtures recorded from the dev stack (`make visual RECORD=1`; signed media URLs rewritten, a 4 KB synthetic clip for the player) under a fixed clock, with animations off, lazy rows mounted, images loaded, and relative times, live durations and avatars masked; a request without a fixture fails the test;
  - it found a real bug at once: the dashboard scrolled sideways at 390 px in English (a chart's `sr-only` table ignores the 1 px width); fixed in `@smart-iptv/ui`.
- **G-17's second half (sessions served too many bytes):**
  - the edges count each token response's bytes per session (njs `bytes.js`, at the end of the request) and report them every 15 s: the local edge to `POST /internal/stream-bytes` (`X-Edge-Auth`, Redis only), a standalone edge in its control poll (`session_bytes`);
  - Django adds them to the session's live record (`apps.playback.bytes`); bytes reported after a session closed go to its row through the sweeper;
  - every 5 minutes, movie and episode sessions served `antisharing.bytes_ratio` (4.0) times what the runtime and the played rendition's bitrate explain, from `antisharing.bytes_min_mb` (500), become `ExcessBytes` rows (migration `antisharing/0002`), the score signal `excess_bytes_24h` (4 points: a flag on its own) and a table on the customer's Security tab. Never a lock;
  - both settings in the admin with en/ar labels; rows follow `privacy.antisharing_retention_days`.
- **G-25's setup card:**
  - **Print setup card** with **Include the password** on every one-time credential reveal (admin: create, add, reset; portal: Add TV app, new password);
  - the card (service, device, server, username, password if ticked, a QR code, steps, a warning) is printed alone through a print stylesheet, black on white, in the page's language and direction; "Save as PDF" in the browser's dialog makes the PDF;
  - nothing reaches the server, and there is no new dependency.
- **Customer two-factor sign-in (optional):**
  - the portal's account page sets it up (QR code and key, the first code switches it on) and turns it off, each after the password step-up (`me/mfa`, `me/mfa/confirm`);
  - sign-ins on the portal and the API host take an optional `code`: an enrolled customer gets 403 `MFA_REQUIRED` without it and 400 `MFA_INVALID` with a wrong one (counted by axes and per account, shown on Lockouts); codes work once; apps signed in before keep refreshing;
  - support's recovery: **Switch two-factor off** on the customer's Security tab (`customers.edit`, step-up, audited as `customer.mfa_reset`);
  - `e2e_portal_account` switches it off between runs and `totp_code` serves customers.
- **TheTVDB's DVD or absolute order for TMDB series:**
  - `episode_ordering` gains `tvdb_absolute` (migration `catalog/0006`); a TMDB series with a TheTVDB id may choose TMDB's order, TheTVDB's DVD order or its absolute order on the series page;
  - file numbers are read in that order and mapped through TheTVDB's episode ids to the aired numbers TMDB uses (`apps.metadata.tvdb_order`); a change places the series' files again (`replace_series_files`); files TheTVDB can't place go to review (`tvdb_unavailable`, `episode_not_found`);
  - synthetic TheTVDB fixtures for Breaking Bad's three orders.
- **Smaller:** `make lint` and `make fmt` give type-aware ESLint 2.5 GB of heap (it needs about 1.8 GB now; Node's default in the frontend container is 1.7 GB); the admin's series ordering control fits 390 px.

### Evidence (dev stack, lane 2)
- **Backend:** 3,405 tests passed at 93.31% coverage in one full run (lane 2). New suites: `test_stream_bytes` (Redis only, the database forbidden), `test_bytes` (expected bytes per kind, the check, the signal and the flag, the Security tab API, late bytes, the standalone poll, retention), `test_customer_mfa` (set up, sign in with codes on both hosts, replays, lockouts, old app sign-ins, step-up, support's reset), `test_tvdb_order` (DVD and absolute mapping, two seasons in one file, no key, matching to review, re-placement, the order rules). Ruff, `ruff format --check`, mypy and `makemigrations --check` clean.
- **Vitest:** ui 168, admin 192, portal 71 (fresh containers). ESLint, Prettier and tsc clean (`make lint`, `make typecheck`); `make api-client` regenerated.
- **`make edge-test`:** all steps passed: local disk mode 174 checks (a whole file and a range reported within 40 s with the edge's id, `X-Edge-Auth` and Host, counted once), the standalone edge 48 (a later poll carries the session's bytes).
- **`make e2e-portal`:** 8 passed: the setup card printed alone under print media; the new two-factor journey (set up with an authenticator code, sign out, a wrong code refused, sign in with a code, switch off) with axe on the set-up dialog and the code step. No CSP violation.
- **`make e2e-admin`:** 129 passed, 1 skipped (monitoring overlay off) in the full run; one fit check failed there (the series page's new ordering select was 3 px too wide at 390 px), fixed and rerun on its own. The device-reset journey prints a card without the password.
- **`make visual`:** 24 + 24 passed three runs in a row after recording.
- **`make compat`** 161 passed, **`make compat-live`** 212 passed, **`make semgrep`** clean.
- On the dev stack, after the portal journey, The Matrix's HLS session had 7.6 MB in `bytes_sent` from the edge's reports.

### For the production server
`install.sh --update` covers it:
- migrations `antisharing/0002` (a new table) and `catalog/0006` (a new choice), both backward compatible;
- the edge's new `nginx.conf` and `bytes.js` arrive with the update. Standalone edges keep working before their own `install.sh --update`; they report bytes after it.

Nothing to do by hand. Optional:
- tune `antisharing.bytes_ratio` and `antisharing.bytes_min_mb` (Settings → Anti-sharing);
- a TheTVDB key is needed for series set to TheTVDB's DVD or absolute order;
- support staff with `customers.edit` can reset customers' two-factor sign-in.

### Still open
- The visual baselines belong to this machine's Chrome (155): a Chrome update that moves text needs `make visual UPDATE=1`.
- Live HLS segments served straight from the edge aren't counted (live isn't checked against a runtime).

## Last items deployed (2026-10-16)

- **Gate:** `make ci ALLOW_DIRTY=1 BUILD_FLAGS="--pull --no-cache"` passed for the tree committed as `1cf4dae` in 1395 s. It covered:
  - the backend: 3405 tests at 93.3% coverage;
  - Vitest: ui 168, admin 192, portal 71;
  - the Xtream contract: offline 161, `compat-live` 212, IPTVnator 9;
  - the browser suites: `e2e-admin` 130 passed with 1 skipped, `e2e-portal` 8;
  - `make visual` 24 + 24;
  - Semgrep, the dependency audit, ZAP, `edge-test`, `mediamtx-test`, `restore-test`, `hardening-test`, `monitoring-test`;
  - images, Trivy and the licence gate.
- **Production:** after a `pg_dump` safety copy, `install.sh --update` rolled the web replicas with no errors, with HTTPS green on seven hosts.
  - Unsigned media gives 403, and failed Xtream sign-in returns `{"user_info":{"auth":0}}`.
  - The live contract suite gives 195 passed and 4 skipped.
- **Open items:** none in code. What remains is the owner's list under "Final state": M10 real devices, M11b payment sandboxes and the go-live checklist.

## Owner decision: GPU is optional (2026-10-16)

> "No need for a GPU option; make it optional. Online servers usually come with no GPU."

- **Default:** CPU-only (libx264 on the `transcode.cpu` queue), which is how production runs.
- **Opt-in:**
  - a GPU server adds `docker/compose.gpu-nvidia.yml` or `docker/compose.gpu-intel.yml` (deploy runbook), and the transcoder then detects the hardware and subscribes to its queue;
  - without an overlay, nothing GPU-related runs.
- **Dropped from the to-do list:** GPU ladder benchmarks and GPU real-time transcoding for live channels.
- **M8:** the per-encoder benchmarks in [benchmarks/transcode.md](benchmarks/transcode.md) (CPU, NVENC, QSV, VAAPI) satisfy its benchmark criterion, so M8 is tagged `m8-done`.

## Final state (2026-10-16)

- **Final gate:** `make ci ALLOW_DIRTY=1 BUILD_FLAGS="--pull --no-cache"` passed for the tree committed as `57a0492` in 1343 s. It covered:
  - the backend: 3369 tests at 93.3% coverage;
  - Vitest: ui 164, admin 189, portal 66;
  - the Xtream contract: offline 161, `compat-live` 212 (the 720p capped check included), IPTVnator 9;
  - the browser suites: `e2e-admin` 130 passed with 1 skipped, `e2e-portal` 7;
  - Semgrep, the dependency audit, ZAP, `edge-test`, `mediamtx-test`, `restore-test`, `hardening-test`, `monitoring-test`;
  - images, Trivy and the licence gate.
- **Tags:** `m1-done` to `m7-done`, `m9-done` and `m11-done` to `m15-done`. Not tagged:
  - M10: real-device client matrix;
  - M11b: Stripe and Moyasar sandbox checkout.
- **Production (tv.mah007.net):** after a `pg_dump` safety copy, `install.sh --update` rolled the web replicas with no errors.
  - HTTPS is green on seven hosts, and `/live`, `/privacy`, `/terms` and the legal API answer.
  - Unsigned media gives 403, and failed Xtream sign-in returns `{"user_info":{"auth":0}}`.
  - The live contract suite gives 195 passed and 4 skipped (no live channels configured).
  - Nightly backups and the weekly drill have run on their own since 2026-10-11.
- **Dev:** the dev PostgreSQL owner and redis-state/redis-cache default passwords were rotated with `scripts/rotate-keys.sh --dev`, because a builder had printed them in its transcript (dev only). Smoke and tests pass on the new values.
- **For the owner before go-live:**
  - the M10 tests on real devices;
  - a TMDB commercial licence (TheTVDB's paid tier above USD 50k revenue);
  - SMTP and alert recipients in the admin, then *Send a test alert*;
  - moving the remaining `.env` settings into the admin (`manage.py settings_report`);
  - the backup passphrase (`/root/smart-iptv-backup-passphrase.txt`) kept off the server;
  - a lawyer's review of the privacy and terms templates;
  - payment keys if cards are wanted;
  - an SSL Labs check and SPF/DKIM/DMARC for the sending domain (`docs/runbooks/security.md`);
  - a decision on the leftover dev volumes `smart-iptv_postgres-replica-data` and `smart-iptv_seaweedfs-data` (test data only).

## Q1: follow-ups (2026-10-16)

### Done ([ADR-0023](adr/0023-follow-ups-tvdb-admin-portal-live-legal-ingest.md))
- **TheTVDB fallback (closes M5):**
  - our own v4 client (`apps/metadata/tvdb/`): sign-in with key and optional PIN, token reuse and one re-sign-in on 401, search, extended series with translations, paged episodes per ordering, artworks, remote ids; the shared Redis rate limit and response cache, retries with `Retry-After`, the metric with `provider="tvdb"`;
  - synthetic offline fixtures (`apps/metadata/fixtures/tvdb/`, marked as such) and `manage.py record_tvdb_fixtures`;
  - matching: TMDB first; TheTVDB only when TMDB has no confident series. A series naming its TMDB id is matched there; otherwise it is built from TheTVDB (ids on series, seasons, episodes and people, aired order, images, genres mapped to TMDB's);
  - reviews carry each candidate's provider; resolve, search and rematch take `provider=tvdb`; the title page's ordering and TheTVDB id;
  - the credit "Metadata provided by TheTVDB" on TheTVDB-sourced titles (portal, admin) and on About.
- **Admin (closes M11):**
  - **Devices** page: every customer's devices, filters in the URL, bulk block, unblock and revoke (`devices.manage`, ≤ 500, audited);
  - **Lockouts** page and a card on the customer's Security tab: sign-in lockouts, second-factor and step-up counters, TV-login and address pauses; unlock with step-up (`lockouts.view`/`lockouts.manage`, migration `accounts/0008`; an admin's lockout needs `admins.manage`);
  - `subscription_status` with its source and end date in the customers list (filter `access`), and customers in grace on the dashboard;
  - stable codes on library, catalog and settings errors (registry validators included), translated;
  - **Sign-in and security → Password**: current password plus step-up; other sessions end; an email confirms.
- **Customer API and portal:** `me/streams` and stop (the player's "Stop it and play here" on `CONCURRENCY_LIMIT`), `me/password`, `eta_s` on `TITLE_PREPARING` with an automatic retry, one web device per browser (`siptv_wd` cookie) and the nightly retirement of web devices idle for `security.web_device_idle_days`, nullable rating values.
- **Live TV in the portal:** `live/channels` (now and next, three queries) and `live/channels/{id}/guide`; `playback/start` with `live` and `catchup`; the Live page (groups, cards, guide drawer, **Watch from the start**) and the player's live mode with grant renewal; channels a browser can't decode point to TV apps; the Live link only for plans with live TV.
- **Legal pages:** `legal.*` settings with the `text` widget (preview, template warning), en/ar templates with PDPL references and placeholders, the public `legal/{privacy|terms}` API, the Markdown subset renderer in `@smart-iptv/ui`, `/privacy` and `/terms` linked from both footers, About and the checkout.
- **MediaMTX sign-ins through Django:** `authMethod: http` in production, `/internal/mediamtx-auth` (token, actions, loopback publishing, readers from the Docker networks, 401/403, throttle, metric), `live.ingest_*` settings with the old `MEDIAMTX_*` values as fallback, the **Live ingest** Integrations card with a Test, `MEDIAMTX_AUTH_TOKEN` in `.env.example`, `make mediamtx-test` in `make ci`.
- **Capped compat MP4s by remux** (`library.capped_compat_mp4`, `auto`): `compat720.mp4`/`compat480.mp4` from the ladder's rung, AAC audio and text subtitles, for the ceilings plans use; queued after the ladder and hourly; playback, publisher, scope links, cleanup and reprocess know them (migration `media/0004`); `make compat-live` checks a 720p account's `.mp4` through to the edge.
- **Settings-cache healer:** a fingerprint beside the cached values, checked every minute; `iptv_settings_cache_heals_total`.
- **Docs:** ADR-0023, the live-tv runbook (sign-ins), CLAUDE.md (the fixture line corrected, `make mediamtx-test`, compat-live), README.

### Evidence (dev stack, lane 2)
- **Backend (lane 2):** 3,369 tests passed at 93.27% coverage in one full run; `make lint` (Ruff, `makemigrations --check`, ESLint, Prettier) and `make typecheck` (mypy, tsc) clean. New suites: the TheTVDB client and fallback, devices and lockouts, password changes, my streams and web devices, live channels and live/catch-up starts, the legal API, MediaMTX sign-ins and the ingest Test, the cache healer, capped MP4s on real ffmpeg (a 480p plan's `compat480.mp4` from the 360p rung with both subtitles) and their planning.
- **Vitest:** ui 164, admin 189, portal 66 (fresh containers); ESLint, Prettier and tsc clean (`make lint`, `make typecheck`); `make api-client-check` up to date.
- **`make compat`** 161 passed. **`make compat-live`** 212 passed, among them the 720p account's `.mp4` redirecting to `compat720.mp4` and the edge serving an MP4 (four remux jobs ran first, seconds each).
- **`make mediamtx-test`** passed in 16 s (seven checks: the read user on the API, wrong passwords refused, the publish user kept off the API and off reading, publishing with the right user, reading it back with ffprobe, callers without MediaMTX's token refused).
- **`make hardening-test`** passed (36 services with the live ingest overlay); **`make semgrep`** clean; ShellCheck clean on `scripts/mediamtx_test.sh`.
- **`make e2e-admin`:** 130 passed, 1 skipped (the monitoring overlay is off): `/devices` and `/lockouts` with axe and at 390 px, blocking and unblocking a device, a TV login paused by 30 failed sign-ins and unlocked from Lockouts, a wrong current password refused, every new setting translated.
- **`make e2e-portal`:** 7 passed: the existing journeys (the Live page, `/privacy` and `/terms` added to the axe sweep at 390 px in both languages), the Test Pattern played live for more than 5 s with the LIVE mark and no seek bar, two minutes of catch-up from the archive, a second browser refused with `CONCURRENCY_LIMIT` then **Stop it and play here**, and a password change and back. No CSP violation.

### For the production server
`install.sh --update` covers everything:
- migrations `catalog/0005`, `accounts/0008` and `media/0004` (backward compatible);
- `secrets.sh` appends `MEDIAMTX_AUTH_TOKEN`; a server with `LIVE_INGEST=1` restarts MediaMTX with HTTP sign-ins, and its encoders keep working with the `.env` users until they are moved into **Settings → Integrations → Live ingest**;
- with a 720p or 480p plan, capped MP4 remuxes for existing titles are queued by the hourly task (seconds each, CPU queue);
- browsers get the web-device cookie on their next play; old web devices retire after 30 idle days.

By hand afterwards: save a TheTVDB key in Settings → Integrations if wanted (the fallback stays off without it), have the privacy policy and terms reviewed (Settings → Legal pages), and give the support role's admins the new Lockouts permissions if a custom role should have them.

### Tags
M5 (the TheTVDB fallback) and M11 (Devices, Lockouts, subscription status, field codes, own password) meet their acceptance now: ready for `m5-done` and `m11-done` once committed.

### Still open
- Real TheTVDB responses: none recorded until a key is available (fixtures are synthetic, built from the published v4 schema).
- TheTVDB's DVD or absolute ordering for series matched on TMDB (only TheTVDB-sourced series use TheTVDB's order). Done in V1 (ADR-0024).
- A capped MP4 gets subtitles added later only when it is reprocessed.
- The legal texts are templates for a lawyer to review.
- Xtream `status_of` during grace (an ADR-0012 follow-up, outside Q1).

## M14a and M15 deployed (2026-10-14)

- **Gate:** `make ci ALLOW_DIRTY=1 BUILD_FLAGS="--pull --no-cache"` passed for the tree committed as `39e2239` in 1170 s. It covered:
  - the backend: 3248 tests at 93.5% coverage;
  - Vitest: ui 160, admin 178, portal 51;
  - the Xtream contract: offline 161, `compat-live` 210, IPTVnator 9;
  - the browser suites: `e2e-admin` 121 passed with 1 skipped (monitoring overlay off), `e2e-portal` 4;
  - Semgrep, the dependency audit, the ZAP baseline, `edge-test`, `restore-test`, `hardening-test`, `monitoring-test`;
  - images, Trivy and the licence gate.
- **Tags:** `m14-done` (both halves) and `m15-done` at `39e2239`.
- **Production:**
  - a `pg_dump` safety copy first, in its own `umask 077` subshell, then the pull under umask 022;
  - then `install.sh --update`: database roles, migrations, then a rolling replacement of the web replicas behind health checks;
  - HTTPS green on seven hosts, `edges.` included.
- **Verified on production:**
  - two web replicas as `iptv_app`, and the `local` edge row;
  - DB-IP geolocation loaded (8.8.8.8 → US, AS15169);
  - CSP with a per-response nonce on admin and app, plus COOP, HSTS, and `no-referrer` on tv;
  - `edges.` answers 401 to anything but a registered edge;
  - unsigned media gives 403, and failed Xtream sign-in returns `{"user_info":{"auth":0}}`;
  - the live contract suite gives 195 passed and 4 skipped.

## M15: scale and CDN (2026-10-14)

### Done ([ADR-0022](adr/0022-scale-edges-origin-replica-and-rollouts.md))
- **Edges (`apps.edges`):**
  - Kinds: `local` (nginx-stream; made by a migration), `standalone` and `cdn`. Each has a weight, a capacity, draining and an audience (everyone, or canary customers). Secrets are 32 bytes, stored hashed, shown once and rotated with step-up.
  - The admin's **Edges** page (`edges.view`, `edges.manage`) shows the list with live health, connections, egress, key ids, the install command and a **Test** (health, media, report, origin key). An edge outside the domain is added to the CSP's media origins.
  - Selection runs per playback session: weighted rendezvous hashing over the healthy, eligible edges, with the local edge as the fallback. Live TV always plays from the local edge.
  - Metrics and alerts: `iptv_edges_*`, `EdgeDown` and `EdgeOverCapacity`.
- **Standalone edges** (`docker/compose.edge.yml`, `install.sh --edge`):
  - the official nginx image, digest-pinned, read-only, with its own Let's Encrypt certificate (nginx's ACME module) or a certificate file;
  - the control channel (`js_periodic`, every 15 s) to `edges.<domain>`: key ids and load go up; the media token keys, the S3 read-only key and the CDN secret come down, into shared memory only;
  - stream-auth over TLS with the edge's bearer, Redis only (`edges:auth`), so kicks and heartbeats work across hosts;
  - `rotate-keys.sh media-token` waits until every edge reports the new key.
- **S3 origin** (`apps/media/s3.py`, our own SigV4 on httpx; `apps/media/publisher.py`):
  - layout `<prefix>/<asset>-g<gen>/…`, with rungs as `hls/v<n>-h<height>/` so edges enforce plan ceilings by name;
  - a server-side copy for a new generation, old generations retired after `storage.old_generation_hours`, playlists and subtitles overwritten in place (60 s at the edges);
  - the `publish` queue, a reconciler, `manage.py publish_media`;
  - separate publisher and read-only edge keys (encrypted settings), and an Integrations **Test** that fails if the edge key can write;
  - SeaweedFS as the dev origin (`make s3-dev`).
- **Bunny CDN** (`apps/edges/bunny.py`): signed path tokens scoped to a presentation's folder, with optional address binding, and the edges' `/c/` origin location gated by `X-Origin-Auth`. Verified against vectors only: Bunny's reference implementation run as an oracle, its 15 published vectors, and `make edge-test`.
- **Read replica** (`docker/compose.replica.yml`, `install.sh --replica`, `make up REPLICA=1`):
  - a hot standby by `pg_basebackup` through a slot capped at 8 GB, read as `iptv_app`;
  - `@replica.reporting` and `replica.cursor()` for charts, billing KPIs, the storage report and the minute gauges;
  - the lag measured every 15 s, with a fallback to the primary;
  - a second exporter, and a failover runbook.
- **Zero-downtime updates:**
  - web runs 2 replicas × 3 workers in production, with Traefik health checks and a drain file, and `retry@file`;
  - `scripts/rollout.sh` handles web and frontend, and recreates instead when the Traefik labels change;
  - `install.sh --update` builds, migrates once, rolls web and frontend, then the rest, and continues with a freshly pulled installer;
  - `make deploy`, `make rollout`, `make rollout-test`.
- **Load tests:**
  - `make loadtest` (Locust 2.46.6 in its own image, `manage.py loadtest_data`, a memory guard, the slot check, production gunicorn during the run);
  - the report is [benchmarks/load.md](benchmarks/load.md);
  - they led to a psycopg connection pool for web (`POSTGRES_POOL_MAX_SIZE`, 8 per process).
- **G-17:** a per-connection rate after a 16 MB burst on every edge. **G-16:** the local edge sends `X-Edge-Auth`.
- **Runbooks:** `add-edge.md`, `cdn-bunny.md` and `database-replica.md` are new. `deploy.md` (zero downtime, `make deploy`, the edges host), `alerts.md`, `backups.md`, `rollback.md`, `rotate-keys.md`, `streaming/README.md` and the threat model are updated.

### Evidence (dev stack, 2026-10-13 and 14)
- **Remote edge:** the dev standalone edge (`make edge-dev`, its own compose project) did the following:
  - polled its keys through Traefik's `edges.localhost`;
  - got stream-auth 204 on the edges host;
  - read presigned objects from SeaweedFS's private bucket, with slice cache hits;
  - gave a canary customer a playback URL on it;
  - stopped a kicked session within the 60 s auth cache (403 `kicked`).
- **S3:** SigV4 publishing to SeaweedFS (8 files, 181 MB). `check_origin` confirmed the edge key can read but not write.
- **`make edge-test`:** every step passes (now in `make ci`):
  - local disk 170 checks (CDN pulls included), live 47, S3 74;
  - the standalone edge 47 (TLS, control channel, rotation without a reload, G-17's rate, a kick across hosts, CDN pulls with ceilings);
  - Let's Encrypt through Pebble 5 (issued, HSTS, the same certificate after a restart);
  - fail-closed 9, logs 34;
  - njs 1,344 token checks plus the SigV4 and control-channel checks.
- **Bunny:** `test_bunny.py` 14 tests against the oracle's vectors. Bunny's 15 published vectors matched `bunny.token`.
- **Replica:**
  - it streamed with lag 0; 20 reporting queries ran on the replica and none on the primary;
  - **`PostgresReplicationLag` verified live:** replay paused, pending at 22:33:21, firing at 22:38:21, and the email in Mailpit at 22:38:51; then replay resumed and the lag returned to 0;
  - **failover rehearsed** on a throwaway Compose project with the same scripts: the primary was lost after 1,100 rows had replicated; promotion took 0.24 s with no rows lost; 50 writes went to the promoted node; a fail back by `pg_basebackup` kept all 1,150 rows; the replica re-seeded and streamed again.
- **Zero downtime:** `make rollout-test` sent 10,041 requests across two web rollouts with 0 failed, and 10,053 with 0 failed after the switch to `retry@file`.
  - The frontend's first roll recreated it, as designed, because its Traefik labels had changed: 136 failures during the swap.
  - The rolling path then lost 2 of 8,871 requests. The dev frontend is Vite, killed without draining; production's nginx drains on SIGQUIT.
- **The gate's other steps, lane 1:**
  - backend 3,243 passed at 93.45% coverage;
  - `make lint`, `typecheck` and `api-client-check`;
  - `make semgrep`, `make audit` (no known vulnerabilities, psycopg-pool included) and `make zap` (informational alerts only);
  - `make hardening-test` (20, 36 and 37 services, and the edge);
  - `make monitoring-test` (17/17, and 38 redaction cases);
  - `make compat-live` 210;
  - Vitest: Edges and locales.
- **E2E:**
  - `make e2e-admin`: 118 passed, 1 skipped. `/edges` passes axe in en/light and ar/dark, and the 390 px Arabic check after the fix (`grid-cols-1`, the control URL wraps). The Integrations "send a test email" journey failed once in the full run (its form reset before Save) and passed when its file was run again.
  - `make e2e-portal`: the playback journey (The Matrix through the edge) and axe passed. The M14a journey "adding a TV app asks for the password again" failed twice: `DELETE /api/v1/auth/step-up` returned 204, then `POST /me/devices` returned 201 without asking. This is outside M15's code, and is reported to the coordinator. Fixed in M14a afterwards: the step-up confirmation moved from the session into redis-state, so a request already in flight can't write the old time back; e2e-portal then passed 4/4 three runs in a row.
- **Load tests** ([benchmarks/load.md](benchmarks/load.md)):
  - 100, 500 and 1,000 users ran to the end, with 0 failures after the pool; at 1,000, play start p95 was 230 ms and the steady-state catalog p95 52 ms;
  - zero slot over-allocation at every tier;
  - 5,000 is **extrapolated**: before the pool it ran with 22% errors, and after it the memory guard stopped it at about 3,400 users on this shared 15 GB machine.
- **Installer:** ShellCheck clean (`install.sh`, `rollout.sh`). `--dry-run` passed in `ubuntu:24.04` with curl for:
  - a new installation with `--replica`;
  - `--update` with monitoring and the replica (3 web replicas);
  - `--update --no-replica`;
  - `--edge`, both installing and updating.

### For the production server
**`sudo /opt/iptv/scripts/install.sh --update`.** This first update to M15:
1. pulls the code and continues with the new installer;
2. appends `POSTGRES_REPLICATION_PASSWORD` to `.env` (`scripts/secrets.sh`), which is used only if the replica is turned on;
3. builds, runs `db-roles` and the migrations once (`edges` 0001 and 0002 with the local edge row and permissions; `media` 0003 `AssetPublication`);
4. rolls web. Its Traefik labels change in this update (health checks, `retry@file`, the edges router), so `rollout.sh` recreates web once: a short interruption. Web becomes 2 replicas × 3 workers with a connection pool. Later updates roll without downtime;
5. routes `https://edges.<domain>`; its certificate comes from the wildcard DNS already in place.

**Nothing else changes until the operator opts in:**
- the S3 origin in **Settings → Storage**;
- edges on the **Edges** page plus `install.sh --edge` on each;
- Bunny in **Settings → Integrations** plus a `cdn` edge;
- the replica with `--replica`.

## M14a: application security (2026-10-14)

### Done ([ADR-0021](adr/0021-application-security.md))
- **Headers and CSP (G-19, G-24):**
  - One policy module for both SPAs (`frontend/packages/ui/csp/policy.js`): `default-src 'none'`, scripts and styles only with a per-request nonce (nginx `$request_id` through `sub_filter` in production, a Vite plugin in development), media origins from Django at frontend start plus `security.csp_extra_media_origins`. Pages are never cached or revalidated, so a 304 can't pair an old nonce with a new header.
  - sonner patched for the nonce, `__webpack_nonce__` for the scroll lock, a nonce prop for Radix Select, zod without its `new Function` probe.
  - Django: `default-src 'none'` and Permissions-Policy on every response, Referrer-Policy per host; invoices and mail previews from their own URLs with `document_csp()`. Traefik's shared middleware keeps nosniff, frame-deny and HSTS.
  - CSP reports counted in `iptv_csp_violations_total`; ESLint bans raw HTML.
- **Admin sign-in (G-03, G-40):** security keys and passkeys (py_webauthn, @simplewebauthn/browser), `security.webauthn_required_for`; "Confirm it's you" on sensitive views (`step_up_methods`), never by password alone; 5 code attempts per sign-in and 10 per account in 15 minutes, new session keys after each step; MFA reset by owners and `manage.py reset_admin_mfa`; the idle timeout from `/auth/me`; `security.admin_ip_allowlist` with a self-lockout guard.
- **Customers (G-28 to G-31):** password step-up before TV logins are created or renewed, emails on new and reset logins; checkouts confirmed with Stripe or Moyasar before activation; disputes suspend, full refunds end.
- **Sharing detection (G-18, G-27):** `apps/antisharing`: hourly sightings per customer, login and network from sign-ins, playback starts, Xtream and stream-auth (redis-state, collected every 5 minutes); the score (ASNs, countries, impossible travel over Natural Earth centroids, networks per login), flags with evidence on the customer's Security tab and a *flagged* filter, the warn/reset/suspend ladder, admin alerts, 90-day retention.
- **IP geolocation:** DB-IP Lite country and ASN (CC BY 4.0, credited on both About pages), downloaded by the worker on first start and checked daily, validated and swapped atomically; the GeoIP card on Integrations with a lookup; `geoip.unknown_country_policy`; *GeoIpDatabaseMissing*.
- **Rate limits (G-20):** Traefik per IP on admin sign-in and CSP reports; Django per account (searches, playback starts, TV logins, checkouts, TMDB searches, backup runs; `security.rate_*`); progressive Xtream pauses per username and network, per username, per IP, with cached logins still passing.
- **Other gaps:** G-02 (resets kick sessions), G-05 (no self-escalation), G-07 (library paths), G-08 (bidi controls), G-09 (TMDB image host), G-12 (search-only Meilisearch key), G-16 (the edge's `X-Edge-Auth` secret, Docker secret `edge_auth_token`), G-26 (*revoke by app*).
- **Supply chain:** `make semgrep` (own rules with tests), `make audit` (pip-audit 2.10.1, `pnpm audit --prod`, `scripts/audit-ignore.toml`), `make zap` (ZAP 2.17 Automation Framework, passive, about 15 s), all in `make ci`; `renovate.json` (config only); ADR-0003's table updated.
- **Docs:** [threat model v2](security/threat-model.md) (a status for every G-item and a review log), the security runbook (admin sign-in and recovery, headers, rate limits, geolocation, SPF/DKIM/DMARC, the checks), *Sharing flags* and *revoke by app* in leaked-credential.md, two new alerts in alerts.md.

### Evidence (dev stack, 2026-10-14)
- **Backend (lane 2), full suite:** 3237 passed, 93.45% coverage. Two failures are M15's work in progress (`test_spec_queues_are_declared` lists no `publish` queue; `test_publish_exports_every_state_gauge` reads through the replica decorator). New tests: WebAuthn with a software authenticator, step-up (admins and customers), GeoIP with generated databases, scoring timelines, anti-sharing services, headers, throttles, bidi text, edge secret, Xtream limits, G-29 webhooks, G-12, G-07, G-09, the session-key rotation.
- **Vitest:** admin 178 (new: step-up dialog, security keys page, sharing panel, *revoke by app*, GeoIP card, About), portal 51 (password step-up, About), ui 160 (the CSP policy).
- **E2E:**
  - `make e2e-portal` 4/4 (three runs in a row), with a CSP-violation check on every page, playback included, and the password step-up journey;
  - `make e2e-admin`: 121 passed, 1 skipped (monitoring, overlay off), on the tree with M15. The new CSP check first found sonner's stale pre-bundle, zod's eval probe and the 304-after-reload nonce mismatch (all fixed). The security-key journey passes (Chrome's virtual authenticator: add a key, sign in with it in a second browser, remove it), and step-up dialogs mid-suite are answered with codes. Flaky runs led to two fixes: the admin allowlist read its setting from the event loop on the async stack right after a settings change (a 500 on `/auth/me` and `/integrations`; it now reads through `sync_to_async`), and the SMTP journey refills the form if the card reloads before Save;
  - the portal's step-up journey found that a lock could be undone by a request already in flight (`SESSION_SAVE_EVERY_REQUEST` wrote the old confirmation back): the confirmation now lives in redis-state; `e2e_portal_account` also revokes TV logins left by an interrupted run;
  - `make compat` 161, `make compat-live` 210, `make e2e-iptvnator` 9 with the new Xtream limits.
- **`make smoke`** with the new header checks per host and the admin sign-in burst (20 of 40 refused with 429); **`make semgrep`** 0 findings (5 + 2 rule tests); **`make audit`** no known vulnerabilities; **`make zap`** passes (accepted alerts listed with reasons in the plan); **`make hardening-test`** and **`make monitoring-test`** pass; `make api-client-check` current.
- **Installer:** ShellCheck clean (`install.sh`, `smoke.sh`, `smoke_images.sh`); `--dry-run --update` in `ubuntu:24.04` shows the new frontend refresh after migrations.

### For the production server
`sudo /opt/iptv/scripts/install.sh --update` does everything:
1. `scripts/secrets.sh` adds `EDGE_AUTH_TOKEN` to `.env` and writes the Docker secret `secrets/runtime/edge_auth_token` (web, the local edge and the live relay read it).
2. The rebuild brings the new frontend image (CSP), `accounts` 0007 (security keys, `auth_epoch`) and `antisharing` 0001; existing admins keep their authenticator apps.
3. After the migrations, the frontend restarts once so the pages' CSP gets the media origins from Django.
4. On its first start the worker downloads the DB-IP Lite databases into the new `geoip-data` volume (outbound HTTPS to `download.db-ip.com`); country rules and the score use them within minutes.

**Afterwards:** each owner adds a security key (user menu → **Sign-in and security**); consider `security.webauthn_required_for` and `security.admin_ip_allowlist`; set SPF, DKIM and DMARC for the mail domain ([security.md](runbooks/security.md)). If the media edge or a CDN uses a domain outside `*.<DOMAIN>`, add it to `security.csp_extra_media_origins` and run the update again (or recreate the frontend).

## M14b: operations security and backups (2026-10-11)

### Done ([ADR-0020](adr/0020-operations-security-and-backups.md))
- **Backups:**
  - **Database:** pgBackRest 2.59 (built from source in `docker/postgres.Dockerfile`), a full backup weekly and differentials nightly, continuous WAL archiving (`archive_timeout` 300 s), AES-256 and zstd.
  - **Files:** restic 0.19 for `.env`, `secrets/`, artwork, a nightly `pg_dump` and optionally media.
  - **Offsite (optional):** an S3-compatible bucket, with the keys as encrypted settings.
  - **Encryption:** one passphrase (`secrets/backup_passphrase`) for both.
  - **Settings:** schedule, retention, media, the drill and the bucket are `backups.*` settings (21 keys, en/ar).
- **The `backup` service:** a stdlib agent (`apps/ops/agent.py`) that executes the jobs Django queues: `db_full`, `db_diff`, `files`, `offsite`, `drill`, `check`. It posts to `/internal/backups/agent/*` with a bearer token. WAL archiving switches on only once the repository exists.
- **Restore drill:**
  - weekly, after the first backup, and on demand;
  - the latest backup plus WAL goes into a throwaway PostgreSQL with no listener, then migrations, row counts and `pgbackrest verify` are compared with live and judged in Django.
- **Alerts:** BackupFailed, RestoreDrillFailed, BackupStale, RestoreDrillStale, BackupAgentDown and WalArchiveFailing, as Prometheus rules (with promtool tests) and delivered by Django itself when the monitoring overlay is off. RedisStateMemoryHigh is added too.
- **Restore:**
  - the `restore` service (profile `restore`): `--target latest|<time>|name:<point>`;
  - `install.sh --update` creates `pre-update-<UTC>` restore points.
- **Admin:**
  - a **Backups** page (`backups.view` and `backups.run`): status tiles, where and when, the last drill with its checks and row counts, history, **Back up now**, **Run the restore drill** and **Test the destinations**;
  - settings groups Backups and Privacy, and CrowdSec under Security; en/ar, both themes, axe and the 390 px check included.
- **Privacy:** a daily retention job from `privacy.*` (9 keys):
  - session IPs and user agents blanked, then the sessions deleted;
  - watch history and resume points;
  - the audit log through `audit_purge` (floors of 1 and 5 years);
  - the outbox, failed admin sign-ins and webhook payloads; expired sessions.
  
  Audited as `privacy.retention`.
- **Database roles (G-37):**
  - `db-roles` one-shot over the local socket; `migrate` as the owner; the app services as `iptv_app`; `iptv_reporting` read-only without credential tables;
  - grants reapplied after every migrate;
  - `SECURITY DEFINER` functions for partitions and the audit purge; the app role is refused for `migrate`.
- **Valkey users (G-38):** `default`, `app` (dangerous commands denied) and `exporter`, from a hashed ACL file rendered at start. redis-state gets `maxmemory` 1 GB (G-35).
- **Socket proxies (G-36):** `docker-proxy` for Traefik and `docker-logs-proxy` for Alloy and CrowdSec, with regex allowlists. cAdvisor and node-exporter are documented exceptions.
- **Containers (G-39):** `cap_drop: ALL`, `no-new-privileges`, read-only roots and digest pins on every service and Dockerfile base, checked by `scripts/check_hardening.py` (`make hardening-test`, in `make ci`).
- **CrowdSec:**
  - **Engine:** an opt-in overlay (`docker/compose.crowdsec.yml`), v1.8.1-slim, reading Traefik's logs through the logs proxy, with two local scenarios (4xx flood, rate-limit abuse); the community blocklist only by opt-in.
  - **Bouncer:** the maxlerebourg plugin 1.7.1, vendored into `smart-iptv/traefik`.
  - **Admin control:** **Settings → Security** switches enforcement and the allowlist; the worker renders the middleware file, and Traefik picks it up in seconds.
  - **Commands:** `make crowdsec-test`; `cscli decisions delete --ip` to unban.
- **TLS (G-43):** TLS 1.2+, forward-secret AEAD suites, modern curves; HSTS preload as an operator switch (`DJANGO_HSTS_PRELOAD`).
- **Host (G-42):** the installer's ufw policy (`default deny incoming`, SSH rate-limited or `--ssh-from`, 80 and 443); SSH hardening in the security runbook.
- **Key rotation:** `scripts/rotate-keys.sh`:
  - `media-token [--finish]`, `secret-key [--finish]`, `field-encryption` (re-encrypts model fields and, through `apps.core.services.reencrypt_secret_settings()`, the settings' secrets);
  - `db-app|reporting|monitor|owner`, `valkey-app|exporter|default`.
  
  Each step is audited as `keys.rotate`, with the target being the kind (shown on Settings → System).
- **Installer:**
  - **New questions:** backups (folder, media, bucket), CrowdSec (default yes; blocklist default no), SSH-from.
  - **Passphrase:** shown once and kept in `/root/smart-iptv-backup-passphrase.txt`.
  - **`--update`:** a restore point, migrations via the `migrate` service as the owner; `--crowdsec` and `--no-crowdsec`.
- **Runbooks:**
  - `backups.md`, `rollback.md`, `security.md` (with "Privacy and data retention"), `rotate-keys.md`, `leaked-credential.md`, `incident-response.md`, `search-index.md`;
  - linked from `deploy.md` and the README; the threat model updated.
- **Development:** the same roles and users (dev parity). `make backup`, `make restore-test` (in `make ci`), `make hardening-test` (in `make ci`), `make crowdsec-test`. The new images (`postgres`, `postgres-backup`, `traefik`) are in `make build` and `make scan`.

### Evidence (dev stack, 2026-10-11)
- **Backend (lane 2):**
  - **Full suite:** 3014 passed at 94.55% coverage, except `test_registry`'s two exhaustive lists, since extended with the new keys (109 registry and settings tests pass).
  - **`apps/ops`:** 136 tests: the agent (with fakes for pgBackRest and restic), scheduling, the drill verdict, alerts, metrics, the API and permissions, privileges per role (denials by SQLSTATE), the Valkey ACLs against the real instances, retention, CrowdSec rendering, and key rotation (media key stage, promote and retire; re-encryption).
- **`make backup`:** a differential in 1.2 s and files in 2.0 s. The first full backup of the dev cluster (53 MB, 5.7 MB stored) took 3 s.
- **`make restore-test`:** restore 1.4 s, ready with WAL replayed 3.7 s, data up to the minute, 55 migrations, 16 tables matching live, verify ok.
- **Rotations:** every kind was run in a throwaway compose project (own networks, Traefik disabled):
  - `keys.rotate` audited per kind;
  - stored secrets readable after `field-encryption`;
  - `migrate` works after `db-owner`;
  - playback and sign-in work after each;
  - a point-in-time restore took 27 s and came back on timeline 2, with later backups and drills fine.
  
  `db-monitor` and `valkey-exporter` were not run there (no monitoring overlay); their steps share the tested code paths.
- **`make crowdsec-test`:** 200, banned 403, enforcement off 200, on 403, unbanned 200.
- **`make hardening-test`:** production alone (20 services) and with every overlay (36), plus every Dockerfile base.
- **`make monitoring-test`:** 17/17 config checks (6 new backup alerts with promtool tests), and the Alloy/Loki redaction pipeline (38 cases, 66 secrets) through the logs proxy.
- **E2E after the dev-parity switch (app services as `iptv_app` and Valkey `app`):**
  - `make compat` 161, `make compat-live` 210, `make e2e-iptvnator` 9;
  - `make e2e-admin`: the first run found the Backups page scrolling sideways at 390 px (fixed: the page grid is `grid-cols-1`); the rerun on the rebuilt images passed 116, with 1 skipped (monitoring, overlay off);
  - `make e2e-portal` 3.
- **Vitest:** Backups 5, locales.
- **Images:** `make build`, `make smoke-images` and `make licenses` pass. `make scan` passes all seven images:
  - the official PostgreSQL image's gosu (25 HIGH/CRITICAL, an old Go build) is replaced by su-exec;
  - restic is built from source with Go 1.26.9 and fixed dependencies (it had 19 HIGH as the release binary);
  - three Go 1.26.9 fixes in the Traefik binary, which no Traefik release has yet, are accepted until 2026-11-15 in `.trivyignore.yaml`.
  
  Its repository secret step currently flags `backend/.pytest_cache` (git-ignored), which holds CFG's key-shaped test ids.
- **Installer:** ShellCheck clean (also `rotate-keys.sh`, `crowdsec_test.sh`, `secrets.sh` and the entrypoints). `--dry-run` in `ubuntu:24.04` for a new installation (with a bucket, firewall and `--ssh-from`) and for `--update`. The dry run found that the SSH-port probe aborted the installer where `sshd -T` fails; fixed.

### For the production server
**Before:** the first update to this version has no backup to fall back on, so take a dump first ([rollback.md](runbooks/rollback.md#before-every-update)):
```bash
cd /opt/iptv && docker compose -f docker/compose.yml -f docker/compose.prod.yml -f docker/compose.monitoring.yml -f docker/compose.monitoring.prod.yml \
  exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > /root/pre-m14b.dump
```

**Then `sudo /opt/iptv/scripts/install.sh --update`** (add `--crowdsec` to turn CrowdSec on; without it, `CROWDSEC` stays off). In order:
1. A restore point `pre-update-<UTC>` in the running database. It isn't archived yet, because WAL archiving starts with this update.
2. `git pull --ff-only`, then `APP_COMMIT`.
3. `scripts/secrets.sh`:
   - appends the new `.env` keys (app and reporting database passwords, Valkey app and exporter passwords, `BACKUP_AGENT_TOKEN`, `CROWDSEC_BOUNCER_KEY`, `REDIS_STATE_MAXMEMORY` and the CrowdSec switches);
   - creates `secrets/backup_passphrase` and `secrets/runtime/*`;
   - with monitoring, the exporter password secrets.
4. `BACKUP_DIR=/var/backups/smart-iptv` in `.env`, and the folder is created.
5. The backup passphrase is shown once and saved to `/root/smart-iptv-backup-passphrase.txt` (mode 600). Copy it off the server.
6. `compose up -d --build --wait --remove-orphans`:
   - builds `smart-iptv/postgres` (the same `postgres:18.6-alpine` base, plus pgBackRest), `smart-iptv/postgres-backup` and `smart-iptv/traefik`, then recreates the stack;
   - PostgreSQL restarts once with the archive settings, on the same data volume;
   - both Valkeys restart once with their ACL files, taking over their old root-owned files;
   - `db-roles` creates `iptv_app` and `iptv_reporting`;
   - `migrate` runs as the owner: `ops` 0001 to 0003 (`BackupRun`, permissions, the audit trigger replacement and the `SECURITY DEFINER` functions) and CFG's migrations, then the grants;
   - the app services start as `iptv_app` and Valkey `app`;
   - Traefik reaches Docker through `docker-proxy`;
   - with monitoring, Alloy goes through `docker-logs-proxy`, and the Valkey exporter uses its own user.
   
   Customers see one restart of a few seconds, as with every update. App clients stay signed in: redis-state's AOF survives.
7. `compose run --rm --no-deps migrate` (nothing left to do), the search index, and the HTTPS checks.
8. **Within a minute,** the backup service creates the stanza, switches WAL archiving on and takes the first full backup, then a restore drill. It took 3 s on the dev cluster; at production's size, expect seconds to a minute. The admin's **Backups** page shows both.

**Afterwards:**
1. **Security checklist** ([security.md](runbooks/security.md)): SSL Labs A+, ufw (if it wasn't enabled at installation, apply the runbook's policy by hand; `--update` doesn't touch the firewall), and SSH settings.
2. Optionally an S3 bucket in **Settings → Backups**, then **Test the destinations**.
3. Delete `/root/pre-m14b.dump` once a drill has passed.


### Done ([ADR-0019](adr/0019-dynamic-configuration.md))
- **Registry:**
  - `SettingDef` gains `env` (the server-config fallback, with `env_resolve` for derived values) and `widget` (image uploads);
  - effective value: stored, else `.env`, else default, with the source (`database`, `environment`, `default`) in the API;
  - an explicitly empty value turns a `.env` value off.
- **Secrets:**
  - Fernet ciphertext in a new column (`core/0003`); the settings cache holds ciphertext only;
  - `get_secret` is the one accessor (`get_setting` refuses secrets);
  - audited as `{"configured", "source"}`, never a value;
  - `core/0004` encrypts stored rows and moves `monitoring.telegram_*` to `notifications.telegram_*`;
  - `reencrypt_secret_settings()` for M14b's key rotation.
- **API:** bulk PATCH, DELETE (reset), `adopt` (move a `.env` value in), logo upload; integrations list and Test; system facts; the public `GET /api/v1/branding`.
- **Moved into the admin, with the old variables as fallback:** SMTP (a settings-driven email backend), TMDB (the client follows a credential change without a restart), TheTVDB key and PIN, Stripe and Moyasar keys, the Telegram bot and admins' chat, the runbook URL, watcher polling, transcode attempts, retry delay and time limit.
- **New branding settings:** support phone and WhatsApp, logos, default language and time zone, used in both apps, emails, invoices and new accounts.
- **Admin:**
  - an **Integrations** page: one card each, with status, write-only secrets (Set, Replace, Clear, Move into settings, Turn off), webhook URLs and a Test per card (a test email to the signed-in admin, TMDB authentication, a TheTVDB login, the Stripe balance and webhook registration, Moyasar payments and webhooks, a Telegram getMe plus a message);
  - a **System** page (version and commit, hosts, folders, overlays, settings still in `.env`, bootstrap secrets and why, key rotations);
  - the generic Settings page shows sources, write-only secrets and logo uploads;
  - en/ar, both themes.
- **Portal:** the admin's name and logo in the header, support contacts in the footer, the service's default language on a first visit.
- **Alertmanager:** the fallback receiver fetches its settings from Django (`/internal/alert-fallback`, bearer token) every minute, keeps the last copy while web is down, and reloads when they change; M13's config checks render that case too.
- **Installer:** TMDB, SMTP, alert recipients and Telegram go into the settings with `set_setting --stdin` (nothing into `.env`). `--reconfigure` covers the domain, admin email, media folder and monitoring. `--update` writes `APP_COMMIT` and prints how many settings still come from `.env` (`manage.py settings_report`).
- **Defence in depth:** provider keys, `whsec_` secrets, bot tokens and JWTs are masked in log text; httpx's request lines are off (WARNING).

### Evidence (dev stack, 2026-10-11)
- **Backend (lane 1), the full suite:** 3014 passed at 94.55% coverage. The only failures are `test_registry`'s two exhaustive key lists, which M14b's new `backups.*` and `privacy.*` keys extend (H2 updates them).
  - The new tests cover encryption at rest (the raw row and the cache hold no plaintext), masking, precedence and sources, audit without values, logs without secrets, adopt, reset and bulk changes, key rotation, the migration helpers, pickup without a restart, every Test with a mocked transport, the integrations and system APIs, logo uploads, branding, account defaults, the watcher's polling switch, the packager's log line and the Alertmanager fallback endpoint.
- **Vitest:** admin 157 (Integrations, System, secret controls), portal 48 (branding, support contacts, first-visit language).
- **`make e2e-admin`:**
  - the Integrations journey: move the SMTP host in, a wrong port that the Test explains, then Mailpit's port, with the test email found in Mailpit's API; a Stripe key saved, never in the page or any response, then cleared;
  - axe on `/settings/integrations` and `/settings/system` in English/light and Arabic/dark, and no sideways scroll at 390 px;
  - the last full run: 102 passed, 1 skipped (monitoring, overlay off). Six pages of other areas rendered blank while services restarted, as in M12's notes, and the serial journeys after them didn't run; all 19 passed on a rerun.
- **`make e2e-portal`:** 3 passed. The journey now pins English, because a first visit speaks the service's default language (Arabic).
- **Runtime pickup on the running stack, no restarts:**
  - the **worker** sent a queued email to the port set in the admin (refused), then delivered it once the port was fixed;
  - the **packager** logged `live.limits_changed` 1 s after a change;
  - the **watcher** swapped to polling 31 s after a change;
  - **Alertmanager** fetched the settings and reloaded 49 s after the alert recipients changed.
- **Monitoring config checks:** 17/17, including the new "fallback from Django's settings".
- **Installer:** ShellCheck clean; `--dry-run` in `ubuntu:24.04` for a new installation, `--update` and `--reconfigure`.

### For the production server
`sudo /opt/iptv/scripts/install.sh --update` is all it needs:
- migrations `core/0003`, `core/0004` and `accounts/0006` run on their own;
- the TMDB token in `.env` keeps working as the fallback.

Then:
1. open **Settings → Integrations**. TMDB shows **From server config**: press **Move into settings** (then remove `TMDB_READ_ACCESS_TOKEN` from `.env`), and **Check the token**;
2. set SMTP and press **Send a test email to me**; optionally set Telegram and press **Send a test message**;
3. with monitoring on, recreate Alertmanager once (`--update` does) so it starts fetching its fallback from Django.

## M12: live TV and EPG (2026-10-11)

### Done ([ADR-0017](adr/0017-live-tv-and-epg.md))
- **Channels (`apps/live`):**
  - owned or licensed only: a rights holder is required to enable (a database constraint), and a licence end disables the channel and stops its viewers (`KickReason.LICENSE_EXPIRED`, every 5 minutes);
  - source URLs limited to http(s), rtmp(s), rtsp(s) and srt, encrypted, never serialized or logged;
  - groups are live categories (a deviation from §6, recorded in the ADR);
  - logos, up/down ordering, a probe-based source test, real-time H.264 as a capped fallback;
  - no playlist import, no bundled channels, no discovery.
- **Guide:**
  - a streaming XMLTV reader (no DOCTYPE or entities, gzip, size cap), URL sources on a cron with conditional GET, or uploads;
  - channel mapping and an unmatched view;
  - `epg_programs` partitioned monthly through the reusable `apps/core/partitions.py`, which supersedes ADR-0005's deferral for this table only.
- **Xtream:**
  - `get_live_categories`, `get_live_streams`, `get_short_epg` and `get_simple_data_table` with base64 text;
  - `xmltv.php` (gzip, cached per plan);
  - live entries first in `get.php` with `url-tvg`;
  - `/live/{u}/{p}/{id}.{ts|m3u8}` (`xtream-live`), the short `/{u}/{p}/{id}` with the channel's default output (`xtream-live-short`), and `/timeshift/{u}/{p}/{minutes}/{start}/{id}.{ts|m3u8}` (`xtream-timeshift`);
  - CORS `*` on play redirects.
- **Streaming:**
  - the `live` packager: one FFmpeg per watched channel, copy into HLS, on demand, with caps, backoff and the catch-up archive with retention under a disk budget;
  - the `live-relay`: ASGI without Django serving the continuous `.ts`, the live playlist and catch-up windows, re-asking stream-auth every 30 s;
  - edge locations and cache headers for live and archive;
  - `allow_live`, quality ceilings and live token TTLs in `start_playback`;
  - stream-auth stays Postgres-free (tested).
- **Security:**
  - **G-13** closed: the session identity includes the client's /24 or /64 for every title kind;
  - **G-11** closed: `apps/live/egress.py` refuses loopback, link-local and metadata addresses and the stack's own services after resolution and on every redirect, for source tests, guide fetches, logos, integration syncs and packager starts;
  - redaction tests for every new URL shape (`/live`, short, `/timeshift` with and without seconds);
  - `KickReason.STOPPED` for customer stops.
- **Settings:** every live and EPG knob is an admin setting with Arabic and English labels (**Settings → Live TV**), read at runtime by the packager, the archive budget (`live.archive_max_gb`, 20 GB) included. Only paths and container wiring stay in the environment. Integration credentials are in the database, encrypted and write-only in the API.
- **Integrations:**
  - ErsatzTV and MediaMTX sync creates **disabled** channels for review;
  - the optional MediaMTX ingest (RTMP, SRT, RTSP) is `docker/compose.mediamtx.yml`, which `scripts/install.sh` adds when `.env` has `LIVE_INGEST=1`. Its users are generated by `secrets.sh`, and `LIVE_INGEST_BIND` limits the published ports.
- **Admin:**
  - **Live TV** (overview, channels, groups, integrations) and **EPG** (sources, unmatched, preview) in ar/en and both themes;
  - axe and 390 px checks for both;
  - a journey that adds a channel, tests its source and removes it.
- **Dev:** `make live-ready` (the synthetic test pattern through the dev MediaMTX, a generated guide, a 1 GB archive budget, 3 minutes recorded; about a second on reruns), part of `make ci` before compat-live.
- **Docs:**
  - the [live TV runbook](runbooks/live-tv.md) (channels, MediaMTX with OBS, FFmpeg or SRT, ErsatzTV, guides, catch-up disk sizing, troubleshooting);
  - compat README, IPTVnator, M3U and XMLTV notes;
  - the threat model (G-11, G-13).

### Evidence (dev stack, 2026-10-11)
- **`make compat-live`:** 210 passed, none skipped. It plays live HLS (playlist and segment), the continuous `.ts`, the short URL and a 2-minute timeshift window through Traefik and the edge.
- **`make compat`:** 161 passed. **`make e2e-iptvnator`:** 9 passed, the live channel playing in the browser (640x360).
- **`make e2e-admin`:** 103 passed and 2 failed. Every Live TV and EPG check passed: axe in English/light and Arabic/dark, and no sideways scroll at 390 px. All 8 journeys passed, the live channel one included. The two failures were blank renders of `/monitoring` and a movie detail at 390 px while another frontend container was starting; earlier runs failed the same way whenever services were recreated or the API client regenerated mid-run.
- **Edge tests** (`streaming/tests`): the live step passes 47/47 (relay proxying, file serving, cache headers, scope checks, redaction).
- **Backend (lane 1):** the full suite at 94.6% coverage, with 2731 passed and 3 failed:
  - mine: the live overview test still expected the old environment budget; fixed, and `apps/live` plus the registry tests now pass (226);
  - not M12: `test_publish_exports_every_state_gauge` (the notification gauge) and `test_access_log_lines_would_be_redacted_too` (uvicorn's access log turned off in `apps/core/logs.py`) are M13 work in progress.
- **Static checks:** Ruff, mypy (478 files) and ESLint/Prettier are clean. `make api-client` shows no drift. Admin Vitest: 149 passed.
- **Installer:** ShellCheck clean, and `--update --dry-run` in `ubuntu:24.04` with `LIVE_INGEST=1` and `0` adds or leaves out the MediaMTX overlay. The overlay composes with the production files and binds to `LIVE_INGEST_BIND`.

### For the production server
`sudo /opt/iptv/scripts/install.sh --update` is all an existing server needs:
- it builds and starts `live` and `live-relay`, creates the `live-data` volume (whose ownership `media-init` sets), and migrates;
- no new secrets are required.

Then:
1. check **Settings → Live TV**, the catch-up budget especially (20 GB by default; see the runbook's sizing table);
2. add channels with their rights and licence fields;
3. for your own encoders, set `LIVE_INGEST=1` (and optionally `LIVE_INGEST_BIND`) in `.env` and run `--update` again.

## M13: monitoring and logging (2026-10-11)

### Done ([ADR-0018](adr/0018-observability.md))
- **The overlay, opt-in:**
  - `docker/compose.monitoring.yml` with dev and production companions: Prometheus, Grafana, Loki, Alloy, Alertmanager, node-exporter, cAdvisor, the PostgreSQL, Valkey, nginx and blackbox exporters, and the NVIDIA exporter with `GPU=nvidia`;
  - pinned by tag and digest, memory-limited and hardened;
  - turned on by `make up MONITORING=1`, the installer's question, or `install.sh --update --monitoring`.
- **Metrics:**
  - every §14 metric is observed (Xtream requests and latency, scans, match confidence and TMDB calls were defined but never recorded before);
  - the Celery workers serve their own counters and task outcomes;
  - business and state gauges every minute;
  - heartbeat ages, queue lengths and the edge capacity at scrape time;
  - edge metrics from the access log;
  - payment webhook results.
- **Logs:**
  - Traefik's access log, new, never writes the path, credentials or cookies;
  - Alloy redacts again before Loki (30 days);
  - one request id links Traefik, Django and the edge;
  - uvicorn's access log, which had come back in dev and printed raw Xtream paths, is off again;
  - playback lines carry `device_id` and `session_id`.
- **Dashboards:** Streaming Ops, Transcoding, Library & Metadata, Business, Platform, Edges, Hosts & GPU. They are generated by `monitoring/grafana/build_dashboards.py` and provisioned read-only.
- **Access:**
  - the admin's **Monitoring** page (permission `monitoring.view`) opens `grafana.<domain>` (Grafana, `/prometheus`, `/alertmanager`) with a one-time ticket and a host-only session;
  - Traefik's forward-auth asks Django about every request;
  - Grafana trusts the identity only from Traefik's fixed address.
- **Alerts and delivery:**
  - 27 rules with promtool unit tests, and a runbook heading each ([alerts.md](runbooks/alerts.md));
  - Alertmanager hands every alert to Django's internal webhook, which emails and messages it as **Settings → Monitoring** say (recipients, Telegram bot and chat, severities per channel, on/off), counted per channel;
  - Alertmanager's own email and Telegram from `.env` cover the alerts that mean Django can't deliver.
- **Admin page:** **Send a test alert** (the fire drill), and read-only values that need a restart.
- **Installer:** the monitoring question (default yes with 8 GB or more), the alert email (default: the admin's), optional Telegram (hidden input), stored in the settings with `manage.py set_setting --stdin`; `--update` keeps the choice.

### Evidence (dev stack, 2026-10-11)
- **`make monitoring-test`** (in `make ci`):
  - 16/16 config checks: promtool config, rules and unit tests for every alert; 127 dashboard queries parse; amtool on two rendered configurations; Loki, Alloy and blackbox; compose merges for dev and production;
  - the pipeline test: 38 real-shaped lines and a live Traefik, 66 fresh secrets absent from Traefik's raw output and from Loki, `request_id` metadata and the edge metrics as expected.
- **`make monitoring-smoke`:** every Prometheus target up, 27 rules loaded, Watchdog in Alertmanager. Without a session a Grafana page goes to the admin and the API answers 401, and a forged `X-WEBAUTH-USER` is refused. A ticket opens Grafana with its 7 dashboards, Prometheus and Alertmanager; signing out closes them. One request id is found in Loki from Traefik and Django (Xtream), and from Traefik and the edge (media).
- **`make monitoring-firedrill`:** the FireDrill email, sent by Django to the recipient in the settings, reached Mailpit 53 s after the drill began; the resolved one followed at 113 s.
- **Admin E2E with the overlay up:** MFA sign-in, the Monitoring page with axe in English/light and Arabic/dark, 390 px, then Grafana opened signed in and signed out again (6 passed); every setting translated (7 passed).
- **Tests:**
  - backend (lane 2): the new monitoring, alert delivery, set_setting, metrics and worker-metrics tests, plus the playback, logging, registry, webhook, scan and metadata suites they touch;
  - Vitest: the Monitoring page and the locale parity;
  - ShellCheck and three `--dry-run`s of the installer in `ubuntu:24.04`.

### For the production server
`sudo /opt/iptv/scripts/install.sh --update --monitoring` turns it on; a plain `--update` keeps it off. Then:
1. set the alert recipients and Telegram in the admin (**Settings → Monitoring**), and SMTP if it isn't set yet;
2. press **Send a test alert** on the Monitoring page;
3. record the fire drill in the go-live checklist.

**Owner decision (2026-10-03): proof of concept first.** An admin creates a customer with device credentials, media is scanned, matched and transcoded, and an IPTV app logs in and plays. Everything commercial (plans, subscriptions, billing, payments, invoices, trials, notifications) is the last slice.

## Configuration (ADR-0019) and M14b (ADR-0020) deployed (2026-10-11)

- **Gate:** `make ci ALLOW_DIRTY=1 BUILD_FLAGS="--pull --no-cache"` passed for the tree committed as `5946289` in 1004 s. It covered:
  - the backend: 3016 tests at 94.6% coverage;
  - Vitest: ui 155, admin 162, portal 48;
  - the Xtream contract: offline 161, `compat-live` 210, IPTVnator 9;
  - the browser suites: `e2e-admin` 116 passed with 1 skipped (the monitoring overlay is off in the gate), `e2e-portal` 3;
  - `restore-test`, `hardening-test` and `monitoring-test`;
  - images, Trivy and the licence gate.
- **Production:** a `pg_dump` safety copy first, then `install.sh --update`.
  - The first run stopped at `migrate`: the pull had run under `umask 077` in the operator's shell, so 207 changed files were written 600 and the image's app user couldn't read them. The API, tv and media hosts were down for about 10 minutes.
  - The fix: tracked files made readable again, and the rerun completed in about 2 minutes, with HTTPS green on all six hosts.
  - `install.sh` now pulls and clones with umask 022 and repairs unreadable tracked files (`bc36c60`).
- **Verified on production:**
  - web connects as `iptv_app`;
  - the first `db_full`, `files` and `drill` backup runs succeeded within 5 minutes of the update;
  - unsigned media gives 403, and failed Xtream sign-in returns `{"user_info":{"auth":0}}`;
  - the live contract suite gives 195 passed and 4 skipped (no live channels yet).
- **Still to do on production:**
  - move the one setting still read from `.env` (the TMDB token) into the admin;
  - set SMTP and alert recipients;
  - keep the backup passphrase (in `/root/smart-iptv-backup-passphrase.txt`) somewhere off the server.

## M12 and M13 deployed (2026-10-11)

- **Gate:** `make ci ALLOW_DIRTY=1 BUILD_FLAGS="--pull --no-cache"` passed for the tree committed as `35b857f` (only the owner's `.claude/settings.json` was left out). It covered:
  - the backend: 2734 tests at 94.6% coverage;
  - Vitest: ui 155, admin 149, portal 45;
  - the Xtream contract: offline 161, `compat-live` 210, IPTVnator 9;
  - the browser suites: `e2e-admin` 104 passed with the monitoring journey skipped (overlay off in the gate), `e2e-portal` 3;
  - `monitoring-test`;
  - images, Trivy and the licence gate.
- **Tags:** `m12-done` and `m13-done` at `35b857f`.
- **Production:** `scripts/install.sh --update --monitoring --yes` on tv.mah007.net rebuilt and started everything in 177 s, migrated, and checked HTTPS on all six hosts.
- **External checks:**
  - unsigned media gives 403, and `/internal` and `/metrics` give 404 on the tv host;
  - Grafana answers 401 without an admin session, including with a forged `X-WEBAUTH-USER`;
  - failed Xtream sign-in returns `{"user_info":{"auth":0}}`;
  - HSTS is present.
- **Live contract suite on the server:** 195 passed, 4 skipped (no live channels configured yet).
- **Still to do on production:**
  - set SMTP and alert recipients (and Telegram if wanted) in the admin, then run *Send a test alert*;
  - add licensed live channels.

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
- **M8:** tagged 2026-10-16 after the owner's decision that GPU is optional (below).
- **M11:** the global Devices and Lockouts pages need APIs (the Live TV and EPG pages arrived with M12).
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
