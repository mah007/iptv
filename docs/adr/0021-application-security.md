# ADR-0021: Application security (M14a)

- **Status:** Accepted
- **Date:** 2026-10-14
- **Milestone:** M14a (the application half of M14; M14b, [ADR-0020](0020-operations-security-and-backups.md), was the operations half)

## Context
SPEC §11 and §16 ask M14 for a strict CSP, WebAuthn for staff, re-authentication before sensitive actions, an optional admin IP allowlist, the §11.1 sharing score, rate limits everywhere, dependency and code scanning, and a ZAP baseline. The [threat model](../security/threat-model.md) added its open gaps (G-01 to G-33, G-40), several of them due "before go-live". This ADR records how they were closed. The threat model's version 2 gives every gap its status.

## Decisions

### 1. Security headers and the Content-Security-Policy (G-19, G-24)
- **One policy module** for both SPAs: `frontend/packages/ui/csp/policy.js` (plain JavaScript, so Node runs it at image build without a build step). `default-src 'none'`; scripts and styles only from the app plus a per-request nonce; no `unsafe-inline` or `unsafe-eval` anywhere; images and media from the media edge, `https://*.<DOMAIN>` and the admin's `security.csp_extra_media_origins`; TMDB images in the admin only; the YouTube-nocookie frame in the portal only; `frame-ancestors 'none'`, `base-uri 'none'`, `object-src 'none'`; `report-uri` and `report-to` (both: Firefox only reports to the former).
- **Nonces.** Production: the frontend image renders an nginx include per app at build; the nonce is nginx's `$request_id`, written into `index.html` by `sub_filter` over a placeholder that Vite's `html.cspNonce` put in the `<meta property="csp-nonce">` tag. Development: a Vite plugin sends the same headers with a random nonce per page. Libraries that add `<style>` at run time get the nonce: sonner through a small pnpm patch (it has no nonce option), react-remove-scroll through `__webpack_nonce__`, Radix Select through its `nonce` prop.
- **Media origins come from Django** at frontend start (`/internal/frontend-csp`, read by `docker/nginx/30-csp.sh`), falling back to `MEDIA_BASE_URL` and `*.<DOMAIN>`. Changing the extra origins applies at the next start: `install.sh --update` recreates the frontend after web is up. No live reload, by the owner's choice.
- **Django** sends `default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'` and a Permissions-Policy on every response, and Referrer-Policy per host (`no-referrer` on `tv.`, `same-origin` on the admin). HTML documents (invoices, mail previews) are served from their own URLs with their own CSP (`document_csp()`), since a `srcdoc` frame would inherit the SPA's; previews are sandboxed. Traefik's shared `secure-headers` keeps only nosniff, frame-deny and HSTS.
- **Reports** go to `/api/v1/csp-report` on the admin and portal hosts (Traefik: 1/s per IP; Django: 60/min per IP), counted in `iptv_csp_violations_total{app,directive}`, with URLs stripped of queries.
- **G-24 in the code:** ESLint refuses `dangerouslySetInnerHTML`, `srcDoc`, `innerHTML`/`outerHTML` assignment and `insertAdjacentHTML`.
- `make smoke` checks the headers per host; a Vitest suite checks the policy; `scripts/smoke_images.sh` checks that the production nonce changes per request and matches the page.

