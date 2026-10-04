# ADR-0012: Plans, subscriptions, payments, invoices and notifications (the commercial slice)

- **Status:** Accepted
- **Date:** 2026-10-04
- **Milestone:** M3 (completed), SPEC §7.6 billing, §7.7 notifications

## Context
- The proof of concept gave customers a manual access profile (`accounts.CustomerAccess`, ADR-0006) and deferred everything commercial to this slice: plans, `subscriptions.activate()`, the grace and expiry jobs, renewal maths with property tests, trials, payments, invoices and notifications (plan `m2-m3.md` §3.3, §3.5).
- ADR-0006 promised that subscriptions plug in as a second entitlement source behind `playback.entitlements.build(user)`, so playback, Xtream and the admin need no change.
- SPEC §7.6 fixes the single writer (`activate`), the states and jobs, the provider interface and the webhooks; §7.7 the outbox, the `notify` queue and the ar/en templates; §15 property tests for renewal dates.

## Decision

### 1. Models (`apps/billing/models.py`)
- **`Plan`**: price in integer minor units with its currency, limits, content kinds, categories (M2M, `billing_plan_category`; none means all), trial flags, `sort`, `active` and `version`. Beyond the SPEC field list, a period is `duration_months` + `duration_days`, so monthly plans can renew on calendar months.
- **`Subscription`**: user, plan, `plan_snapshot` (everything the entitlement and invoices read), status (`pending|active|grace|expired|suspended|cancelled`), `starts_at`/`ends_at`, `grace_days` (copied from `billing.grace_days`), source, `trial_identity`, reminder stamps, `ended_at`/`end_reason`.
  - A btree_gist **exclusion constraint** refuses two current (`active`, `grace` or `suspended`) subscriptions of one customer whose `tstzrange(starts_at, ends_at)` overlap. Suspended rows are included, so a suspension can't be dodged by buying a second period.
  - The SPEC's partial index on `status IN ('active','grace')` is there.
