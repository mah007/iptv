# ADR-0013: The customer API: two sign-in transports, entitlement-filtered catalogue, search, web playback and engagement

- **Status:** Accepted
- **Date:** 2026-10-04
- **Milestone:** M6 (catalogue and search), §7.9 (engagement), the API side of M11b (portal)

## Context
SPEC §10 lists the customer endpoints (auth, me, catalogue, search, playback, engagement) on `api.<domain>/api/v1`. Two kinds of clients will call them:

- the customer portal, a browser SPA on `app.<domain>`;
- later, native and TV apps, which hold no cookies and cannot answer CSRF checks.

SPEC §11 asks for two transports:

- web sessions: HttpOnly, Secure, SameSite=Lax cookies, CSRF on unsafe methods, CORS restricted;
- app tokens: a 10-minute access token plus a rotating refresh token with reuse detection, which revokes the family.

It also asks for Argon2id passwords, rate limits, and django-axes lockouts with an exponential cool-off that is never permanent.

ADR-0004 already reserved `app.<domain>/api` for a same-origin portal API (Traefik router `portal-api`) and explained why a browser SPA should not call another origin:

- `app.localhost` and `api.localhost` are different sites;
- cross-site requests need preflights;
- a cookie scoped to the parent domain would leak to the other hosts.

Customers created by admins have unusable passwords (ADR-0006), so they need a way to set one.

The rest of the API needs:

- a catalogue filtered by the entitlement (§7.4), in Arabic or English;
- search with the matcher's Arabic normaliser (§7.8);
- web playback that reuses `start_playback` (ADR-0007, ADR-0010);
- engagement: progress, favourites, ratings, recommendations (§7.9).

## Decision

### 1. One set of routes, two hosts, two transports
- `config.urls_api.customer_api` is the list of customer routes. Each app keeps its routes in `apps/<app>/urls_customer.py`, and billing adds its own.
- It is mounted twice:
  - **`app.<domain>/api/v1`** (`config.urls_portal`): the portal, signed in with a **session cookie and CSRF**, exactly like the admin (ADR-0004). It is same-origin, so it needs no CORS. The cookie is host-only, with `security.customer_session_days` (30) of idle expiry, renewed on every request.
  - **`api.<domain>/api/v1`** (`config.urls_api`): apps, signed in with **`Authorization: Bearer`**. Cookies are never read there, so there is no CSRF exposure, and no CORS is configured because no browser origin needs it.
- Each authentication class answers on its own host only. `PortalSessionAuthentication` checks `request.urlconf`, and so does `BearerTokenAuthentication`. A portal cookie is therefore never a credential on `api.`, and a token never one on `app.`. 401s name the right scheme (`Bearer realm="api"` or the session realm).
- `auth/*` differs per host:
  - the portal has `csrf`, `login`, `logout` and `password/{forgot,reset}`;
  - apps have `login` (with an optional `device_name`), `refresh`, `logout` and the same password routes.
- Anonymous unsafe portal requests check CSRF themselves (`CsrfOnPortal`), as in ADR-0004.

### 2. App tokens: opaque, in redis-state, rotating (`apps/accounts/customer_tokens.py`)
- **Tokens.** Access tokens (`siptv_at_…`, `security.customer_access_token_ttl_s` = 600) and refresh tokens (`siptv_rt_…`, `security.customer_refresh_token_days` = 30, sliding) are 256-bit random strings. Only their SHA-256 digests reach redis-state (noeviction, AOF).
- **Families.** A sign-in starts a *family* bound to the user and to a new `app` device.
  - `refresh` rotates the pair in one Lua script.
  - Presenting an already-rotated refresh token is **reuse**: the family is deleted, every access token of the family stops working at once, the device is revoked, and `auth.token_reuse` is audited.