### 2. Admin sign-in: security keys, step-up, idle timeout, allowlist (G-03, G-40)
- **WebAuthn** with py_webauthn 3.0.1 (BSD-3) and @simplewebauthn/browser 14 (MIT). The relying party is the admin host; the expected origin comes from `config.origins`. Keys are an alternative second factor to TOTP, so passkeys on phones work too. `security.webauthn_required_for` (`none` by default, `owners`, `admins`) makes keys mandatory for those roles: they're sent to *Sign-in and security* to add one, and can no longer confirm with a code. A cloned key (sign counter going backwards) is refused and audited.
- **Step-up** ("Confirm it's you"): views list `step_up_methods`; when the last confirmation is older than `security.admin_step_up_window_min` (5 minutes), the API answers `403 STEP_UP_REQUIRED`, the SPA's fetcher shows a dialog (code or key), and the request is retried once. **Never the password alone**: a stolen session plus a phished password must not unlock refunds or roles. Covered: settings and integrations, roles and admins, refunds, plan changes, trials and extensions, payments recorded by hand, deletions, credential resets and creation, backups, *revoke by app*, GeoIP downloads. **Protective actions never ask** (block, revoke, kill, suspend): an admin under attack must be able to act at once. Five wrong confirmations end the session.
- **G-03:** 5 code attempts per half-authenticated sign-in, 10 per account in 15 minutes; the session key changes after the password step and again at login.
- **MFA reset:** an owner (or an admin with *admins.manage*, never for owners) resets another admin's second factors; `manage.py reset_admin_mfa` is the host's last resort. `User.auth_epoch`, folded into the session hash, ends every session at once.
- **Idle timeout** comes from `/auth/me`, so admins without *settings.view* get the configured value.
- **IP allowlist** (`security.admin_ip_allowlist`) lives in Django only, not Traefik: it applies to the admin host's `/api/` (and Grafana's forward auth), the admin refuses a list without your own address, and `set_setting ... --stdin < /dev/null` clears it from the host.

### 3. Customers (G-28, G-29, G-30, G-31)
- **G-31:** creating or renewing a TV login asks for the customer's password when the last sign-in or confirmation is older than `security.customer_step_up_window_min` (10 minutes); the "confirmed until" time lives in redis-state, per session (a hash of its key) or per app-token family. Not in the session itself: with `SESSION_SAVE_EVERY_REQUEST`, a request that loaded the session before a lock would write the old time back, and the lock would be undone (found by the portal E2E). For the same reason, "must add a key" is computed per request, not kept as a session flag. The customer gets an email for a new login and for a reset (never with the password).
- **G-29:** a successful checkout is confirmed with the provider's API (Stripe Checkout Session, Moyasar payment) before the subscription starts, when the provider is configured; disputes suspend the subscription; full refunds end it.
- G-28 and G-30 were already built in the commercial slice; tests now pin them.

### 4. Sharing detection (spec §11.1; G-18, G-27)
- **Data:** `Sighting` rows, one per customer, device, network (/24 or /64), source and clock hour (portal and app sign-ins, playback starts, Xtream sign-ins, and the networks stream-auth sees per session, written to redis-state and collected every 5 minutes, so media requests still never touch Postgres). `RiskFlag`: one open flag per customer with its evidence and the actions taken.
- **Score**, every 5 minutes for customers seen recently, over 1 and 24 hours: providers (ASNs), countries, impossible journeys between country centroids (Natural Earth, public domain), and networks per TV login (changes within one provider ignored unless simultaneous). Weights: 2 per ASN over the hourly limit, 1 per ASN over the daily limit, 2 per extra country, 3 per impossible journey (up to 3), 2 per extra network per login. Flag at `antisharing.flag_score` (4). Every limit is a setting.
- **Never automatic:** a flag tells the admins (once per flag) and shows on the customer's Security tab with the evidence; the ladder (warn, reset app logins, suspend) is manual, audited, and tells the customer.
- **Retention:** `privacy.antisharing_retention_days` (90).

### 5. IP geolocation: DB-IP Lite
- The free country and ASN databases (CC BY 4.0), read with maxminddb 3.2.0 (Apache-2.0) from memory-mapped files, reopened when the file changes. The worker downloads the current month's release (or last month's early in the month) on its first start and checks daily, validates type, size, build date and sample lookups, and swaps files atomically in the `geoip-data` volume (worker writes, web reads). No account or key.
- **Attribution:** "IP Geolocation by DB-IP" on both apps' About pages, the evidence view and the GeoIP card.
- **Country rules:** local network addresses never match them; a public address the database doesn't place follows `geoip.unknown_country_policy` (`deny` by default when allow rules exist). Admins get *GeoIpDatabaseMissing* when the database is missing for an hour.
- Tests build small databases with mmdb-writer (MIT, dev only); nothing downloads in tests.

