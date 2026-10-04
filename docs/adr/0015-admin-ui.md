# ADR-0015: The admin UI: aggregates, localised field errors, shortcuts and end-to-end checks

- **Status:** Accepted
- **Date:** 2026-10-04
- **Milestone:** M11

## Context
SPEC §8 and §16 M11 ask for the whole admin SPA:
- every §8 page that has an API;
- both themes, Arabic and English with right-to-left layout;
- keyboard shortcuts and a command palette;
- live views over SSE;
- Playwright journeys and axe checks in the quality gate.

Some pages needed data no API gave yet:
- the dashboard charts and activity feed;
- system health;
- a customer's watch history;
- storage usage.

Validation messages were English strings from DRF and Django, which an Arabic admin can't read. There is no hosted CI (ADR-0003), so the browser checks run in `make ci` on the developer's machine.

## Decision

### 1. Aggregate endpoints (`apps/dashboard`)
They read Postgres with a fixed number of queries (asserted in tests) and are cached 30 s in `redis-cache`. A cache outage only costs speed. Days are calendar days in the admin's time zone (`User.timezone`, falling back to Asia/Riyadh).

| Endpoint | Permission | What it returns |
|---|---|---|
| `admin/dashboard/kpis` | `dashboard.view` | Customer, device, stream, review and transcode counts. |
| `admin/dashboard/timeseries` | `dashboard.view` | Streams over 24 h in 15-minute buckets. Sign-ups against ended access, and plays, per day over 30 days. The top 8 categories and top 10 titles; a series counts its episodes. |
| `admin/dashboard/activity` | `dashboard.view`, `customers.view` with `?customer=`, `audit.view` for diffs | The audit log as a feed of subjects with names, resolved in bulk. With `?customer=` it is also the customer's Activity tab. |
| `admin/health` | `settings.view` | Each service's state, latency or heartbeat age, queue depths (kombu priority queues included), transcoders and their encoders, Redis memory and eviction policy, and Postgres connections. Error details are exception class names only, because messages can carry hosts or secrets. |
| `admin/customers/{id}/history` | `customers.view` | `WatchProgress`, newest first (SPEC §10 `users/{id}/history`). |
| `admin/storage` | `library.view` or `library.manage` | Sources and renditions by library, growth over 90 days and the 10 largest titles. |

Billing figures come from `admin/dashboard/billing` (ADR-0012). The dashboard shows them as their own section to admins with `billing.view`.

How storage usage is counted:
- A rendition linked to its source (`details.linked`) takes no space.
- Growth is rebuilt from row timestamps. Sources run from `created_at` to `removed_at`, renditions from `ready_at`.
- Renditions deleted since are gone from the table, so the rendition curve shows the history of what is on disk today.
- The web container has no media volume, so free space on disk is left to Prometheus (M13).
- The orphan cleanup on the same page uses T1's `admin/renditions/cleanup`. It always runs a dry run first: deleting is enabled only after a dry run that found entries, and asks for confirmation.

### 2. Localised field errors
The problem document gains `field_error_codes`, aligned key for key and item for item with `field_errors`. The field is additive, so older clients keep working.
- Codes come from DRF `ErrorDetail.code` and Django `ValidationError.code`.
- Our own raise sites pass `code=` through `apps.core.errors.field_error`.
- A message without a code reports `invalid`.

The SPA translates a known code with `fieldErrors.<code>` in the UI language. The numbers a message needs (lengths, limits) are taken from the form's own constraints, or else from the API message in order (`FIELD_CODE_PARAMS`). Unknown codes fall back to the API's message. Tests on both sides keep every code we send translated in both languages.

### 3. Keyboard shortcuts and the command palette
- **Command palette:** cmdk, opened with ⌘K or Ctrl+K. It jumps to pages, customers and titles (by API search) and runs actions: create a customer, add an access rule, scan a library, switch theme or language.
- **Go-to sequences:** `g` then a letter. `?` opens the help overlay, built from the same registry.
- **Tables:** `j`/`k` move between rows, Enter opens a row, `/` focuses the search. The review queue keeps `1`–`5` to choose a candidate (SPEC §8.3.7).
- **Rules:** shortcuts never fire while focus is in a field. Buttons carry `aria-keyshortcuts`, and the visible `Kbd` hints inside them are `aria-hidden`.

