# ADR-0008: The Xtream API: authentication, status, catalog cache and play redirects

- **Status:** Accepted
- **Date:** 2026-10-04
- **Milestone:** M9 (POC slice 4)

## Context
IPTV apps (Smarters, TiviMate, XCIPTV, OTT Navigator, IPTVnator) talk to `tv.<DOMAIN>` with the Xtream Codes API that PHP panels made a de facto standard. They parse it strictly: a wrong type usually gives an empty list, not an error. SPEC §7.5 fixes the types, and `compat/` pins them as schemas and golden fixtures.

compat/README left some questions for M9: unknown ids, failed logins on `get.php` and `xmltv.php`, and the `type=m3u` and `output=hls` aliases. This ADR answers them and records what `apps/xtream_api` does.

## Decision
- **Layering.** `apps/xtream_api` builds responses as pure functions over small DTO dataclasses (`dto.py`, `payloads.py`, `playlist.py`). Data comes from a `CatalogSource` protocol and playback from a `PlaybackStarter` protocol. Slice 4 plugs in the Django catalog source and the `playback.services.start_playback` adapter through `set_catalog_source()` and `set_playback_starter()` in `AppConfig.ready()`; tests use in-memory fakes. Every builder enforces the contract whatever the source returns:
  - category ids are cut to the visible categories, primary first;
  - image URLs that aren't absolute JPEG/PNG/WebP become `""`;
  - malformed IMDb or YouTube ids become `""`;
  - ratings have one decimal place, with `rating_5based = rating / 2`.
- **Authentication.** It reuses `accounts.services.authenticate_xtream` (Argon2id, HMAC-keyed 5-minute cache bound to the stored hash, a burn verify for unknown users). A failed login is always HTTP 200 `{"user_info":{"auth":0}}`, identical in body, headers and cost for an unknown user and a wrong password. Credentials are accepted in the query or a form body, over GET or POST. No action, an unknown action or `get_account_info` returns the login payload.
- **Status.** A known account always signs in (`auth: 1`). `user_info.status` is:
  - `Active`;
  - `Expired`;
  - `Disabled`: for a suspended or disabled account, a blocked or unapproved device, or a customer without an access profile.

  Anything not `Active` gets `[]` for lists, `{}` with 404 for info actions and a header-only M3U.

  `exp_date` for access without an end is `4102444800` (2100-01-01 UTC), because the schema forbids `null`. Until subscriptions exist, `is_trial` is `"0"` and `message` is `""`.
- **Device activity.** `last_seen`, `last_ip`, `first_seen` and the credential's `last_used_at` are written at most once a minute per device (`xc:seen:{device}` SET NX EX 60 in redis-state).
- **Answers to the open questions:**
  - An unknown or hidden `vod_id`/`series_id` gets `{}` with HTTP 404.
  - `get.php` and `xmltv.php` auth failures get an empty 403, as the fixture server does.
  - `type=m3u` is accepted, and `output=hls` is an alias of `m3u8`.
- **Play URLs.** `/movie|series/<u>/<p>/<xc_id>.<ext>` answers:
  - an auth failure or unknown id: an empty 404;
  - a refusal from the playback starter: an empty 403 with `X-Reason`;
  - `TITLE_PREPARING`, `PLAYBACK_UNAVAILABLE` and internal errors: 503 with `Retry-After`;
  - a grant: 302 to the signed edge URL.

  Unknown refusal codes fail closed with 403.
- **Ordering and localisation.** Categories are ordered by `(sort, xc_id)`. Items are ordered by primary category position, then newest first, then id. `seasons` lists only seasons with playable episodes, with one episode per `(season, number)`. Names, plots and genres follow the user's locale and fall back to the other language; season names are generated ("Season N", "Specials") rather than falling back to English.
- **Catalog cache.** Responses are cached as orjson in redis-cache under `xc:{sha256(version|scope)[:16]}:{locale}:{action}[:{extra}]` with a 1-hour TTL.
  - `scope` is the entitlement scope (what the customer may see), so customers with the same access share entries and access changes need no invalidation.
  - Catalog writers call `invalidate_on_commit()`, which bumps the version.
  - Not-found results are never cached.
  - M3U entries are cached without credentials and rendered per request.
  - If redis-cache is down, responses are built directly.
- **Credentials in logs.** Tests show that no log line carries a username or password from play paths, query or form parameters, error pages or tracebacks. Gunicorn's access log stays off.
- **Admin-chosen credentials** (owner request, 2026-10-04). Admins may set a device's Xtream username and password instead of the generated ones:
  - usernames: 3–32 characters of `[A-Za-z0-9._-]`;
  - passwords: letters, digits and `. _ - ~ @ ! *`, between `xtream.password_min_length` (default 8) and 64 characters.

  Apps put both into URLs, often without percent-encoding, so these are the characters that need none. Usernames are unique regardless of case, so no two logins differ only in case, but authentication compares them exactly, as PHP panels do.

## Alternatives considered
- **Serializers bound to the Django models.** Rejected: the types would depend on the model layer. Pure builders over DTOs can be checked against the golden fixtures with no database.
- **`exp_date: null` for unlimited access**, as PHP panels send. Rejected: the compat schemas forbid it and several apps show "expired" for `null`.
- **Distinguishing unknown users in the login response.** Rejected: it would let anyone enumerate usernames.
- **Invalidating cache keys per customer.** Rejected: a version bump plus scope-keyed entries invalidates everything at once with one Redis write.

## Consequences
- The Django `CatalogSource` and the `PlaybackStarter` adapter remain to wire in slice 4. Until then the default starter answers 503.
- SPEC §7.5 rate limits per IP and per username are still to do: a Traefik rate-limit middleware on the tv router, plus a failure counter in redis-state that answers `auth: 0` without verifying.
- The contract tests need `compat/` mounted in the test container (`./compat:/compat:ro`); without it they skip and say why.