### 6. Rate limits (G-20) and other gaps
- **Traefik, per IP:** admin and customer sign-in 5/s (burst 20), Xtream 10/s (burst 50), CSP reports 1/s (burst 10).
- **Django, per account:** searches, playback starts, TV logins, checkouts, TMDB searches and backup runs, each a `security.rate_*` setting (DRF throttles with counters in redis-cache).
- **Xtream (G-20):** failures pause sign-ins per username and client network (so carrier-grade NAT neighbours aren't punished), per username across networks at 10× the limit, and per IP with doubling pauses up to a day. While paused, a login that succeeded recently still passes from the cache, without Argon2.
- **G-16:** stream-auth and the live relay need the shared secret `X-Edge-Auth` (Docker secret `edge_auth_token`, made by `scripts/secrets.sh`, compared in constant time, failing closed). The edges send it (M15).
- **G-07** library paths resolved and kept inside the root; **G-08** bidirectional controls stripped; **G-09** TMDB images only from `image.tmdb.org`; **G-12** a search-only Meilisearch key derived from the master key; **G-05** grants limited to what the grantor holds; **G-26** *revoke by app*.

### 7. Supply chain and scanning
- `make audit`: pip-audit 2.10.1 (Apache-2.0, through uv) on the exported production lock, and `pnpm audit --prod`. Exceptions only in `scripts/audit-ignore.toml`, each with a reason and an expiry; an expired one fails.
- `make semgrep`: Semgrep CE 1.178.0 (LGPL-2.1, run only as a container, digest-pinned) with **our own rules only** (`.semgrep/rules.yml`, with tests): the project's invariants that Ruff's bandit rules and ESLint can't express. No registry rules (their licence and their noise).
- `make zap`: ZAP 2.17.0's Automation Framework in the `zap-bare` image (Apache-2.0, digest-pinned): fixed requests to every host, a short spider, passive rules, and a failure on any Low-or-higher alert the plan's filters don't explain, each filter with its reason. About 15 seconds, so it runs in `make ci`.
- `renovate.json`: configuration only (weekly groups, releases at least 14 days old, digests pinned, security fixes at once). Nothing runs Renovate automatically ([ADR-0003](0003-no-hosted-ci.md)).

## Alternatives considered
- **A CSP with `'unsafe-inline'` styles:** simpler (sonner, Radix and Tailwind's runtime need styles), but leaves CSS injection open. Nonces cost a patch and two shims.
- **Hashes instead of nonces:** the inline styles change at run time; hashes can't cover them.
- **Step-up by password for admins:** rejected by the owner; a phished password is the threat step-up answers.
- **Allowlist at Traefik:** faster, but it can't refuse a list that locks out the admin making it, and Settings couldn't edit it live.
- **MaxMind GeoLite2:** needs an account and a licence key, and its EULA is stricter; DB-IP Lite needs neither.
- **Automatic suspension on a high score:** spec §11.1 forbids it; households, travel and VPNs make false positives certain.
- **Semgrep registry rules:** noisy here, mostly duplicate Ruff's bandit rules, and come under Semgrep's rules licence.
- **ZAP's full image or active scan:** about 2.3 times the size, and active rules in `make ci` would attack the shared dev stack.

## Consequences
- Existing servers need nothing by hand: `install.sh --update` adds `EDGE_AUTH_TOKEN` and the Docker secret, the worker downloads the GeoIP databases on its first start, and the frontend restarts after web so the CSP gets the media origins. Owners should add a security key and then consider `security.webauthn_required_for`.
- New dependencies: webauthn 3.0.1 (BSD-3; brings cbor2 MIT, pyopenssl Apache-2.0, pyasn1 and pyasn1-modules BSD), maxminddb 3.2.0 (Apache-2.0), mmdb-writer 0.2.7 (MIT, dev; brings netaddr BSD), @simplewebauthn/browser 14.0.0 (MIT). Tools as containers: Semgrep CE (LGPL-2.1), ZAP (Apache-2.0); pip-audit (Apache-2.0) through uv.
- `make ci` gains three steps (about 40 seconds) and two images kept between runs (Semgrep about 430 MB, zap-bare about 505 MB compressed).
- Every new view that changes something must decide whether it needs `step_up_methods`; every new HTML response needs its own CSP (a Semgrep rule checks).
- The sonner patch has to follow sonner upgrades (`frontend/patches/`).
