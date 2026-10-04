# ADR-0011: The Xtream API over the real catalog and playback, and sign-in limits

- **Status:** Accepted
- **Date:** 2026-10-04
- **Milestone:** M9 (POC slice 4)

## Context
ADR-0008 built `apps/xtream_api` as pure builders behind two protocols, `CatalogSource` and `PlaybackStarter`, and left three things for slice 4:
- the catalog source over the catalog models (slice 2);
- the starter over `playback.services.start_playback` (slice 3);
- the SPEC §7.5 per-IP and per-username sign-in limits.

## Decision
- **Installation.** `XtreamApiConfig.ready()` installs `DjangoCatalogSource` (`catalog.py`) and `DjangoPlaybackStarter` (`starter.py`). Tests still swap in fakes with `set_catalog_source()` and `set_playback_starter()`.
- **What apps see.**
  - **Categories:** of the kind, `visible_in_xtream`, allowed by the scope, and holding at least one listed title. Empty categories are dead ends in every app, so they are left out.
  - **Movies:** status `ready`, in at least one such category.
  - **Series:** status `ready`, in such a category, and with at least one playable episode.
  - **Episodes:** only playable ones, meaning they have a file matching `catalog.services.playable_files()`. Seasons still come from the catalog, and the builder drops seasons without episodes.
  - **Primary category:** a title's `category_ids` follow link order (the link rows' ids), which puts the library's default categories before the genre categories. The first one is the primary. Genre categories linked in one `add()` keep whatever order they were stored in, which is stable but not TMDB's.
- **Field mapping.**
  - **Artwork:** WebP only, because apps cannot decode AVIF. Posters are `w500`, large posters `original`, backdrops `w1280` (at most five), and stills `w500`.
  - **`added`:** the title's `created_at`.
  - **Series `last_modified`:** the newest playable episode's `created_at`.
  - **Credits:** at most ten cast members; directors for movies; for series, the creators (credits with role `writer` and character "Creator").
  - **`is_adult`:** set when any visible category is adult.
- **Media info.** It describes what the player receives:
  - a direct-play source as probed, with the default audio track;
  - otherwise the ready `compat_mp4` rendition, with H.264 from the rendition and AAC stereo first (`profiles.yaml`).
- **Query cost.** Every call has a fixed number of queries whatever the catalog size, asserted in `test_catalog_source.py`:

  | Call | Queries |
  |---|---|
  | categories | 1 |
  | movie list | 3 |
  | movie | 7 |
  | series list | 5 |
  | series | 11 |
  | M3U episodes | 1 |

  The responses are cached anyway (ADR-0008).
- **Play URLs.** `title()` resolves movies and episodes whose title status is `ready`, `processing` or `review`. Anything else, including `hidden` and `license_expired`, is unknown, so the app gets a 404 rather than a retry. The starter then asks `catalog.playable.playable_title(kind, xc_id)`:
  - **None** (the title is not ready) means `TITLE_PREPARING`, a 503 with `Retry-After`.
  - **A `PlaybackDenied`** becomes its code (the X-Reason). `CONCURRENCY_LIMIT`, `CONTENT_TYPE_NOT_ALLOWED` and the other refusals are 403.
  - **A Redis error** becomes `INTERNAL_ERROR`, a 503.
  - **A grant** becomes a 302 to the signed edge URL.

  The extension `.m3u8` asks for HLS, and anything else for MP4. The app's User-Agent goes to the session row. `user_info.active_cons` is `playback.concurrency.active_streams()`, which reads 0 when redis-state is down.
- **Sign-in limits (`ratelimit.py`).** Every refused sign-in on tv.* counts in redis-state, under `xc:fail:ip:{ip}` and `xc:fail:user:{sha256(casefold(username))[:32]}`. Each counter is a fixed window that starts at the first failure: INCR plus EXPIRE NX.
  - **Past either limit,** the password is not verified until the window ends. The endpoint gives its usual failure: `{"user_info":{"auth":0}}` with HTTP 200, an empty 403 for get.php and xmltv.php, or an empty 404 for play URLs. The body therefore reveals nothing, and a guesser costs no Argon2 work.
  - **A success** clears the username's counter.
  - **If redis-state is down,** nothing is limited.
  - **Defaults:** 30 failures per username and 60 per IP in 900 s. They are generous because apps fire about ten requests at start-up, and each one fails after a password change.
  - **Settings:** `xtream.auth_failures_per_username`, `xtream.auth_failures_per_ip` and `xtream.auth_failure_window_s`. They are read from the registry once it declares them; until then the defaults in `ratelimit.DEFAULTS` apply.
- **Request rate.** Traefik limits the request rate per IP on the tv router (the `xtream-ratelimit` middleware: 10 requests/s on average, bursts of 50). Django's counter catches slow guessing that a request rate cannot.
- **Contract account.** `manage.py xtream_contract_account` creates or updates the account that `make compat-live` and the IPTVnator journey sign in with: all categories and 3 streams. It reads the credentials from `XC_USER`/`XC_PASS` and never prints them.

## Alternatives considered
- **Listing empty categories,** as PHP panels do. Rejected: apps show them as empty screens, and the genre map creates about twenty categories.
- **Treating unknown and not-ready titles the same (404).** Rejected: apps would give up on a title that will play in minutes. 503 with `Retry-After` keeps them retrying, and status-based resolution keeps hidden titles a 404.
- **A sliding or progressive lockout window.** Rejected for now: each further attempt would extend the window, so an attacker could keep a username locked indefinitely. The fixed window bounds that.
- **django-axes for Xtream sign-ins.** Rejected: it writes to Postgres on every failure and answers with a lockout response, while the Xtream failure must stay byte-for-byte identical.

## Consequences
- IPTV apps browse and play the real catalog. When the catalog changes, the existing `notify_catalog_changed` invalidation retires the cached responses.
- The per-username limit lets anyone who knows a username lock it out for at most one window. The defaults keep that short; M14 (CrowdSec) bans IPs that do it repeatedly.
- Media info comes from the probe and rendition columns. Per-track audio of renditions waits until renditions record it.
- **Registry** (`apps/core/registry.py`): the three limit settings above should be declared there so admins can tune them.
