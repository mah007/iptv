# ADR-0022: Scale: standalone edges, the S3 origin, a CDN, a read replica and zero-downtime updates (M15)

- **Status:** Accepted
- **Date:** 2026-10-14
- **Milestone:** M15 (Scale & CDN)

## Context
SPEC §16 (M15) asks for:
- media edges on other hosts, chosen at playback start by health and load;
- renditions published to a private S3-compatible origin that those edges read;
- a CDN in front, with Bunny's token authentication;
- a PostgreSQL read replica for reporting;
- load tests from 100 to 5,000 viewers, with a report;
- updates without downtime.

What is already fixed limits the choices:
- **Media requests never touch Postgres (§1).** An edge verifies the HMAC media token (ADR-0007), then asks stream-auth, which reads Redis only, for the heartbeat and the kick check. Kicks and heartbeats must keep working when the edge is on another host.
- **One host today.** Production is one server (ADR-0020), updated with `install.sh --update`. Everything new must arrive through that path, stay off until the operator turns it on, and leave a one-server install exactly as it was.
- **Configuration in the admin (ADR-0019).** Endpoints, keys and switches are registry settings, with secrets encrypted. `.env` holds bootstrap values only.
- **Licences.** We write our own S3 client (SigV4) rather than import an SDK with a heavy dependency tree, and Bunny's token code from its documentation; nothing is copied.
- **Live TV (ADR-0017)** reads a local volume written by the packager, so it can't move to another host without a shared filesystem.

## Decision