### 4. Charts and colour tokens
Recharts is wrapped in `@smart-iptv/ui` (`TimeSeriesChart`, `ColumnChart`, `BarList`, `ChartLegend`):
- one accent series and grey for the rest;
- a hairline grid and tabular numbers;
- time runs from the inline start in Arabic;
- labels are HTML beside the chart, because SVG text can't mirror.

Each chart's SVG is `aria-hidden`, with a visually hidden table of the same data. Tokens `--chart-accent` and `--chart-muted` are defined for both themes. A `--primary-hover` token replaces `bg-primary/90`, which failed contrast with white text in the light theme.

### 5. Live views
Live sessions, transcode jobs and scans subscribe to the SSE feeds. When the server refuses a feed (the EventSource closes for good), the hook reopens it after 5 s. Meanwhile the page polls the REST list every 5 s (`FALLBACK_POLL_MS`) and says so in its connection badge.

### 6. End-to-end checks (`make e2e-admin`, a step of `make ci`)
The suite uses Playwright 1.63.0 and @axe-core/playwright 4.13.0 (both Apache-2.0/MPL-2.0, dev only). It drives the host's Chrome against the running dev stack, as `e2e-iptvnator` does.

How the run is set up:
- **Admin account:** the Makefile creates a fresh password per run. `manage.py e2e_admin_account` (DEBUG only, never prints credentials) makes or updates the `e2e-admin` owner. With `--open-review` it reopens the latest decided movie review, with the last choice first, so resolving it keeps the catalogue as it was.
- **Sign-in:** one project signs in with the password and a TOTP code read from `manage.py totp_code`, and saves the session for the other projects.
- **Journeys** (SPEC §15): create a customer with chosen credentials, reset a device password, start a scan, resolve a review with the keyboard, and stop a live session started by a real Xtream play request.

What every run checks:
- **axe:** every page and the customer, movie and series details (every customer tab), the command palette and the help overlay. They run in English with the light theme and in Arabic with the dark theme. A serious or critical violation fails the run. Charts' decorative SVG and sandboxed previews (mail, invoices) are excluded; their content is checked in their own tests.
- **Phone width:** every page and detail at 390 px in Arabic, with no sideways scroll.
- **Translations:** every registry setting has a label in both languages. A row without one is marked `data-untranslated`.

Artifacts (report, traces and screenshots of failures) go to `dist/admin-e2e`.

## Alternatives considered
- **Charts from Prometheus now:** the dashboard would depend on M13's monitoring stack. Postgres already holds sessions and plays, and egress per edge waits for M13.
- **Translating validation messages by matching their English text:** this breaks on every Django or DRF wording change. Codes are stable.
- **Cypress or WebdriverIO:** Playwright is already used for the IPTVnator journey and has first-party axe integration.
- **A headless Chromium download for the suite:** the host's Chrome is already used by `e2e-iptvnator` and keeps the dev machine's disk free.
- **A separate storage service measuring the volume:** the web process must not mount media in production (ADR-0014). Row sizes are recorded when outputs are made.

## Consequences
- Every new admin page needs an entry in `e2e/accessibility.e2e.ts`. Every new setting needs `settings.keys.<key>.label` and `.description` in both locale files, or `make ci` fails.
- New validation raise sites should pass a `code=`. Codes without a translation still show the API's English message.
- Not covered yet:
  - Library, catalog and settings raise sites still report `invalid`.
  - The customer list shows the access profile's status, not the governing subscription's (the list API has no subscription field yet).
  - Egress and edge charts wait for Prometheus (M13).
  - SPEC §8.2 pages without an API (global Devices, Lockouts, Live TV, EPG) are not in the sidebar.
- `make e2e-admin` needs the dev stack up with ready media (`make media-ready`) and Chrome on the host.
