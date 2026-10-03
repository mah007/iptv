# ADR-0002: Foundations: custom user and UUIDv7 keys, host routing, network split

- **Status:** Accepted
- **Date:** 2026-10-03
- **Milestone:** M1

## Context
Three decisions are cheap on day one and expensive later:
1. Django's user model cannot be swapped after the first migration without rebuilding the database, and SPEC §6 wants UUIDv7 keys on every table.
2. One Django project serves several hosts (`api.`, `tv.`, internal-only endpoints) that must never leak into each other. In particular, `/internal/stream-auth` (M7) must be unreachable from the internet.
3. Data stores must not be reachable from the edge or the internet.

## Decision
1. **`accounts.User` and `core.BaseModel` exist from migration 0001.** `BaseModel` gives every table a UUIDv7 primary key plus `created_at`/`updated_at`. UUIDv7 is generated in Python (`apps/core/ids.py`, RFC 9562) because Python 3.13 has no `uuid7()`; time-ordering keeps B-tree inserts append-mostly. PostgreSQL extensions (pg_trgm, unaccent, btree_gist) are installed by migration `core.0001`.
2. **Host-based URLconfs** (`apps.core.middleware.HostURLConfMiddleware`, sync and async capable):
   - `api.<domain>` → `config.urls_api`;
   - `tv.<domain>` → `config.urls_xtream`;
   - any other allowed host (`web`, `localhost`) → `config.urls_internal`.

   Unknown hosts get a 400 from `ALLOWED_HOSTS`. Traefik routers also exclude `/internal`, so there are two independent layers.
3. **Two Docker networks:**
   - `edge`: Traefik, web, frontend;
   - `backend` (`internal: true`): web, worker, beat, PostgreSQL, both Valkey instances, Meilisearch. It has no internet egress and no route from Traefik.

   Workers that need egress (TMDB in M5) get an explicit egress network then.
4. **Health model:**
   - public liveness (`/api/v1/health`) reveals nothing;
   - internal readiness (`/internal/health/ready`) checks every store and reports only exception class names, never messages (they can carry hosts or credentials);
   - a beat → broker → worker heartbeat (`hb:beat` in redis-state) backs the beat container's healthcheck.
5. **Dev port fallback:** `make secrets` picks `HTTP_PORT=8080` when port 80 is taken, so the stack boots on machines with a local web server.

## Alternatives considered
- **Default `auth.User` until M3:** the migration later would require a database reset.
- **Integer keys plus a separate public UUID:** two identifiers everywhere. Xtream's integer `xc_id` is added only where clients need it (catalog, M6).
- **Path prefixes instead of hosts** (`/xtream/...`): IPTV apps expect `player_api.php` at the server root, and separate hosts give separate rate limits and caching.
- **A single flat Docker network:** simpler, but Traefik would sit one hop from the databases.

## Consequences
- Every new model inherits `BaseModel`; tests assert UUIDv7 keys.
- New public endpoints go into the right host URLconf; internal ones go only into `urls_internal`.
- Services that need internet access must be added to an egress network deliberately.
- The beat healthcheck depends on a worker running the heartbeat. A dead worker therefore shows up on both containers, which is intentional.
