# ADR-0024: Visual regression, byte-based sharing flags, setup cards, customer two-factor sign-in, TheTVDB orders for TMDB series (V1)

- **Status:** Accepted
- **Date:** 2026-10-16
- **Milestone:** V1 (the last open items: SPEC §15's visual regression; threat-model gaps G-17 and G-25; customer TOTP; TheTVDB orders for TMDB-matched series)

## Context
Five items were still open after Q1 (ADR-0023):
- **SPEC §15** asks for visual regression on key pages in both themes and both directions. The E2E suites run axe and fit checks, but they don't compare pixels.
- **G-17** was half fixed. Every edge throttles a connection after a 16 MB burst (ADR-0022), but nothing flagged sessions whose bytes far exceed what the title explains. ADR-0007 had noted why: stream-auth runs before the response, so the edge can't send a byte count there.
  - The live relay sends `X-Bytes` for its own streams.
  - For movies and episodes, `PlaybackSession.bytes_sent` stayed at 0.
- **G-25:** QR codes are drawn in the browser, but there was no printable setup card.
- **Customer TOTP** was in the SPEC (§9, optional) and in the threat model (PT-1, AC-10), but had no API.
- **TheTVDB's DVD and absolute orders** applied only to series built from TheTVDB. A TMDB series whose files are named after the DVDs or with absolute numbers couldn't be placed.

The rules stay:
- no GPL code imported;
- media requests never touch Postgres;
- settings live in the admin with en/ar labels;
- sensitive changes need a step-up;
- both SPAs are CSP-clean.

## Decision

### 1. Visual regression (`make visual`)
- **Suites:** Playwright with the host's Chrome, like the E2E suites, under each app's `e2e/visual/`. Admin pages: dashboard, customers, a customer, a movie, live TV, settings. Portal pages: sign-in, home, a title, the player's chrome, account, live TV.
- **Variants:** English (light) and Arabic (dark, right to left), each at 1440 px and at 390 px. That makes 48 baselines, committed in `e2e/visual/baselines/` (about 7 MB in all).
- **Fixtures instead of live data.** Every `/api/v1/` request is answered from JSON fixtures recorded once from the dev stack (`make visual RECORD=1`).
  - Each response is keyed by method, path, sorted query and the language the app asked for.
  - A request the fixtures can't answer fails the test, so API drift is visible.
  - Recording rewrites signed media URLs (`/v/<token>/` becomes `/v/visual-fixture/`), so no token is committed.
  - A playback grant is replayed as progressive MP4 of a 4 KB synthetic clip (`e2e/visual/media/sample.mp4`).
  - Server-sent event streams replay their first recorded events, then wait a day before reconnecting.
  - Images are a neutral placeholder, and any other host is refused.
  - The only live part is the SPA served by the dev stack.
- **Determinism:**
  - The clock is fixed at the recording's time (`page.clock.setFixedTime`).
  - Animations are off and motion is reduced.
  - Before a screenshot, every lazy row is mounted (scroll to the end and back), every image is loaded eagerly, and the page waits for fonts.
  - Relative times, live durations, avatars and `[data-visual-mask]` are masked. Video frames are hidden through CSSOM rather than masked, so the player's controls stay in the picture.
  - The threshold is 0.1% of pixels, to absorb Chrome's anti-aliasing changes.
  - Settings is over 20,000 px tall, so only its first screen is compared.
- **Gate:** three consecutive runs of `make visual` passed with no difference, so the target is in `make ci` after the E2E suites. Rewrite baselines with `make visual UPDATE=1`, and re-record after an API change with `RECORD=1`.
- **First catch:** the dashboard scrolled sideways at 390 px in English. A chart's screen-reader table ignored the 1 px width of `sr-only`. The table now sits in an `sr-only` wrapper (`@smart-iptv/ui` charts). The Arabic fit check couldn't see it, because overflow to the left doesn't scroll.

### 2. Sessions served too many bytes (G-17's second half)
- **Counting at the edge (njs, `bytes.js`).** `$edge_bytes_note` is referenced only by a second access log (`edge_bytes`, written to `/dev/null`), so it runs once at the end of every token request.
  - A 200 or 206 adds its `$bytes_sent` to the session in the shared dictionary `edge_bytes` (4 MB, entries kept at most an hour, the oldest evicted when full).
  - Live and catch-up tokens are skipped: the relay reports their bytes itself.
- **Reporting.**
  - The local edge: worker 0 (`js_periodic`, every 15 s) posts `{"sessions": {"<session>": bytes}}` to `POST /internal/stream-bytes`, with `X-Edge-Auth` and stream-auth's Host.
  - A standalone edge: the same object goes as `session_bytes` in its control poll (every 15 s).
  - A report that fails goes back into the dictionary for the next attempt. Reports hold at most 2,000 sessions.
- **Django (Redis only, `apps.playback.bytes`).** A Lua script adds each count to the session's live record (`sess:<session>`, field `bytes`, which the sweeper already copies into `PlaybackSession.bytes_sent`).
  - A session whose record is gone goes to `playback:late-bytes` instead. This happens to a file pulled in one long request, which is counted only when the request ends.
  - The sweeper adds late bytes to the closed row.
- **The check (`apps.antisharing.bytes`, from `score_recent`, every 5 minutes).** For each movie or episode session of the last day:

  `expected = runtime × bitrate / 8`

  - The runtime is the renditions' duration, else `runtime_min` (an episode falls back to the series' run time).
  - The bitrate is the highest of the renditions of the kind the session played (compat: the compat MP4s; hls: every rung; source; uhd), else of all the title's renditions.
  - A session at `antisharing.bytes_ratio` times that or more (default 4.0), served at least `antisharing.bytes_min_mb` (default 500), gets an `ExcessBytes` row. The row keeps the session, device, title, rendition, network, bytes, expected bytes, ratio and first detection, and it is updated while the session grows.
  - Each row of the last 24 hours counts in a new score signal, `excess_bytes_24h`, worth 4 points (a flag on its own at the default `flag_score`).
