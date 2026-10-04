# ADR-0016: The customer portal: Shaka player, progress, RTL media controls and the browser journey

- **Status:** Accepted
- **Date:** 2026-10-04
- **Milestone:** M11b (SPEC §9; the API is ADR-0013, billing ADR-0012, HLS and tracks ADR-0014)

## Context
SPEC §9 asks for a streaming-service portal on `app.<domain>`:
- dark by default, Arabic RTL first, responsive from 360 px;
- sign-in, home rows, browse, search, title pages;
- a Shaka player with thumbnails, tracks and resume;
- favourites, history, devices and TV apps, the subscription, invoices and checkout.

The customer API (ADR-0013) is done, with its generated client in `frontend/packages/api-portal`. Several things shape the UI:
- **Playback grants.** `playback/start` returns a signed URL: HLS when `prefer=hls` and the ladder exists, else the compat MP4. It also returns the resume position, the audio tracks, the WebVTT subtitles and the thumbnail sprite map, all under the token's scope.
- **Progress** goes to the API, never through the edge: about every 15 s, on pause, and a final `stop`. Reports closer than 5 s are dropped.
- **Media controls in Arabic.** Material Design's bidirectionality guidance keeps playback timelines and transport buttons left to right in RTL interfaces.
- **The sample media is 30 s long.** The engagement service only resumed or showed "continue watching" past 30 s, below the 90 % watched ratio. A 30 s clip therefore never reached continue watching, which the acceptance journey checks.

## Decision

### 1. App structure (`frontend/apps/portal`)
- **Routes.** TanStack Router routes are code-split per page:
  - the signed-in area sits behind a guard that loads `GET me`;
  - the player (`/watch/movie/$id`, `/watch/series/$id?episode=`) is a full-screen route outside the navigation;
  - the signed-out routes are `/login`, `/forgot-password` and `/reset-password`. The last handles the reset links and the admin's invitations (`&welcome=1`, ADR-0013 §3).
- **Session expiry.** Any `NOT_AUTHENTICATED` from a query or mutation sends the customer to `/login?redirect=…&expired=true` and clears the query cache.
- **The generated client only.** Every documented endpoint goes through `@smart-iptv/api-portal`'s functions and hooks.
  - Infinite lists (movies, series, watch history) use `useInfiniteQuery` over the generated list functions, with the cursor taken from the `next` link.
  - Invoices open and print `getMeInvoiceDocumentUrl(...)`. The document's `frame-ancestors 'self'` allows printing it from a same-origin iframe.
  - The only hand-written fetch is `thumbs.vtt` on the media edge, which is not an API endpoint.
- **Layout.**
  - A fixed, translucent top bar sits over full-bleed heroes, with a tab bar on phones.
  - Rows are horizontal scroll-snap rails. Rows below the fold mount as they come near (IntersectionObserver), which is how SPEC §9 "rows are virtualized" is met.
  - Rails are `position: relative`, so the poster cards' absolutely positioned screen-reader text scrolls with them. Without it, the page widened at 390 px.
  - Artwork uses the kit's `PosterImage`/`BackdropImage` with a resolver over the API's per-size WebP/AVIF URLs and the blurhash.
- **Bidirectional text.**
  - Logical properties only.
  - Directional icons are mirrored; play icons are not.
  - Latin values inside Arabic text are isolated (`<bdi>`, `dir="ltr"` on codes and URLs).
- **Dates** use the customer's own time zone (`me.timezone`).

### 2. The player (`features/player`)
- **Shaka.** Shaka Player 5.2.12 (Apache-2.0), HLS build (`shaka-player.hls-es2021`), dynamically imported, so its ~520 kB chunk loads with the player only.
- **Controls.** Our own controls, not Shaka's UI, so the player follows the design system, i18n and RTL. They are:
  - play/pause, ±10 s, a seek bar with sprite previews, volume, time;
  - a settings panel (quality, audio, subtitles with size and background, speed);
  - captions, picture-in-picture, full screen, next episode.

  The timeline and transport stay left to right in Arabic. The settings panel lives inside the player element, so it works in full screen.
- **Quality.** The quality menu lists the heights Shaka exposes. They are already within the plan's ceiling, because the token's presentation holds only allowed rungs (ADR-0014 §3). "Auto" re-enables ABR.
- **MP4 fallback.** If the browser can't play HLS, or the HLS load fails for any reason other than a refused token, the page asks again with `prefer=mp4` from the same position. When the API returns the progressive MP4 directly, it is played as is.
- **Subtitles.**
  - Shaka's `UITextDisplayer` draws them inside the player, styled by CSS variables (size and background, remembered per browser).
  - Each line takes its direction from its text (`unicode-bidi: plaintext`), so Arabic lines with Latin words read correctly.
  - When the HLS master lists no subtitles, the grant's signed WebVTT files are added.
- **Trickplay.** A small WebVTT sprite-map parser (`thumbnails.ts`) reads `#xywh=` cues. It works the same for HLS and MP4 and is unit-tested; Shaka's thumbnail tracks are tied to its streaming modes.
- **Keyboard.**
  - space or k: play/pause;
  - ←/→ or j/l: 10 s back and forward;
  - ↑/↓: volume;
  - f, m, c: full screen, mute, captions;
  - 0–9: jump to a tenth of the title.

  A focused slider or button keeps its own keys.
- **Resume.** A prompt ("Resume from 12:34" or "Start from the beginning") appears when the grant has a resume position. `?restart` skips it.
- **Errors.** Every §7.4 code maps to a message and an action (`playback-errors.ts`):
  - `SUBSCRIPTION_EXPIRED` → renew;
  - `CONCURRENCY_LIMIT` → manage devices, or try again;
  - `TITLE_PREPARING` → try again shortly;
  - `QUALITY_NOT_ALLOWED`, `CATEGORY_NOT_ALLOWED`, `CONTENT_TYPE_NOT_ALLOWED` → plans;
  - a segment refused mid-stream (401/403: kicked, or the link expired) → "Playback stopped", and "Try again" asks for a new grant at the same position.