- **Revocation.** Each access-token check also checks that its family exists, so a sign-out, a password reset or a disabled account ends app access immediately. A password reset revokes every family of the user. Portal sessions end through Django's session auth hash.
- **Why not JWTs:** every request must check revocation in Redis anyway, so a self-contained token adds nothing and could not be revoked. It would also need a new dependency (PyJWT or simplejwt) and an image rebuild. The SPEC's properties hold: a short-lived access token, rotation, and reuse revoking the family.

### 3. Sign-in, lockouts and password links (`apps/accounts/customer_auth.py`)
- **Logins.** A customer signs in with a username, an email or a phone number, normalised to E.164 the same way as the admin's phone field. An ambiguous match counts as no match.
- **Lockouts.** django-axes counts failures per username and IP, with the cool-off from ADR-0006. On top, a per-IP budget in redis-state (`security.customer_auth_requests_per_ip`, 60 per 15 min) covers failed sign-ins, reset requests and bad reset links across usernames, and answers `RATE_LIMITED` (429) with `Retry-After`. A Traefik middleware (`customer-auth-ratelimit`, 5/s, burst 20 per IP) sits in front of `/api/v1/auth` on both hosts.
- **Who signs in.** Staff never sign in here, since they use the admin host with MFA. Disabled accounts cannot sign in. Suspended customers can sign in and see their status, and playback refuses them. Every bad sign-in reads the same: `INVALID_CREDENTIALS`.
- **Password links** are Django's password-reset tokens, with our own salt and lifetime per purpose:
  - `reset`: `security.password_reset_ttl_min`, 60 minutes;
  - `invite`: `security.password_invite_ttl_days`, 7 days.

  They are HMACs over the password hash and the last sign-in, so a link works once and stops working when the password changes. Nothing is stored, and the link is never logged or audited.
  - "Forgot password" always answers 202. Each account receives at most `security.password_reset_emails_per_hour` (3) emails.
  - The admin's `POST /api/v1/admin/customers/{id}/password-invite` (`customers.edit`) emails the link when possible and returns it once (`no-store`), for customers who only have a phone.
  - Email goes through `set_password_link_sender`, which the notifications app (ADR-0012) installs. It renders and sends the link without writing it to the outbox.

### 4. Devices
- **Device per sign-in.** Each portal session gets a `web` device, created at its first playback and named from the User-Agent ("Chrome on Windows"). It is revoked at sign-out. Each app sign-in gets an `app` device, revoked at sign-out or on reuse.
- **`max_devices` counts only `xtream` devices,** the ones holding credentials that can leak (the one-line change in `accounts.services._issue_device`). Browser and app sign-ins are bounded by the password, the lockouts and `max_streams`.
  - Why one device per session: `start_playback` stops a device's other session when it starts a title, so two browsers sharing one device would stop each other.
  - Why not count them: the limit would fill up with sign-ins and block "Add TV app".
- **`me/devices` endpoints:**
  - list, flagging the current device;
  - add an Xtream device with generated or chosen credentials, shown once;
  - rename;
  - revoke;
  - new Xtream credentials, shown once.

  They reuse `accounts.services`, with the customer as the audited actor.

### 5. What a customer may browse (`apps/catalog/browse.py`)
- One scope per request comes from the cached entitlement (`ent:{user}`). Every surface uses it: lists, title pages, home rows, search hydration, favourites, recommendations and playback start.
- **Ready only.** Movies need status `ready`. Series need `ready` plus a playable episode. Episodes need a playable file. Titles past their licence date are hidden before the status job catches up.
- **Content kinds and categories:**
  - `allow_movies` and `allow_series` hide a whole kind;
  - when the profile lists categories, a title must be in one of them, like playback's check 6;
  - "every category" also shows uncategorised titles.