- **Outcome:** a flag for the admins, with the usual notification. Nothing is locked. The customer's Security tab lists the sessions (served, explained, ratio, network, detected). Rows follow `privacy.antisharing_retention_days`.

### 3. Setup cards (G-25)
- **Where:** a **Print setup card** button and an **Include the password** box on the one-time credential reveal, in the admin (create, add, reset) and in the portal (Add TV app, new password). The component is `PrintSetupCard` in `@smart-iptv/ui`.
- **How it prints:** the card is drawn in a root at the end of `<body>`, hidden on screen. While printing, `<html class="printing-setup-card">` and the print stylesheet show the card alone, black on white, in the page's language and direction. The browser's dialog prints it or saves it as a PDF, and the card is removed on `afterprint`.
- **Contents:**
  - the service name, the device name, the server, the username, and the password when the box is ticked;
  - a QR code holding the same JSON as the reveal's, with the password only when it is printed;
  - three setup steps and a warning to keep the card private.
- **No new dependency:** QR codes use `qrcode.react`, which the apps already have.
- **Nothing on the server:** nothing is sent, stored or logged. The card exists only while the password is on the page, and the button is disabled once it is hidden.

### 4. Customer two-factor sign-in (optional)
- **Storage:** the same `MfaTotp` table as admins (Fernet-encrypted secret, `last_used_step`, so a code works once) and `User.mfa_enabled`.
- **Portal account page** (`me/mfa`), each step after the password step-up:
  - `POST` makes a new secret waiting for its first code and returns the `otpauth://` URI, shown as a QR code plus the key;
  - `POST me/mfa/confirm {code}` switches it on;
  - `DELETE` switches it off.
  - Audit actions are `customer.mfa_enable` and `customer.mfa_disable`.
- **Signing in** (portal session and API host tokens alike):
  - the sign-in body takes an optional `code`;
  - for an enrolled customer, a right password without a code gets 403 `MFA_REQUIRED`, and the portal shows a code step;
  - a wrong code is 400 `MFA_INVALID`. It counts in django-axes and in the account's `mfa:fail:<id>` counter (10 in 15 minutes, the admins' limit), which the Lockouts page shows and clears.
- **No pending server state:** the code goes with the password in one request, so the API host's bearer sign-in needs nothing new.
- **Existing sign-ins:** token families and sessions issued before it was switched on keep working until they end. Refreshing never asks for a code.
- **Recovery through support:** on the customer's Security tab, an admin with `customers.edit` uses **Switch two-factor off**, after a code step-up. It is audited as `customer.mfa_reset`, and the next sign-in needs the password alone.
- **Tooling:**
  - `e2e_portal_account` switches it off between runs;
  - `totp_code` serves customers too, so the portal journey sets it up, signs in with a code and switches it off.

