# ADR-0025: Imports from the operator's storage and direct links (I1)

- **Status:** Accepted
- **Date:** 2026-10-16
- **Milestone:** I1 ("Import from storage")

## Context
The owner's films are often more than 100 GB each, and uploading them from a home connection isn't practical. The server should pull them itself, at datacentre speed. They come from storage the owner controls, or from one direct link the owner pastes (for example a public-domain film's HTTPS download on the Internet Archive).

SPEC §1.1 is unchanged and binding:
- media enters only through admin libraries;
- there are no torrents, magnet links, Usenet/NZB, indexers, scrapers, search-and-download, RSS or auto-download;
- there is no Radarr/Sonarr/Prowlarr-style automation;
- every title carries its rights.

An import is therefore always started by an admin for specific files, or for one specific URL. It records who imported what, from where and under which rights: a rights holder and a licence reference are required, and "Public domain" with the source page is a valid answer.

Other constraints:
- web, worker, watcher and transcoder mount the libraries read-only (ADR-0009, ADR-0020);
- every admin-typed URL goes through the egress guard (ADR-0017);
- credentials are encrypted and never logged (ADR-0019);
- sensitive changes need a step-up (ADR-0021).

## Decision

### 1. Sources and credentials (`apps.imports`)
- **`ImportSource`** holds a name, a kind and non-secret `options`. The secrets are Fernet JSON in `credentials_encrypted`, never serialized; the API lists only which secrets are set. Kinds:
  - **S3-compatible:** AWS, Backblaze B2 (its S3 API), Cloudflare R2, Wasabi, MinIO or another; endpoint, region, path style, access key pair;
  - **SFTP:** host, port and user, with a password and/or a private key (with a passphrase). The host key is pinned: either pasted by the admin, or pinned on the first successful Test (trust on first use, rclone's `pin_host_key`) and enforced from then on;
  - **WebDAV:** URL, vendor, user and password;
  - **Google Drive, Dropbox, OneDrive:** the token JSON `rclone authorize "<backend>"` prints on the admin's own computer, plus an optional OAuth client and, for Drive, a root folder. OneDrive's drive is found on the first Test through rclone's own non-interactive configuration (`config update … config_type onedrive`), or the admin pastes it.
- **Validation:** options are validated per kind. Hosts, endpoints and URLs get the egress guard's static check when saved and the full check, with DNS, before every run: no loopback, link-local or metadata addresses, and none of the stack's own services (`importer` joined `INTERNAL_HOSTS`). Line breaks are refused in every value, since they would inject rclone config keys.
- **Key rotation:** `key_rotation reencrypt` (`scripts/rotate-keys.sh`) covers both new encrypted fields, the source credentials and the jobs' links.
- **Writing back:** what rclone writes back is stored again, encrypted: a refreshed OAuth token (OneDrive rotates them), a pinned host key, a discovered drive.
- **Test:** lists the source's root in the importer and answers `ok`, `auth_failed`, `not_found`, `unreachable`, `timeout`, `tls_failed`, `unsafe_destination` or `unexpected`. Messages are scrubbed of every secret, and of its obscured form, before they are stored.
- **Admin API:** the Test and the folder browser are a POST (202, a request id) and a poll (`imports/requests/{id}`, answers kept 10 minutes in redis-cache). The web process never runs rclone and never blocks on the importer.

### 2. The engine: rclone in a dedicated `importer`
- **rclone:** v1.75.2, the latest stable release (2026-10-09), MIT. It is one static Go binary from the release zip, installed only after its SHA-256 (the release's SHA256SUMS) matches. Trivy finds nothing in it.
- **Images:** it ships in the new `importer` image target (runtime plus rclone, 567 MB, which shares the runtime's layers) and in the `dev` image, where its tests run real transfers. rclone always runs as a separate process.
- **Per-run config:** each run writes a private folder (0700) with an rclone config file (0600) holding the one remote `[src]`, on the importer's in-memory `/tmp` in production. Credentials never reach rclone's command line, its environment (a clean one: `PATH`, `HOME`, proxies) or a log. Passwords are obscured with `rclone obscure -`, which reads them from stdin.
- **The `importer` service** (base, dev and prod compose):
  - a Celery worker that consumes only the new `import` queue, with 4 threads; jobs are limited to `library.import_max_concurrent` (at most 2), so a Test always finds a free thread;
  - on `backend` and `egress`, with `cap_drop: ALL` and `no-new-privileges`, and a read-only root with a tmpfs `/tmp` in production;
  - the only service whose overlay mounts the media root read-write. `make hardening-test` now fails any other service that mounts `/media` writable.
- **Media write access in production.** Libraries usually belong to root on the host. `install.sh` (install and `--update`) gives uid 10001 write access with an ACL on `MEDIA_ROOT`: `setfacl -R -m u:10001:rwX,d:u:10001:rwX`, with a default ACL so folders added later by rsync are covered too. Ownership is untouched. It is idempotent, installs `acl` when it's missing, and only warns on file systems without ACLs.

### 3. Jobs
- **Who and what.** `ImportJob` records the origin (a source or a link), the selected items, or the link (encrypted, with a display form that has no query string or user info), the target library and subfolder, the rights holder, the licence reference and an optional licence end, and who asked. `import.start`, `import.finish` and `import.cancel` are audited, with names but no credentials. Source changes are audited as `import.source.*`, with the names of the secrets set or changed.
- **Dispatch.** Jobs are queued in order. `dispatch` starts at most `library.import_max_concurrent` (1 by default, at most 2) and runs on create, on finish and from the sweeper.
  - A task carries the job's dispatch number and claims the job with a conditional update. Duplicates and stale messages exit.
  - Tasks are acknowledged on receipt: a long copy would outlive the broker's visibility timeout.
  - A Redis lease (2 minutes, refreshed with progress) and `sweep_imports` (beat, every minute, in the importer) requeue the job of a stopped importer, so it resumes from its staged data, or fail it after its retry. The sweep also removes staging folders no active job owns and beats `hb:importer`, which the admin shows when it is stale.
- **The run:**
  1. **Plan.** List the items: `rclone lsjson -R --files-only` for folders, `--stat` for files. Only video containers (the scanner's list) and subtitle sidecars (`srt`, `vtt`, `ass`, `ssa`) are kept. A folder's other files and hidden files are counted as skipped; an explicitly chosen file of another type is refused, at creation and again here. Names already in the library, or two files landing on one name, are refused. The space check refuses the copy when it, plus the other running imports to the same disk and the reserve (`library.import_reserve_gb`, 10 GB by default), wouldn't fit. Nothing is copied first.
  2. **Copy** into `<library>/.smart-iptv-import/<job>/`. That is the same file system, so the move is a rename, and a hidden folder, which the scanner and the watcher already skip. rclone runs `copy --files-from-raw` with 3 retries, 10 low-level retries and `--bwlimit` from `library.import_bandwidth_mbit`. It verifies hashes where both sides share one, and skips files a previous attempt finished. JSON stats every 2 s give bytes, speed and ETA on the job row. A watchdog stops the copy if free space falls under half the reserve.
  3. **Move** each file into place, subtitles first, so a video's sidecars are there when the watcher sees it. A hard link refuses to overwrite a name taken meanwhile; where hard links aren't supported, a rename after a check; across file systems, a copy to a hidden name, then a rename. Files are 0644 and folders 0755, so every service can read them.
  4. **Scan.** Queue `scan_path` for every folder that received a video, or one library scan if there are more than 50; the watcher would do it a minute later. The existing scan, match and transcode pipeline does the rest.
- **Rights at match time.** `ImportFile` keys each placed file as `MediaFile` does (library and library-relative path). When `accept_movie` or `link_episode_file` matches it, the job's rights fill the title's empty fields. Fields the title already has, and fields an admin edited (locked), are kept. This runs before the status refresh, so a licence end in the past hides the title at once.
- **Failure and cancel.** A transient failure (unreachable, timeout, rclone's temporary error code, a connection that ends early) is retried once after a minute, resuming from the staged data. Any other failure is final, and its staging data is deleted. A queued job cancels at once; a running one stops within two seconds, when its reporter reads the flag, and its staging data is deleted.

### 4. Direct links
- **Our own downloader**, not `rclone copyurl`, because only httpx's request hooks put the egress guard on every hop, redirects included. Links must be HTTPS, and a redirect may never drop to HTTP; DEBUG allows HTTP for the dev stack only.
- **Resume:** a dropped download resumes with a `Range` request from the bytes on disk, up to 6 attempts with growing waits. A server that ignores the range makes it start again. Bytes are written as they arrive, with no compression requested, so offsets are the file's own.
- **Checks:** the name comes from the admin, the link or the server's `Content-Disposition`, and must be a video or subtitle type. An optional SHA-256, SHA-1 or MD5 is verified after the download. A certificate that doesn't verify fails at once (`tls_failed`).

### 5. Admin UI (Libraries → Import, `/libraries/import`)
- **The page:** the importer's state and limits; the queue, with progress bars, bytes, speed, ETA and Cancel; the sources, with Test, Browse, Edit and Delete; the history (who, what, from where, rights, files), with details.
- **The browser** lists one folder at a time with sizes. Only importable entries can be chosen. Next asks for the library (with its free space), the subfolder and the rights.
- **The Direct link dialog** takes a link, an optional name and an optional checksum, then the same target and rights.
- **Permissions and step-up:** `library.view` reads; `library.manage` changes sources and starts or cancels imports. Adding a source, changing its secrets, host, endpoint, URL, user or host key, or deleting it needs a step-up.
- **Quality:** en/ar, both themes and RTL; axe-clean, and no sideways scroll at 390 px. Covered by the admin E2E page list and a journey.

### 6. Tests and `make import-test`
- **Backend:** real rclone servers (`rclone serve sftp`, `webdav` and `s3` on loopback) and a local HTTPS server with a throwaway certificate cover:
  - Tests and their failure codes, host-key pinning, the browser and the request polling;
  - credentials never in argv or the environment;
  - folder and file imports with skipped types; S3 and WebDAV imports; the type, rights and target checks; the space refusal;
  - cancel, retry once, recovery after a lost importer, token write-back, OneDrive discovery;
  - links: drop and resume, checksum, refusals, the egress guard, TLS;
  - the API's RBAC, step-up, secrets and audit, and query counts;
  - rights at match time.
- **`make import-test`** (in `make ci`, about 5 s):
  - a throwaway container from the dev image joins the egress network and serves synthetic FFmpeg clips over SFTP (a generated password) and HTTPS (a throwaway CA, trusted by the dev importer through `IMPORT_EXTRA_CA_FILE`, honoured with DEBUG only);
  - `manage.py import_test` then adds a library and a source, Tests it through the importer, imports a folder (the clip and its subtitle; a poster stays behind) and a link, and waits until the scanner has probed both files;
  - it removes everything it made. The internet is never used.

## Alternatives considered
- **Uploads through the browser (resumable, tus).** This is what the owner can't do: the bottleneck is their uplink, not the protocol.
- **rclone's `rcd` daemon with its HTTP API,** instead of a process per run. That would be a long-lived process holding every source's credentials in memory, behind an API of its own to secure. A process per run with a throwaway config holds one source's credentials for one run.
- **Credentials in `RCLONE_CONFIG_SRC_*` environment variables.** They work, but rclone can't write back a refreshed OAuth token there. OneDrive rotates refresh tokens, so a source would stop working when its stored token expires.
- **`rclone copyurl` for links.** It doesn't check redirect targets against our guard, and it can't resume.
- **Running the imports in the worker,** and giving it the media root read-write. That would widen the write access of a service that runs everything else, which ADR-0020 avoids.
- **Python SDKs (boto3, paramiko, the Google client).** That means three or four dependency trees, OAuth refresh code and per-provider quirks. rclone covers them all, as one MIT binary we only execute.
- **A Docker volume for staging.** A rename from it into the library would be a copy across file systems; staging inside the library folder keeps the move atomic.
- **atmoz/sftp for the dev SFTP server.** `rclone serve sftp`, `webdav`, `s3` and `http` come with the binary already in the dev image, so no extra image is pulled.

## Consequences
- **Upgrading.** An existing server upgrades with `install.sh --update` alone:
  - the new `importer` image is built;
  - migration `imports/0001` adds three tables, backward compatible;
  - the new queue and beat entry, and three settings with their defaults;
  - the ACL on `MEDIA_ROOT`.
  
  Nothing in `.env` changes.
- **Space:** imports need free space on the library's disk for the file and the reserve. Renditions are written to the media volume afterwards, so the reserve should leave room for them where both share a disk.
- **rclone updates** are a version and checksum change in `docker/app.Dockerfile` (Renovate doesn't track it). Trivy scans the binary with the image.
- **Not verified against the real services:** Google Drive, Dropbox and OneDrive, and the OneDrive drive discovery against a real account. They follow rclone's documented flows, but this machine has no such accounts and the tests never use the internet. The owner's first Test of such a source is the check, and the runbook says what to paste if discovery fails.
- **DNS rebinding:** rclone resolves a source's host itself after our check. A name that changes its answer between the two could still reach an internal address, as with FFmpeg for live sources (ADR-0017). The importer has no secrets of other services to offer, and the stack's own names are refused outright. Direct links have no such gap: httpx checks every request it sends.
- **File systems without hard links or ACLs** (some NFS and SMB shares) still work: a rename after a check, and the warning from `install.sh`. The owner must then make the media folder writable for uid 10001 by hand.
- **No automation.** Imports never start without an admin, and the history and audit log show who started each one.
