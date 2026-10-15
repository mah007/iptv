# ADR-0023: Follow-ups: TheTVDB, admin devices and lockouts, the portal's live TV, legal pages, MediaMTX sign-ins, capped MP4s (Q1)

- **Status:** Accepted
- **Date:** 2026-10-16
- **Milestone:** Q1 (follow-ups closing M5 and M11, and the open items of M11b, M12 and CFG)

## Context
Several milestones shipped with named gaps:
- **M5** waited for TheTVDB, the SPEC's fallback for series TMDB can't match.
- **M11** lacked a global Devices page, a Lockouts page, a subscription status in the customers list, stable codes on several validation errors, and a way for admins to change their own password.
- **M11b/M12:** the portal had no live TV, no way to stop a stream playing elsewhere when the plan is full, no own-password change, and no estimate while a title is prepared. Web devices piled up (one per sign-in).
- **§17:** no privacy policy or terms pages.
- **ADR-0017's ingest:** MediaMTX kept its users in its own config (`authInternalUsers` from `.env`), so they couldn't be managed in the admin like every other credential (ADR-0019).
- **Capped plans over Xtream:** on a 720p plan, `<id>.mp4` redirected to the capped HLS presentation, which ExoPlayer-based apps can't open as an MP4.
- **Settings cache:** a version bump that failed (Redis hiccup after the commit) left other processes on old values until the next change.

The rules stay: no acquisition features, no GPL code imported, configuration in the admin, media requests never touch Postgres, the Xtream JSON types unchanged.

## Decision