- **Adult content is opt-in.** Adult categories show only when the profile names them. A title in any adult category the customer was not given is hidden, wherever else it is filed. The entitlement has no adult flag, and an explicit grant is the safe default.
- **Expired customers still browse** and get a "renew" prompt. Playback answers `SUBSCRIPTION_EXPIRED`.
- **Language.** The first of `ar`/`en` in `Accept-Language` wins (the portal's fetcher sends `<html lang>`), then the customer's saved locale. Responses say which they used (`Content-Language`, `Vary: Accept-Language`). Empty Arabic fields fall back to English.
- **Artwork** is `{url, sizes: {w500: {webp, avif}, …}, width, height, blurhash}` on the media edge.
- **Large lists** (movies, series, watch history) use DRF cursor pagination over `sort` (added, popular, rating, year, title). Nullable sort keys are coalesced, so no title falls out of a page.

### 6. Home rows (`apps/catalog/home.py`, `apps/engagement/api.py`)
- **Shared rows, cached.** The hero (featured titles with a backdrop, filled with popular ones), recently added, popular this week, collections and one row per category are serialised per scope fingerprint and language. They live in **redis-cache** under `home:<version>:<scope>:<locale>` for 10 minutes. A `catalog_changed` receiver, and every collection edit, replaces `home:version`, which retires every entry at once, like the Xtream cache.
- **Personal rows** are computed per request: continue watching, because you watched (two rows), and top picks.
- **Row order** follows SPEC §9.

### 7. Collections
- `catalog.Collection` and `CollectionItem` hold exactly one movie or series per item, ordered.
- Admin CRUD lives at `/api/v1/admin/collections` (`library.view`, `library.manage`, audited). The customer page is `collections/{slug}`.

### 8. Search (`apps/search`)
- **Index.** The Meilisearch index `titles` holds movies, series and episodes. It is driven by a small httpx REST client (`meili.py`) rather than the official client, to avoid a dependency (and `requests`).
- **Normalised text.** Searchable fields are stored normalised by `apps.search.normalize`, the matcher's normaliser, and queries are normalised the same way. "المصفوفة", "مصفوفه" and "the matrx" all find The Matrix, with typo tolerance on top. Hits carry ids only and are loaded through `browse`, so the index can never show what the customer may not browse.
- **Customer filters.** Every query carries `status = "ready"`, the allowed types, and either `category_ids IN [...]` or `is_adult = false`, plus the request's genre and year.
- **Indexing:**
  - incremental, through the `catalog_changed` receiver and the `index_titles` Celery task (series with their episodes, removed titles deleted);
  - a debounced full rebuild for category changes;
  - a nightly rebuild into a staging index swapped in atomically (beat `search-rebuild-index`, `manage.py search_reindex`);
  - a missing index schedules a build by itself.
- **Fallback.** While Meilisearch fails, a 30-second process-local circuit breaker sends queries to PostgreSQL. It queries movies and series on the new `search_text` column, which holds the normalised titles, is kept up to date on every save and has a trigram GIN index. It uses `websearch_to_tsquery` with the `simple` config plus `word_similarity`, ranked by the better of the two. Episodes need the index.
- **People** are matched in PostgreSQL among people credited in titles the customer may browse.

### 9. Web playback and progress (`apps/engagement/api.py`)
- **`playback/start`** resolves a visible movie, or a series' episode: the one asked for, else the episode in progress or the next one. It then calls `playable_title_by_id` and `start_playback` on the request's device. It returns:
  - the signed URL and its delivery (`progressive` compat MP4, or `segmented` HLS when `prefer=hls` and T1's presentations exist);
  - `resume_ms` and the duration;
  - audio tracks;
  - WebVTT subtitles and the thumbnail sprite map, under the token's own scope (`<scope>/subs/…`, `<scope>/thumbs/thumbs.vtt`, ADR-0014).

  `TITLE_PREPARING` (409) applies while nothing plays yet. Every SPEC §7.4 denial keeps its stable code.
- **Progress** is reported to the API, never through the media edge (SPEC §1.5), every 15 s and on pause.
  - Reports closer together than `playback.progress_min_interval_s` (5 s) are dropped.
  - A title counts as watched past `playback.watched_ratio` (0.9).
  - `stop` saves the final position unthrottled and ends the session as `stopped`. It uses `KickReason.REPLACED`, which maps to that end reason.

### 10. Engagement and recommendations (`apps/engagement`)
- **Models.** `WatchProgress` (one row per user and movie or episode, with the series for episodes), `Favorite`, `Rating` (thumbs) and `SimilarTitle` use typed nullable foreign keys, as in ADR-0009.
- **Watch history** is the progress rows: deleting one also removes the title from continue watching.
- **Similar titles.** `SimilarTitle` is computed nightly as SPEC §7.9's weighted overlap: genres 0.35, top-5 cast 0.25, directors 0.15, keywords 0.15, decade 0.05, language 0.05, with Jaccard overlaps.
  - Only titles sharing a genre, a cast member or a director are scored (an inverted index), and each title keeps its top 30.
  - TMDB keywords are not stored yet, so that term is 0.
  - "More like this" falls back to same-genre titles before the first run.
- **Per-user rows:**
  - because you watched: the last five finished titles, each with twelve unwatched similar ones;
  - top picks: merged similar lists boosted by the genres the customer finishes most, minus anything watched or thumbed down, filled with popular and then top-rated titles;
  - popular this week: plays of the last seven days weighted by `exp(-age/3 days)`, episodes counting for their series.
- **`Recommender`** is the protocol a learned model can replace later.

### 11. OpenAPI and the portal client
- The portal URLconf's schema is generated with `--custom-settings config.urls_portal.SPECTACULAR_SETTINGS`, which gives it its own title and adds the customer enum names.
- `make api-client` now also writes `frontend/packages/api-portal`: the schema in `openapi/portal.yaml` and Orval's TanStack Query hooks. Its fetcher is like the admin's (same-origin, CSRF, problem+json `ApiError` with field error codes), and it also sends the UI language.
- `api-client-check` covers both packages.

## Alternatives considered
- **JWTs for everyone, including the portal (SPA calls `api.` with CORS):** rejected for the reasons ADR-0004 gives. XSS could read the tokens, preflights are needed, and dev and production behave differently.
- **Only session cookies, with the API on `app.` alone:** apps (SPEC §3, "own TV apps") would have no usable transport, and `auth/refresh` would mean nothing.
- **Stored reset tokens in a new accounts table:** this would add a migration to `accounts` (owned elsewhere). Stateless HMAC tokens give single use and expiry with nothing to clean up.
- **Counting browser sessions towards `max_devices`:** sign-ins would exhaust the limit, so customers could not add a TV app. `max_streams` already bounds concurrent use.
- **Meilisearch's official Python client:** an extra dependency for a few REST calls.
- **A full-text `search_vector` column maintained by triggers:** the Python normaliser cannot run in PostgreSQL, so `search_text` is computed on save and in the indexing task, and the fallback builds the tsvector on the fly. The fallback is a degraded mode.
- **Caching personal rows:** they change on every progress report. They are cheap: a fixed number of queries.

## Consequences
- **Two schemas.** Every customer endpoint exists on both hosts. The portal's schema is the one the generated client uses; app developers use the same paths with `Authorization: Bearer`.
- **Recreating `web`.** The Traefik `customer-auth` router and the production `portal-api` and `customer-auth` websecure labels take effect when `web` is recreated.
- **Adult opt-in** changes nothing for Xtream: ADR-0011 applies there.
- **Explicit stops** end as `stopped` through `KickReason.REPLACED`. A dedicated `STOPPED` reason in `apps.playback` would read better in `X-Reason`.
- **Revisit:**
  - TMDB keywords: store them, and the 0.15 term starts counting;
  - customer TOTP (SPEC §9 "optional TOTP");
  - caching home rows per user, if load tests ask for it (M15).