### 5. TheTVDB's DVD or absolute order for TMDB series
- **New value:** `episode_ordering` gains `tvdb_absolute` (migration `catalog/0006`).
- **Which orders a series may use:**
  - a series built from TheTVDB keeps `tvdb_aired` or `tvdb_dvd`;
  - a TMDB series keeps `tmdb_default`, or, when TMDB names its TheTVDB id, may read file names in `tvdb_dvd` or `tvdb_absolute`;
  - any other combination is refused with `ordering_source` or `ordering_needs_tvdb_id`.
- **Placement (`apps.metadata.tvdb_order`):** a file's numbers are looked up in TheTVDB's chosen order. TheTVDB's episode id then gives the aired (`official`) numbers, which TMDB follows, and the file is linked to those TMDB episodes:
  - an absolute name (`- 012`), or season 1 under the absolute order, is an absolute number;
  - a multi-episode file may span two aired seasons.
- **Failures:** without a TheTVDB key, or when TheTVDB lacks the episode, the file goes to the review queue (`tvdb_unavailable`, `episode_not_found`).
- **Changing the order:** for a TMDB series, a change queues `replace_series_files`, which places every file of the series again from its parsed name. A TheTVDB series is still fetched again.
- **Admin:** the series page offers the choices that apply.
- **Tests:** synthetic TheTVDB fixtures for Breaking Bad's official, DVD (invented) and absolute orders.

## Alternatives considered
- **Visual regression against live dev data, with masks.** The data changes with every E2E run, and other suites create customers and sessions, so the baselines would never hold. Hand-written fixtures typed with the Orval models would be safer against drift but much slower to build for 12 pages. Playwright's HAR replay records headers, cookies included, and is harder to sanitise and review than plain JSON.
- **Bytes in stream-auth's `X-Bytes`.** The auth subrequest runs before the response and is cached per token. A whole file pulled in one request would never be reported.
- **Bytes from the access log.** This needs a log shipper on every edge (Alloy runs only with the monitoring overlay), and the log names sessions by a 16-hex prefix.
- **Counting in a body filter.** It would run njs on every chunk and turn off sendfile for media.
- **Bytes as a `Sighting`.** Sightings are networks per hour. A session's excess is a different kind of evidence, so it gets its own model and its own signal.
- **A setup-card PDF built on the server, or a PDF library in the browser** (pdf-lib, jsPDF). The server must never see the password (G-25). A library adds weight and fonts for Arabic shaping, while the browser's print-to-PDF already shapes Arabic and honours the page's direction.
- **A pending second step in the session for customer sign-ins** (the admins' pattern). The API host has no session; one request with the code works on both hosts.
- **TheTVDB's order rewriting a TMDB series' episodes,** as it does for TheTVDB series. That would break TMDB's metadata, images and the Xtream ids; mapping the files keeps TMDB as the source.

## Consequences
- **Visual baselines and fixtures:**
  - they belong to the machine's Chrome. A new Chrome build that moves text by more than the threshold needs `make visual UPDATE=1`, and a new admin or customer API field on those pages needs `RECORD=1`;
  - recording needs the dev stack with `media-ready` and `live-ready`;
  - the fixtures are dev data (end-to-end accounts, sample titles), never production.
- **Byte reports:**
  - they add one small POST every 15 s per edge with traffic;
  - counts unreported for an hour (control plane down) are lost;
  - a ripping session shows up once its request ends;
  - HLS live segments served straight from the edge aren't counted (live isn't checked against a runtime).
- **TOTP:** customers with TOTP must send the code from every app on its next sign-in. Third-party apps on the API host that predate this get 403 `MFA_REQUIRED` and must add a code field.
- **TheTVDB orders:** a TMDB series on a TheTVDB order needs a TheTVDB key to place new files. TMDB and TheTVDB may disagree on aired order for odd series (split or merged episodes); those files go to review.
- **ESLint:** `make lint` and `make fmt` give type-aware ESLint 2.5 GB of heap. Every package's TypeScript program together now needs about 1.8 GB, and Node's default in the 3 GB frontend container is 1.7 GB.