- **One row per continuous period of service.** Renewals extend the current row; a customer gets a new row only after a gap. The plan history of a row lives in the audit log.
- **`Invoice`** is numbered only when paid (`PREFIX-YEAR-NNNNNN`, from an `InvoiceSequence` row per year, locked in the payment's transaction). Abandoned checkouts are `void` and never numbered, so numbers have no gaps. A check constraint keeps `subtotal + vat_amount = total`.
- **`Payment`**: provider, `checkout_ref` (Stripe Checkout Session, Moyasar invoice), `provider_ref` (Stripe PaymentIntent, Moyasar payment; unique per provider), amount, status, `refunded_amount`, a unique `idempotency_key`, and the redacted provider payload.
- **`WebhookEvent`**: unique `(provider, event_id)`.
- Financial rows use `PROTECT` foreign keys: money records never disappear with a user or plan.

### 2. `subscriptions.activate(user, plan, *, source, actor, payment, starts_at, ends_at)`: the only way in
- It locks the customer's row, so one customer's changes are serial; the exclusion constraint is the database backstop.
- **With a current subscription**, the row is extended to `max(now, current end) + period` and takes the new plan's snapshot. A plan purchase is effectively an upgrade or downgrade from now, keeping the time left. A suspended one stays suspended: a payment never lifts an admin's suspension.
- **Without one**, a new row starts at `starts_at` (default now). A future start is `pending`, and the state job starts it. `ends_at` lets admins and imports override the computed end; a past period imports as `expired`.
- **Every call** snapshots the plan, audits (`subscription.activate` or `.renew`), schedules the entitlement rebuild after the commit and queues `account_activated` or `subscription_renewed`. A payment sends `payment_succeeded` instead of the renewal notice.
- **Admin changes** go through the same module: `extend` (from `max(now, end)`; an ended row reopens from when access really ended), `change_plan` (dates kept), `cancel`, `suspend`/`resume`, and `approve_trial`. Cancelling sends `access_expired` and suspending sends `access_suspended`, so playback stops sessions through the existing receivers.

### 3. Renewal maths (`renewal.py`; Hypothesis property tests in `tests/test_renewal.py`)
- Periods are added **on the customer's wall clock** (`User.timezone`, default Asia/Riyadh): months first, clamping to the month's end (Jan 31 + 1 month = Feb 28, or Feb 29 in leap years), then calendar days.
- A wall time inside a spring-forward gap moves forward by the gap; an ambiguous one takes its first occurrence (PEP 495 `fold=0`), as java.time does.
- **Properties tested** across ten zones, including Lord Howe's 30-minute shift and Chatham's +12:45:
  - the end is later and in UTC;
  - days keep the wall clock (or move by at most the gap);
  - days last 24 h ± the DST shift;
  - months clamp exactly;
  - later purchases never end more than one DST shift earlier, and are exactly monotonic in UTC;
  - extensions run from `max(now, end)`.
- **Known drift:** renewing monthly from the 31st clamps to the 28th and stays there; plans on days 29–31 drift to shorter months.

### 4. States and jobs (beat, `apps.billing.tasks`)
- **`advance_subscriptions`, every 5 minutes:**
  - starts due `pending` rows. If a current subscription appeared meanwhile, the pending period is added to it and the row closes as `merged`;
  - moves `active` past its end to `grace`;
  - moves `active`/`grace` past `ends_at + grace_days` to `expired`, with an audit entry, an entitlement rebuild, the `access_expired` signal (sessions stop) and the `expired` email;
  - sends the T-7 and T-1 day reminders, once per period: the stamps are cleared on renewal, and the outbox `dedupe_key` holds the end date. A missed T-7 reminder is not sent after the T-1 one. Periods no longer than the window (24-hour trials) get no reminder;
  - publishes `iptv_subscriptions{status}`.

  Rows are locked with `SKIP LOCKED` in batches, each row in its own savepoint: one failing row is logged and the rest go ahead.
- **`expire_checkouts`, hourly:** voids checkouts unpaid after `billing.checkout_ttl_hours`.

### 5. Entitlements: the subscription source (`playback/entitlements.py`)
- **Source rule.** The governing subscription is the current one, else the one that ended last; it sets `source: "subscription"`. Customers whose subscriptions are all `pending` (or who have none) keep their access profile. Once a customer has subscribed, an open-ended profile can no longer give them free access after the subscription ends.
- **The profile does not cap a subscription.** This is the "override or fallback" question ADR-0006 left open: it is the fallback for customers without subscriptions, such as POC customers or ones managed only by hand.
- **Status** comes from the dates, not only the stored status, so an entitlement is right before the 5-minute job catches up. During grace the status stays `active`, with `ends_at` past and `grace_until` ahead; `playback._within_period` already honours that.
- **TTL** is `min(grace_until or ends_at - now, 1 h)`.
- `load_user` costs four queries (it was three). The object's shape is unchanged; `device_limit(user_id)` exposes `max_devices` for whichever source applies.

### 6. Trials
- A trial lasts `trials.duration_hours` (absolute hours), with no grace.
- It is limited per phone number or email: `trials.limit_per_phone`, or the plan's own `trial_limit_per_phone`. Earlier trials are counted by `trial_identity` (the phone, else the email) or by account; rejected requests don't count.
- With `trials.require_approval` (default on, because phone numbers aren't verified), a customer's request is a `pending` trial row that the job never starts. An admin approves it (`/subscriptions/{id}/approve`) or rejects it (cancel). An admin can start a trial directly.

### 7. Payments and providers (`services.py`, `providers/`, `webhooks.py`)
- **The provider interface** (SPEC §7.6): `create_checkout(invoice, payment, return_url)`, `verify_webhook(request)`, `parse_event(event)`, `refund(payment, amount)`, `cancel_recurring(subscription)`.
  - Checkout takes our pending invoice rather than `(user, plan)`, because the provider needs the invoice's VAT-inclusive total and our reference.
  - Every checkout is a one-off payment (renewal is a new checkout), so `cancel_recurring` has nothing to cancel.
- **Manual:** checkout answers with `billing.manual_instructions_{en,ar}` and a reference to quote. An admin records the bank transfer or cash (`POST admin/payments`), against the pending invoice or a plan. A different amount (a discount) is invoiced as received. An `idempotency_key` makes double submits return the first recording.
- **Stripe:** hosted Checkout Sessions over httpx, with no SDK:
  - form-encoded requests, a bearer secret key and an Idempotency-Key;
  - webhooks verified by HMAC-SHA256 over `t.body` with a 300 s tolerance against replays;
  - events used: `checkout.session.completed`/`async_payment_succeeded`/`async_payment_failed`/`expired`, and `charge.refunded` for dashboard refunds.
- **Moyasar:** a hosted invoice (`POST /v1/invoices`, Basic auth with the secret key). Webhooks are checked by a constant-time comparison of the body's `secret_token`. Events used: `payment_paid`, `payment_failed` (and the reference's `payment_faild` spelling), `payment_refunded` and `payment_voided`. The payment source gives the method (mada, Apple Pay, STC Pay, card).
- **When providers are offered.** A provider needs its keys in the environment (`STRIPE_*`, `MOYASAR_*`) and its `billing.<provider>_enabled` setting on. Webhooks need only the keys, so pending checkouts still settle after a provider is switched off.
- **Fixtures.** Tests run behind a transport that fails on any request; the provider tests replay responses shaped from the providers' API references. No sandbox keys were available, so the fixtures were transcribed from the documented objects, not captured.
- **Webhooks** (`POST /api/v1/webhooks/{provider}`, api host only, no session or CSRF):
  1. the signature is verified first (400 `WEBHOOK_INVALID`, nothing stored);
  2. the event is stored once per `(provider, event_id)`, and an event already processed is acknowledged and not applied again;
  3. it is applied with its payment in one transaction and audited (`payment.webhook`).

  A failure keeps the redacted error on the event and answers 500, so the provider retries. A success whose amount or currency differs from the checkout's fails the payment and never activates.
- **Refunds** go through the provider while the payment row is locked, then update the payment and invoice. The admin may also cancel the subscription (`end_reason: refunded`).

### 8. Invoices
- **VAT** (`billing.vat_rate`, 15%) is carved out of the amount paid with Decimal and rounded half up, so `net + vat = total` always (property-tested).
- **`billing.prices_include_vat`** (default on, as Saudi consumer prices are) decides whether plan prices already hold VAT.
- **Snapshots.** Seller (branding names, `billing.vat_number`, `billing.seller_address`) and customer details are copied onto the invoice.
- **The document** is a self-contained, print-ready A4 HTML page in Arabic (RTL) or English, served with a restrictive CSP. Browsers print or save it as PDF.
- **Deviation from SPEC §7.6:** WeasyPrint PDFs (BSD) need Pango and Arabic fonts in the app images, which this slice could not rebuild. PDF storage (`pdf_storage_key`) is left to that follow-up.

### 9. Notifications (`apps/notifications`)
- **The outbox.** `NotificationOutbox` rows are written in the caller's transaction. A Celery task on `notify` makes the first attempt after commit.
- **Retries.** The `notifications-dispatch` beat job (every minute) retries with backoff (1, 5, 15 and 60 minutes, then `failed`) and sends whatever waited for SMTP.
- **Without `EMAIL_HOST`, nothing is sent:** messages stay `queued` and visible in the admin.
- **Payloads are redacted on the way in**, so no secret is ever stored. Each event has a `dedupe_key`.
- **Templates.** There are code defaults in en and ar for every SPEC §7.7 event plus `subscription_renewed`, `payment_refunded`, `password_reset` and `password_invite`. A `NotificationTemplate` row overrides one (event, channel, locale). The admin can edit, switch off, revert, preview with sample values, and send a test to themselves.
- **The sandbox.** Templates are rendered by Django's template engine through `SandboxEngine`, whose only built-ins are a whitelist (`if`, `for`, `with` and a few others; safe filters; no `load`, `include`, `extends`, `url`, `debug`, `safe`, `pprint`, `stringformat`), with no loaders. The context is a read-only deep copy: tuples and read-only mappings, so templates can call no mutating method. Underscore names are refused, sources and subjects are length-limited, and HTML bodies autoescape.
  - **Deviation from SPEC §7.7 (Jinja2 sandbox):** this gives the same guarantees without a second template language or a new runtime dependency.
- **Password links** for the customer API (ADR-0013's `set_password_link_sender`) are rendered and sent at once, never stored. The outbox gets a row without the link for the delivery log, and it can't be resent.
- **Channels.** Email only: Telegram and WhatsApp are later work.
- **Dev mail.** Mailpit (`axllent/mailpit:v1.31.4`, MIT; dep-check 2026-10-04: latest, released 2026-10-03) runs in `docker/compose.dev.yml`, with its UI at `mail.<DOMAIN>`; `dev.py` points SMTP at it.

### 10. APIs, permissions and KPIs
- **Admin** (`/api/v1/admin/`):
  - `plans` (CRUD, `reorder`, `{id}/migrate-subscriptions`);
  - `subscriptions` (list and filters; create through `activate`; `extend`, `change-plan`, `cancel`, `suspend`, `resume`, `approve`; `trial`);
  - `payments` (list; record a manual payment; detail with the webhook timeline; `refund`);
  - `invoices` (list, detail, `document`);
  - `notifications` (log, detail, `retry`);
  - `templates` (list; `{key}/{channel}/{locale}` GET/PUT/DELETE; `preview`; `test`);
  - `dashboard/billing`: subscribers, grace, trials, expiring, MRR (gross and net, per currency), revenue this and last month on the Riyadh calendar, plan mix. Cached 30 s.
- **New RBAC codes:** `billing.view`, `billing.manage`, `notifications.view` and `notifications.manage`. Migration `billing.0002` grants them to existing seed roles: admin all four; support and viewer the two `.view` codes. Refunds keep `billing.refund`; plans and subscriptions keep their codes.
- **Customer** (`customer_api` on api.<domain> with tokens and app.<domain> with the portal session; ADR-0013's authentication):
  - `GET plans` (public);
  - `GET payment-providers`;
  - `POST checkout` (`redirect`, `manual` or `trial`);
  - `GET me/subscription` (the governing one and any waiting one);
  - `GET me/invoices` and `me/invoices/{id}/document`.

  The provider's return URL must be a portal page.
- **New error codes:** `TRIAL_NOT_ELIGIBLE` (409), `PAYMENT_PROVIDER_UNAVAILABLE` (503), `PAYMENT_PROVIDER_ERROR` (502), `WEBHOOK_INVALID` (400).

## Alternatives considered
- **A new row per renewal.** Keeps a row per purchase but makes "current subscription" a range query, fights the exclusion constraint on early renewals, and duplicates what the audit log and payments already record.
- **Queueing a different-plan purchase after the current period.** The customer would pay for Premium and keep Basic for weeks. Switching now and keeping the time left is what customers expect.
- **Numbering invoices at checkout.** Abandoned checkouts would leave holes or void numbers in a sequence that Saudi e-invoicing expects to be continuous.
- **The Stripe SDK.** It is MIT, but a few form posts and an HMAC check are easier to audit and to replay in tests, and the Moyasar adapter needs httpx anyway.
- **Jinja2's `SandboxedEnvironment`.** It is the SPEC's choice and a fine one. It would add a runtime dependency and a second template syntax for the same guarantees (section 9).
- **The access profile capping the subscription** (the lower of the two limits). It would make a subscriber's rights depend on a form most admins never see, and a POC profile's defaults (open-ended, 1 stream) would silently cap paid plans.

## Consequences
- **Consumer follow-ups.** No consumer was changed, so these need their owners:
  - **Xtream (`apps/xtream_api/auth.py` `status_of`):** shows `Expired` during grace, because it compares only `ends_at`, and its catalog actions answer empty then, although playback still allows grace. It should treat `grace_until` as playback does, and could show `is_trial` (the entitlement doesn't carry it yet).
  - **Device limit (accounts `_issue_device`, C1's `me/devices`):** still reads `CustomerAccess.max_devices`. Use `entitlements.device_limit(user.pk)`.
  - **The accounts expiry job:** still publishes `iptv_subscriptions` from profiles every 5 minutes, overwriting this slice's gauge. Its `access_expired` signal would also stop the sessions of a subscriber whose old profile date passes. It should skip customers governed by a subscription, and stop publishing the gauge.
  - **The admin customer list status** and the dashboard's customer KPIs still read the profile.
- **Self-service sign-up (ADR-0013)** must not give new customers an open-ended `CustomerAccess`: until they subscribe, that profile is their entitlement.
- **Later work:**
  - WeasyPrint PDF invoices once the images carry Pango and Arabic fonts;
  - ZATCA e-invoicing (the QR code and XML) for production in Saudi Arabia;
  - credit notes for refunds;
  - Telegram and WhatsApp channels;
  - PayPal, HyperPay and Tap adapters;
  - re-authentication before refunds (SPEC §8.3, with WebAuthn in M14).
- **Before going live:** run the Stripe and Moyasar adapters once against their sandboxes with real test keys, to replace the transcribed fixtures with captured ones.