### 1. Edges as a model, chosen per session
- **`apps.edges.Edge`.** Each edge has:
  - a `kind`: `local` (the control host's nginx-stream; one row, made by a migration), `standalone` (nginx + njs on another host) or `cdn` (a Bunny pull zone in front of one of our edges, `origin_edge`);
  - a `storage`: `disk` (the renditions volume, or a full copy of it) or `origin` (the S3 bucket);
  - `weight`, `capacity_mbps`, `enabled`, `draining`;
  - an `audience`: everyone, or the canary customers.
- **Selection** (`apps/edges/selection.py`, pure) runs at playback start, in `playback.services._open_session`, after the slot is taken. An edge is eligible when:
  - it is enabled, not draining, and its weight is above 0;
  - it is healthy:
    - the local edge passed its probe (`/healthz` and `stub_status`, every 15 s);
    - a standalone edge polled within `edges.stale_after_s` and reports the current signing key id;
    - a CDN zone has Bunny's token key set and a healthy origin edge;
  - it can serve the session: live TV only on the local edge; an origin-storage edge only for renditions already published (`published_key`);
  - its reported egress is under `edges.max_utilisation_pct` of its capacity;
  - the customer is in its audience.

  Among those, weighted rendezvous hashing on the session key picks one. A reconnect lands on the same edge, weights split new sessions, and losing an edge moves only its own sessions. With nothing eligible, the local edge answers, as before M15.
- **The snapshot.** Selection reads `edges:snapshot:v1` in redis-cache (rebuilt on every edge change, with a 2 s in-process memo), not Postgres per playback. Its status and the reports are served from there.
- **The token's title is the edge's name for the content.** It is the storage key on a disk edge and the published key (`<asset>-g<generation>`) on an origin edge. The edge serves only what that title and the token's rendition allow, as before.

### 2. A standalone edge
- **The image.** It runs the official `nginx:1.30.5-alpine` image, digest-pinned, with our configuration mounted from the checkout (`docker/compose.edge.yml`, project `smart-iptv-edge`). It is read-only, with every capability dropped but CHOWN/SETUID/SETGID.
- **TLS.** The edge has its own certificate from Let's Encrypt, through nginx's ACME module (in the official image), or from a file. HSTS is on. The HTTP-01 challenge is answered on port 80, which redirects everything else. `make edge-test` checks issuance against Pebble (Let's Encrypt's test server, its own process) and that a restart serves the kept certificate.
- **One secret on the host.** The edge's bearer secret (`secrets/edge_token`) is 32 random bytes, shown once in the admin, stored as a SHA-256 hash and rotatable. Everything else reaches the edge on its **control channel**:
  - Every 15 s, `js_periodic` posts to `https://edges.<domain>/edge/v1/poll` with `Authorization: Bearer` and `X-Edge-Id`. The edge reports its loaded key ids, its connections and its byte counter.
  - It receives the media token key set (current and previous), the origin's read-only key and the CDN origin secret.
  - It keeps them in a `js_shared_dict_zone`, in memory only. A restart fetches them again before `/healthz` turns green.
  - A key rotation reaches every edge within a poll. `scripts/rotate-keys.sh` waits (`manage.py edges_wait_keys`) until every enabled standalone edge reports the new key before it signs with it.
- **Stream-auth over TLS.** `GET https://edges.<domain>/edge/v1/stream-auth` is the internal stream-auth (`playback.views.authorize`, shared), behind a bearer check. That check also reads Redis only: `edges:auth`, a hash of edge id to secret hash in redis-state, rebuilt on every edge change and every minute. The edge sends the client's address in `X-Edge-Client-IP`, because Traefik rewrites `X-Real-IP`. The answer is cached 60 s per token, as on the local edge. So **kicks and heartbeats work across hosts** unchanged, and a kick bites within the cache window. This was verified on the dev stack with a remote edge.
- **The edges host.**
  - `edges.<domain>` is always routed, with Traefik's rate limit (300/s, burst 600). Without a valid, enabled edge's id and secret, every path answers 401 (stream-auth answers 403 with `X-Reason`).
  - It has its own URLconf (`config/urls_edge.py`): poll (Postgres, which records the report), stream-auth (Redis only) and `ping` (the installer's check).
- **Live TV stays on the control host.** The entrypoint refuses `EDGE_LIVE_ROOT` together with `EDGE_CONTROL_URL`, and selection keeps live sessions on the local edge.
- **G-17.** Every edge limits each connection to a few times its rendition's bitrate after a 16 MB burst (`EDGE_RATE_LIMIT`, `EDGE_RATE_LIMIT_UHD`, `EDGE_RATE_AFTER`), so a whole film can't be pulled at line rate.

### 3. The S3 origin
- **Layout.**
  - `<prefix>/<asset hex>-g<generation>/…`.
  - HLS rungs live once, as `hls/v<n>-h<height>/`. The height tag lets an edge enforce a capped presentation's ceiling without a copy per presentation: a token for `hls720` reaching `hls720/v1-h720/x` maps to `hls/v1-h720/x` only when 720 ≤ 720 (`s3.js` `map`).
  - Masters are rewritten to the tagged names; subtitles and thumbnails are shared.
- **Generations.**
  - An immutable change (a re-encode) publishes a new generation. Unchanged objects are copied server-side.
  - The old generation is deleted after `storage.old_generation_hours`, once no session can still use it.
  - Mutable files (`.m3u8`, `.vtt`, `.srt`, which change when a subtitle is added) are overwritten in place. Edges keep them 60 s and then revalidate.
- **Publishing.** The `publish` Celery queue publishes each finished presentation (`AssetPublication` rows, attempts, errors). A reconciler runs every 5 minutes. `manage.py publish_media --all|--status` covers the backlog and reports progress.
- **Our own SigV4 client** (`apps/media/s3.py`, on httpx):
  - header-signed requests;
  - multipart above 64 MB;
  - server-side copy, list and batch delete;
  - a rate limit (`storage.publish_mbps`).

  It is tested against shared vectors (AWS's published example included) that the edge's njs implementation also passes.
- **Two keys.**
  - The publisher's read-write key and the edges' read-only key are separate encrypted settings.
  - Edges presign every request with the read-only key (a query signature per 4 MB slice).
  - The bucket stays private (G-44).
  - The Integrations page's **Test** checks write, read and delete with the publisher's key, then read with the edges' key, and fails if the edges' key can write.
- **Edges cache in 4 MB slices** for 30 days, keyed by object and range, never by token. Every session shares one cached copy.
- **SeaweedFS** (Apache-2.0, `docker/compose.s3-dev.yml`, `make s3-dev`) is the dev origin, with real SigV4 and real per-identity permissions.

### 4. A CDN: Bunny, verified against vectors only
- **The URL.** A `cdn` edge's URL is Bunny's signed URL (`apps/edges/bunny.py`, HMAC-SHA256, the token in the path). It is scoped to the presentation's folder for HLS, so one token covers the playlists, segments and subtitles, or to the file for a progressive rendition. It has the session's expiry and, optionally, the viewer's address (`cdn.bunny_ip_binding`, off by default, because Bunny reaches viewers over IPv4 only).
- **The pull.** Bunny pulls from its origin edge's `/c/<title>/<path>`, with the origin secret (`cdn.origin_secret`) in `X-Origin-Auth`, which an edge rule on the zone adds.
  - The edge compares it in constant time, maps the path as for a token (the first folder is the rendition, so ceilings hold), and serves from the same cache entries.
  - A standalone origin edge gets the secret on its control channel. The local edge gets it from a file (`EDGE_CDN_SECRET_FILE`).
- **Verified against vectors only, with no live pull zone:**
  - Bunny's reference implementation was run once as an oracle on our URL shapes (`apps/edges/tests/data/bunny_vectors.json`; its code is not copied).
  - Bunny's own 15 published vectors match `bunny.token`.
  - The edge's `/c/` location is covered in `make edge-test`.
- **The trade-off, stated plainly.** Cache hits at Bunny never reach stream-auth. A CDN session behaves like one long progressive response:
  - its slot is taken at start and lapses after the heartbeat TTL;
  - a kick bites only at the token's expiry.

  That is why a CDN zone can be limited to canary customers, and why it is last in the cut order.

### 5. A read replica for reporting
- **The overlay.** `docker/compose.replica.yml` (`REPLICA=1`, `install.sh --replica`):
  - `replica-prepare` (uid 70, over the primary's socket) creates the physical slot `replica1`, sets `max_slot_wal_keep_size` (8 GB, so an absent replica can't fill the disk) and adds one `pg_hba` line for `iptv_replicator`. That role, with REPLICATION only, is created by `db-roles` when `POSTGRES_REPLICATION_PASSWORD` is set.
  - `postgres-replica` starts from `pg_basebackup -R` and then streams.
- **Reads.**
  - The app reads it as `iptv_app`, through the alias `replica` in `DATABASES`. Physical replication copies roles, and a standby refuses writes anyway.
  - Only code marked `@replica.reporting`, or using `replica.cursor()`, reads there: dashboard charts, billing KPIs, the storage report and the minute gauges. A contextvar router keeps everything else on the primary.
  - Writes, migrations, backups (pgBackRest archives from the primary) and the restore drill stay on the primary.
- **Lag.** It is measured every 15 s into redis-cache. Above `database.replica_max_lag_s`, or on a database error, reads fall back to the primary, so a broken replica degrades to M14's behaviour.
- **Monitoring.** A second postgres-exporter feeds the existing `PostgresReplicationLag` alert (over 30 s for 5 minutes). It was verified live by pausing replay on the dev replica.
- **Failover is manual** (`docs/runbooks/database-replica.md`): promote, repoint, re-seed. Automatic failover (Patroni) needs three nodes, which a one-server tier doesn't have.

### 6. Zero-downtime updates
- **Web runs two replicas in production** (`WEB_REPLICAS=2` × `WEB_CONCURRENCY=3` gunicorn workers: the same six workers as before, split in two).
- **Health.** Traefik routes only to healthy containers. It also checks `/internal/health/live` itself every 5 s, and retries a request whose replica refused the connection (`retry@file`, connection errors only, before any response).
- **`scripts/rollout.sh`** replaces a service's replicas:
  1. It starts N new ones next to the old (`--scale`, `--no-recreate`) and waits for Docker to report them healthy.
  2. It drains the old ones: `/tmp/web-draining` makes liveness answer 503, so Traefik drops them within seconds.
  3. It stops them gracefully (`stop_grace_period` 35 s; gunicorn and nginx finish their requests in flight) and removes them.

  A new replica that never turns healthy is removed while the old ones keep serving. When an update changes the service's Traefik labels, old and new replicas can't serve side by side (Traefik drops a router its containers define differently), so the script recreates the service instead: a short interruption, once.
- **`install.sh --update`** runs, in order:
  1. it pulls the code, then re-runs itself if the installer changed;
  2. it builds while the old version serves;
  3. it runs `db-roles` and migrations once, as the owner;
  4. it starts the replica if it is on;
  5. it rolls web, then the apps' server (`frontend`, whose CSP picks up the media origins from the updated web);
  6. it runs `up -d --wait --remove-orphans` for the rest.
- **Migrations must stay backward compatible.** Add now, remove a release later: the old web serves during the migration.
- **`make deploy DEPLOY_HOST=…`** checks that the tree is committed and pushed, then runs the update on the server over SSH.
- **Proof.** `make rollout-test` sends a steady load (Xtream catalog, APIs, the SPAs, sign-in POSTs) while web rolls twice, and fails on any failed request. It measured 10,041 requests with 0 failed.

### 7. Load tests, and a connection pool they called for
- **`make loadtest`.** Locust in its own pinned image, local network only, IPTV apps signing in, browsing, starting films and reading from the edge, plus devices racing for slots. The driver adds a memory guard and checks the slots every 10 s. It switches web to production gunicorn for the run and puts it back afterwards. The report is `docs/benchmarks/load.md`.
- **What they found.** Under ASGI each concurrent request has its own thread and its own PostgreSQL connection, so the 1,000-user sign-in burst used up the 100 connections (HTTP 500), and 5,000 collapsed.
- **The fix.** Web pools its connections: psycopg's pool, `POSTGRES_POOL_MAX_SIZE` = 8 per process in production, 48 at most for 2 × 3 workers. Requests beyond that wait up to 10 s.
  - At 1,000 users, failures fell from 45 to 0 and the play-start p95 from 840 to 230 ms.
  - Celery and the tests keep plain connections.
  - `psycopg-pool` is LGPL-3.0, used unmodified like psycopg.

### 8. The installer
- **`install.sh --edge`** installs a standalone edge.
  - It asks for the control URL, the edge id and the edge's domain, and for the secret as hidden input (or `EDGE_TOKEN`).
  - It checks them with `GET /edge/v1/ping`, which returns the storage, the origin URL and the CORS origins.
  - It checks DNS and ports, writes `.env` and `secrets/edge_token` (mode 600), starts the edge and waits for its certificate.
  - `--update` and `--reconfigure` work on an edge too. An update recreates the edge only when its configuration changed.
- **`--replica` / `--no-replica`** switch the read replica on or off. Turning it off drops the replication slot.
- **The checks.** The edges host is in the HTTPS checks (401 means it is up).

## Alternatives considered
- **An S3 SDK (boto3, aioboto3).** Large dependency trees for six calls. We already need SigV4 in njs, and one set of vectors tests both implementations.
- **Edges pulling keys over SSH, or a shared file.** Either would put a long-lived secret with more power on every edge, and rotation would need orchestration. The control channel uses the edge's own revocable secret and reaches every edge within 15 s.
- **Per-request stream-auth from Bunny (token-auth with a callback).** Bunny can't call an authorisation endpoint per request. Edge Rules with a shared header are its documented origin protection.
- **The rsync mirror as the default for standalone edges.** It needs SSH between hosts and a full copy of the library per edge. It stays documented (`storage=disk`, `EDGE_MODE=local`) for operators without a bucket.
- **Patroni or repmgr for the replica.** Automatic failover needs a quorum (three nodes), and the small tier has one. A manual promote with a tested runbook is honest for this tier.
- **Kubernetes-style rolling updates (Swarm, a deploy tool).** A second orchestrator for one host. Compose's `--scale` plus Traefik's health checks do the job with what is already there.

## Consequences
- **What an existing server needs:** `install.sh --update`. It applies the `edges` and `media` migrations, appends `POSTGRES_REPLICATION_PASSWORD` to `.env` (`scripts/secrets.sh`), and routes `edges.<domain>` (its certificate comes from the wildcard DNS already in place). It also runs web as 2 replicas × 3 workers. The first update to M15 changes web's Traefik labels, so it recreates web once (a short interruption). Every later update is rolling.
- **Everything else is opt-in:** S3 publishing (Settings → Storage), standalone edges (Edges page plus `install.sh --edge`), Bunny (Settings → Integrations plus a `cdn` edge) and the replica (`--replica`).
- **What becomes harder:**
  - Every new media path must work on both kinds of storage and through `/c/`.
  - Every schema change must be backward compatible for one release.
  - Every new reporting query should say whether it may read the replica.
- **To revisit:**
  - live TV on standalone edges, which needs the packager's output in the origin (LL-HLS to S3);
  - a second Bunny-like provider;
  - automatic failover when a second database host exists;
  - the CDN's slot lapse, if CDN zones carry more than canary traffic.