### 3. Progress (`progress.ts`)
- **Reporting.** `ProgressReporter` reports at once, then every 15 s while playing. It also reports on pause, at the end and when the page is hidden, unless it reported less than 5 s before, since the API would drop it.
- **Stopping.** Leaving stops the session once, with the final position and `keepalive`, so the request survives navigation and `pagehide`.
- **Remounts.** An unmount schedules the stop on a zero-delay timer, and a remount with the same session cancels it. Without this, React's StrictMode remount, or any quick re-render, would end the stream.
- **The last position** is kept in a ref, because the video element is already detached when the effect cleans up.
- **Refreshing.** After the stop, every query that shows progress is invalidated: home, continue watching, history, title pages.

### 4. Account, devices and billing
- **Profile.**
  - Name, language (which also switches the UI), time zone and marketing email.
  - There is no password-change endpoint, so "Password" sends the reset link by email (`auth/password/forgot` with the customer's username).
- **Devices & TV apps.**
  - The list separates IPTV logins from browsers and apps.
  - "Add TV app" offers generated or chosen credentials, checked with the API's rules. The login is shown once, with copy buttons and a QR code of `{"server","username","password"}` (the admin's rule). Hiding the password removes the QR code too.
  - Each app gets a setup guide, and MAC-activated apps (IBO Player, SmartOne) get a warning.
  - Devices can be renamed, given a new login, or removed.
- **Subscription.**
  - The governing subscription, or the access profile for customers managed by hand, plus any waiting renewal.
  - A banner on every page during grace, after expiry and when suspended.
  - Invoices to view or print, as a table from `sm` up and as cards on phones.
- **Plans.** Plans and `POST checkout`:
  - `manual` shows the instructions in the UI language, the amount and the reference;
  - a provider redirects, and is listed only when `GET payment-providers` lists it;
  - trial plans start or request a trial.

### 5. Backend changes
- **`manage.py e2e_portal_account`** (DEBUG only, idempotent, tested) creates or resets the journey's customer from `E2E_PORTAL_USER`/`E2E_PORTAL_PASS`:
  - an open access profile with three streams;
  - an empty history, favourites and ratings;
  - axes failures cleared.
- **"Started" scales with short titles** (`engagement.services.started`). A position counts as started from `min(30 s, 10 % of the duration)`, for resume and continue watching alike. Real films and episodes still need 30 s; a 30 s clip resumes from 3 s.

### 6. The browser journey (`e2e/`, `make e2e-portal`)
- **Setup.** `@playwright/test` 1.63.0 (as compat and the admin suite) drives the host's Chrome against the dev stack; the bundled Chromium has no H.264/AAC. `make e2e-portal` creates the customer with a fresh random password (never stored) and runs in `ci-steps` after `e2e-admin`.
- **The journey:**
  1. sign in;
  2. home;
  3. search «المصفوفة»;
  4. open The Matrix;
  5. play for more than 5 s and leave (a 204 stop);
  6. find "Resume" on the title and The Matrix in Continue watching;
  7. sign out; the guard then sends the customer back to sign-in.
- **Accessibility and width.** axe (WCAG 2.2 AA tags) runs on the sign-in, home, search, title and player screens. Two more runs cover ten pages in Arabic/light and English/dark at 390 px. Each asserts there is no horizontal scroll.

## Alternatives considered
- **Shaka's own UI (`shaka-player.ui`).** Its controls and locale files don't follow our design system or ar/en messages, it is ~600 kB more, and its overflow menus fight RTL.
- **hls.js or video.js.** The SPEC names Shaka. Shaka also covers the HLS fMP4 ladder with WebVTT groups, the MP4 `src=` mode and a future DASH from the same segments.
- **Shaka's `addThumbnailsTrack`.** It is tied to the manifest or `src=` mode. A 60-line parser is simpler and testable.
- **Mirroring the timeline in Arabic.** Bidi guidance keeps media timelines and transport controls left to right, as the time they show runs.
- **Python Playwright for the journey, like `compat/iptvnator_e2e.py`.** The admin suite already uses `@playwright/test` and `@axe-core/playwright` (in the lockfile), and a TypeScript journey is linted and type-checked with the app.
- **Leaving the 30 s rule and seeking the clip past 30 s in the journey.** A 30 s clip would then be complete at 27 s and never "continue": the rule had to scale.

## Consequences
- **What the API lacks**, each a follow-up for the API's owner:
  - `TITLE_PREPARING` carries no ETA. The player says "usually ready within a few minutes"; an `eta_s` field on the problem would let it show one.
  - Customers can't list or stop their own active streams. `CONCURRENCY_LIMIT` offers "Manage devices" (removing a browser signs it out) and "Try again".
  - There is no `me/password` change endpoint; the email reset link stands in.
  - The OpenAPI schema types `RatingRequest.value` as non-null, while the API accepts `null` to clear a thumb. The UI sends `null` through a typed cast.
  - Customer TOTP (SPEC §9, "optional") has no API yet.
- **Device clutter.** Every sign-in creates a web device at its first playback (ADR-0013 §4), so the browser list grows until those sessions sign out.
- **Not built:** D-pad spatial navigation for TV browsers (SPEC §9's progressive enhancement), and notification preferences beyond the marketing opt-in (the API has no others).
- **Media session.** `pagehide` stops the session. A page restored from the back/forward cache shows "Playback stopped" on its next play, and "Try again" asks for a new grant.