### 1. TheTVDB as the TV fallback (M5)
- **Our own v4 client** (`apps/metadata/tvdb/`), not a library: `POST /login` with the API key (and the subscriber PIN when one is set) for a bearer token kept in the process and refreshed once on a 401; `search`, `series/{id}/extended?meta=translations`, `series/{id}/episodes/{official|dvd|absolute}/{lang}` (paged), `series/{id}/artworks`, `search/remoteid/{id}`. It shares TMDB's machinery: the Redis token bucket (`ratelimit:tvdb`, `metadata.tvdb_rate_limit_per_s`), the response cache (`tvdb:response:`), tenacity retries with `Retry-After`, error classes that subclass TMDB's so the tasks' retry logic applies, and the metric `iptv_metadata_api_requests{provider="tvdb"}`.
- **Offline fixtures** (`apps/metadata/fixtures/tvdb/`) are synthetic and say so; `manage.py record_tvdb_fixtures` records real responses (they stay out of the public repo).
- **Matching:** TMDB stays primary. TheTVDB is asked only when TMDB has no candidate or no confident one for a series. A TheTVDB series that names its TMDB id is matched there (the crosswalk), so TMDB stays the source of truth; otherwise the series is built from TheTVDB (`MetadataSource.TVDB`, `tvdb_id` on series, seasons, episodes and people) with its aired order. Reviews carry each candidate's provider, and resolve, search and rematch take `provider=tvdb`.
- **Attribution and licence:** "Metadata provided by TheTVDB" with a link wherever a TheTVDB-sourced title shows (the portal's title page, the admin, About). TheTVDB is free below USD 50,000 of yearly company revenue with that credit, and paid above: the operator decides; nothing works differently without a key.

### 2. Admin: devices, lockouts, subscription status, own password (M11)
- **Devices page** (`GET admin/devices`, `POST admin/devices/bulk`): every customer's devices with filters (kind, app, status, seen within, country, customer) and bulk block, unblock and revoke (`devices.manage`, at most 500, audited per device).
- **Lockouts page** (`GET admin/lockouts`, `POST admin/lockouts/unlock`): sign-in lockouts (axes), second-factor and step-up failure counters, Xtream username and address pauses, in one list with ids such as `sign_in:<pk>` or `xtream_address:<ip>`. Unlocking needs `lockouts.manage` and a recent step-up; an admin's lockout needs `admins.manage` too. New permissions `lockouts.view` and `lockouts.manage` (migration `accounts/0008`).
- **Subscription status** (`apps/accounts/governing.py`): one annotation gives each customer `governing_status` (`active`, `grace`, `expired`, `none`, `suspended`, `disabled`), its source (subscription or access) and the effective end, following ADR-0012's governing rules. The customers list filters on it and the dashboard counts customers in grace.
- **Field codes:** library, catalog and settings validation errors carry stable codes, translated in both languages.
- **Own password** (`POST auth/password`): current password plus a recent step-up; other sessions end, an email confirms. Five wrong current passwords sign the session out.

### 3. Customer API and portal gaps
- **`me/streams`** lists the customer's open sessions, and `me/streams/{id}/stop` frees a slot (audited). On `CONCURRENCY_LIMIT` the player lists the other streams with **Stop it and play here**.
- **`me/password`:** the current password, then every other portal session, app sign-in and their playback ends; TV logins keep their own passwords.
- **`eta_s`** on `TITLE_PREPARING`, from `request_on_demand()`, with `Retry-After`; the player says when and retries by itself.
- **One web device per browser:** a signed `siptv_wd` cookie (400 days) brings a browser back to its device; signing out keeps it. Web devices unused for `security.web_device_idle_days` (30) are retired by a nightly job.
- `RatingRequest.value` is nullable (clearing a rating), typed as such in the portal client.

### 4. Live TV in the portal
- **API:** `GET live/channels` (groups, channels, now and next: three queries whatever the number of channels) and `GET live/channels/{id}/guide?from&to` (programmes marked `catchup` when fully inside the recorded window). Visibility follows the catalogue's rules (ADR-0013): the plan's `allow_live`, granted categories, adult groups only when granted. `visible_in_xtream` is the IPTV apps' switch and isn't applied, as for movies and series.
- **Playback:** `playback/start` takes `title_type` `live` or `catchup` (with `start` and `duration_min`), reusing ADR-0017's `playable_channel` and the same entitlement checks; the grant says `live: true`.
- **Portal:** a Live page (group chips, channel cards with now/next and progress, a guide drawer with **Watch from the start** on recorded programmes) and the player's live mode: no seek bar, skips or speed, a LIVE mark, and a fresh grant two minutes before the signed link expires. Channels a browser can't decode (not H.264 with AAC or MP3) point to TV apps.
- **Duplicate starts:** the watch page starts once per mount (a ref), so React's development double effects can't take a second slot.

### 5. Legal pages (§17)
- **Settings group `legal`:** `legal.privacy_en/_ar` and `legal.terms_en/_ar`, Markdown, with a new registry widget `text` (a textarea with a preview). Defaults are English and Arabic templates (PDPL references) with `{service}`, `{support_email}`, `{support_phone}` and `{seller_address}` filled from Branding and Billing; a list item whose value is empty is left out. While a text is still the template, the admin shows "Template text: review it with a lawyer before go-live"; customers never see that.
- **Public API:** `GET /api/v1/legal/{privacy|terms}?lang=` on the app and api hosts, cached 60 s.
- **Rendering:** a Markdown subset in `@smart-iptv/ui` (headings, paragraphs, lists, bold, https/mailto links) that produces React elements only.
- **Portal:** public `/privacy` and `/terms`, linked from both footers (so from the sign-in page), About, and the checkout ("By continuing you accept…").

### 6. MediaMTX asks Django about sign-ins
- `docker/mediamtx/mediamtx.yml` uses `authMethod: http` with nothing exempt. `compose.mediamtx.yml` sets `MTX_AUTHHTTPADDRESS` to `http://mediamtx:${MEDIAMTX_AUTH_TOKEN}@web:8000/internal/mediamtx-auth`; Go sends the userinfo as Basic auth. `MEDIAMTX_AUTH_TOKEN` is a generated bootstrap secret (`secrets.sh` appends it on update).
- **`/internal/mediamtx-auth`** (internal URLconf only; 404 without a token, 401 without the right one):
  - `publish`: the ingest publish user (`live.ingest_publish_user` / `_password`); MediaMTX's own loopback (its `runOnDemand` hooks) may publish;
  - `read`, `playback`, `api`: the ingest read user or a MediaMTX integration's user, from the Docker networks only (as the internal users were);
  - anything else is refused; no credentials → 401 (RTSP clients send them only when asked); wrong ones → 403, counted per address, which is refused for `live.ingest_auth_block_minutes` after `live.ingest_auth_max_failures`;
  - `iptv_live_ingest_auth_total{action,result}`; credentials are never logged.
- **Settings:** the four users and passwords are registry keys (passwords encrypted) whose `.env` fallbacks are the old `MEDIAMTX_*` variables, so existing encoders keep working after the update and the admin can move them in. An Integrations card **Live ingest** tests the read user against MediaMTX's API, which exercises the whole loop.
- **`make mediamtx-test`** (about 15 s, in `make ci`): the production image and config against a throwaway web process; publish, read, API and refusals. The dev MediaMTX keeps internal auth.

### 7. Smaller items
- **Capped compat MP4s by remux:** for each plan ceiling below 1080p that an active plan, subscription or access profile uses (`library.capped_compat_mp4`: `auto` or `off`), the ladder's tallest rung that fits, its AAC audio renditions and the text subtitles (mov_text) are remuxed into `compat720.mp4` / `compat480.mp4`: a `compat_capped` rendition named after its token rendition, made by the `compat_capped` job (CPU, `-c copy`, seconds per title). It is queued when a ladder finishes and hourly for a new ceiling (`queue_capped_compat`), and offered to playback as a progressive rendition, so check 7 picks it for `.mp4` on a capped plan. The publisher, scope links and cleanup handle it per row. `make compat-live` checks a 720p account's `.mp4` through to the edge's MP4 bytes.
- **Settings-cache healer:** the cached values carry a fingerprint of the stored rows (key and `updated_at`); a beat task every minute compares it with the database and starts a new version when they differ (`iptv_settings_cache_heals_total`, a warning). A failed bump now heals within a minute.
- **Registry validation codes** reach the admin on bulk saves, translated.

## Alternatives considered
- **TheTVDB's own Python library:** a thin synchronous wrapper without retries, rate limiting or caching; our client shares TMDB's limiter, cache and retries, and adds no dependency.
- **TheTVDB as an equal source:** two sources of truth for the same series; the crosswalk keeps TMDB primary.
- **Reading programme names in the player from the guide endpoint:** an extra request per channel switch; the listing's now/next is enough.
- **MediaMTX `jwt` auth:** every encoder would need tokens; HTTP auth keeps plain RTMP/SRT users and puts them in the admin.
- **Encoding a 720p MP4:** minutes of GPU or CPU per title; the remux reuses the ladder's rung for seconds of CPU. **Refusing `.mp4` on capped plans:** breaks the apps that only play MP4.
- **Shorter settings-cache TTLs:** more database reads everywhere to cover a rare failure; the healer costs one small query a minute.

## Consequences
- Migrations: `catalog/0005` (TheTVDB ids, review provider), `accounts/0008` (lockout permissions), `media/0004` (capped compat). All backward compatible: the old web ignores the new rendition kind and the new columns have database defaults.
- A server with `LIVE_INGEST=1` gets `MEDIAMTX_AUTH_TOKEN` from `secrets.sh` during `install.sh --update`; its encoders keep their `.env` users until the admin moves them in.
- Servers with a 720p or 480p plan queue capped MP4 remuxes for existing titles in the first hours after the update (seconds each, on the CPU queue). Disk use grows by about that rung's size per title, only for ceilings plans use.
- Browsers get the `siptv_wd` cookie on their next play; old per-sign-in web devices are retired by the first nightly run after 30 days of not being used.
- TheTVDB stays off until its key is saved in Settings → Integrations.
- The legal texts are templates until the operator replaces them; the admin says so.
