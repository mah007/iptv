# ADR-0006: Accounts and authentication: admin sign-in with TOTP, RBAC, Argon2id device credentials, manual access profiles

- **Status:** Accepted
- **Date:** 2026-10-03
- **Milestone:** M3 (M3-lite: proof-of-concept scope)

## Context
- SPEC §11 sets the security bar:
  - admins sign in on their own host with mandatory MFA (TOTP now, WebAuthn later), lockouts that are never permanent, and an idle timeout;
  - Xtream secrets are hashed with Argon2id and checked in constant time, and a login must stay under 150 ms at p95;
  - every admin mutation is audited.
- SPEC §6 and §8.3 need fine-grained permissions (`customers.view`, `settings.edit`, …) grouped into roles that owners can edit.
- The owner moved everything commercial (plans, subscriptions, payments, trials, notifications) to the final slice (plan `m2-m3.md`, "Scope change"). The proof of concept still needs a customer's playback rights: how long, how many streams and devices, which quality, which content.
- ADR-0004 fixed the transport: a same-origin session cookie plus CSRF on `admin.<domain>/api`.

## Decision

### 1. Admin sign-in (`apps/accounts/auth.py`, `api_auth.py`; `/api/v1/auth/*` on the admin host)
- **Two steps; a session exists only after both.**
  1. `POST auth/login {login, password}`, where `login` is a username or an email (matched case-insensitively). Django's `authenticate` checks it, behind django-axes.
     - A correct password does **not** sign in. The session (with a new key) only stores the pending user id, a stage and a timestamp, valid for 5 minutes.
     - Admins without a confirmed authenticator get `{status: "mfa_setup_required", otpauth_uri}`; the others get `{status: "mfa_required"}`.
  2. `POST auth/mfa/verify {code}` checks the TOTP code. Only then is the session logged in: `login()` rotates the key, and `security.admin_idle_timeout_min` (default 30) becomes the idle expiry. Each request renews it (`SESSION_SAVE_EVERY_REQUEST`). The response is `me`.
- **Other endpoints:**
  - `GET auth/csrf` sets the `csrftoken` cookie (204);
  - `GET auth/me` returns the admin, their role names and permission codes;
  - `POST auth/logout` works with or without a session, so it also clears a half-finished sign-in.
- **CSRF on anonymous requests.** DRF only checks CSRF for authenticated sessions, so login and MFA verification run the same check themselves. A cross-site page cannot drive the sign-in flow.
- **One error for all bad sign-ins.** Wrong passwords, unknown users, customers (non-staff) and suspended or disabled admins all get the same `INVALID_CREDENTIALS` with the same text. The error codes used are `ACCOUNT_LOCKED` (429, with `Retry-After`), `MFA_INVALID`, and `NOT_AUTHENTICATED` when step one is missing or stale.
- **Lockouts with django-axes (MIT).**
  - Failures are counted per **username + client IP**. Both wrong passwords and wrong codes count.
  - After 5 failures the pair is locked for 15 minutes. Each further failure doubles the lock, up to 24 hours (`apps/accounts/lockout.py`).
  - A record expires one cool-off after the last failure, so a lockout is never permanent. A successful sign-in resets the count.
  - The client IP is the one Traefik saw (`apps.core.http.client_ip`), never a header the client chose.
  - `make seed args=--reset-admin-password` also clears the demo admin's lockouts.
- **The audit log records** `auth.login`, `auth.mfa_enroll` and `auth.logout`. Failures stay in axes' table; we don't duplicate them.

### 2. TOTP now, WebAuthn in M14
- **The code (`mfa.py`, pyotp, MIT):** RFC 6238, six digits, 30-second steps, one step of drift either way.
  - All three steps in the window are compared in constant time.
  - `last_used_step` is stored, so a code (and every older step) is never accepted twice.
- **Enrolment.** Every enrolment attempt makes a new secret, so an `otpauth://` URI seen once is useless later. Confirming the first code sets `confirmed_at` and `mfa_enabled`.
- **The secret at rest:**
  - `MfaTotp.secret_encrypted` is Fernet (`cryptography`, Apache-2.0/BSD) under `FIELD_ENCRYPTION_KEY`;
  - the setting holds comma-separated keys, newest first, so keys can rotate (`crypto.rotate`);
  - the system check `accounts.E001` refuses to start with a missing or malformed key;
  - `scripts/secrets.sh` generates the key (`__GENERATE_FERNET__`).
- **Recovery and testing:**
  - `make seed args=--reset-admin-mfa` removes the demo admin's authenticator (audited `admin.mfa_reset`);
  - `manage.py totp_code <username>` prints the next acceptable code for end-to-end tests. It refuses to run unless `DEBUG` is on.
- **WebAuthn arrives in M14** as a second factor type next to `MfaTotp`. The flow above already has the stage and status fields it needs (`mfa_required` will list the methods), so the API shape stays the same.

### 3. RBAC (`apps/accounts/rbac.py`, `permissions.py`)
- **Permissions and roles.**
  - `Permission(code, description)` and `Role(name, description, permissions)`, with `User.roles`. These are our own tables, not `django.contrib.auth`'s.
  - The catalogue of 16 codes and the default grants of the five seed roles live in code. They are written by a data migration and by `seed_demo` through `sync_rbac`, which only adds what is missing, so an owner's edits survive.
  - Seed roles: `owner` (everything), `admin` (everything except roles and admins), `support` (customers, devices, sessions, subscriptions), `content_manager` (dashboard, library), `viewer` (every `.view`).
- **`HasPermission`** guards every admin endpoint. Views declare `required_permissions = {"GET": "customers.view", "POST": "customers.edit"}`; a tuple means any one of the codes is enough.
  - A method the mapping leaves out is refused: it fails closed.
  - A method the view doesn't implement answers 405.
  - Non-staff users and inactive admins hold no permissions.
  - The owner role always holds every code, whatever its rows say.
  - Codes are resolved once per request with one query.
- **Guards:** only owners grant or remove the owner role; nobody disables their own account; one active owner always remains; the owner role cannot be edited or deleted; a role still held by an admin cannot be deleted.

### 4. Xtream device credentials (`apps/accounts/credentials.py`, `services.py`)
- **Username:** `<3 letters of the name>-<6 lowercase base32>`, e.g. `sar-k3p9qa`. It is unique, and the insert retries on the rare clash.
- **Password:** 16 characters from `23456789abcdefghjkmnpqrstuvwxyz`, which leaves out 0/o and 1/l/i (about 79 bits). It is easy to type with a TV remote.
- **Plaintext exists only in the create or reset response,** which is sent with `Cache-Control: no-store`. It is never audited or logged.
- **Argon2id (argon2-cffi, MIT)** with OWASP's first recommended set: `m=19 MiB, t=2, p=1`.
  - **Measured verify latency** (`apps/accounts/tests/test_secrets.py::test_verify_latency_p95_is_within_budget`, 40 samples in the dev `web` container on the reference host): **p50 = 15.1 ms, p95 = 16.7 ms**, well under the 150 ms budget, which leaves room for the database lookup and Redis.
  - The test fails if p95 reaches 150 ms.
  - Hashes made with weaker parameters are upgraded on the next successful login.
- **`authenticate_xtream` (used by the Xtream API from M5):**
  - one query loads the credential, device and customer;
  - a success is cached in redis-state for 5 minutes under `xauth:<HMAC-SHA256(SECRET_KEY, username, password)>`, so neither value reaches Redis;
  - the cached value is tied to a digest of the stored hash, so a reset or revoke takes effect at once;
  - an unknown username still pays for one verification, so response time doesn't reveal which usernames exist;
  - revoked devices are refused. Blocked devices and inactive accounts are left to playback (M7), which answers with its own error codes.
- **Device limit.** Adding a device beyond the profile's `max_devices` gives `DEVICE_LIMIT` (409). Revoked devices stay for history and free their slot.

### 5. The manual access profile replaces plans and subscriptions for now
- **`accounts.CustomerAccess`** is 1:1 with the customer, created with them:
  - `expires_at`: null means no end;
  - `max_streams` (1) and `max_devices` (2), each 1–50, with database check constraints;
  - `max_quality` (480/720/1080/2160, default 1080);
  - `concurrency_policy` (`reject` or `kick_oldest`);
  - `allow_movies`, `allow_series`, `allow_live`;
  - `categories`: M2M to `catalog.Category`, where empty means all.
- **`playback.entitlements.build(user)`** derives the SPEC §7.4 entitlement object from the profile:
  - **status order:** a disabled or suspended account comes first, then the access period (`active` or `expired`). This matches SPEC §7.4 checks 1 and 2;
  - **`categories`:** null means all;
  - **rules:** the customer's own unexpired access rules, split into `ip_rules` and `country_rules`. Global rules are checked by playback directly, so changing one never rewrites every entitlement;
  - **fixed for now:** `grace_until` is null and `allow_download` is false until subscriptions exist;
  - **version fields:** the object also carries `v` and `source: "access_profile"`, so a consumer can tell versions and sources apart.
- **Caching.**
  - `refresh(user_id)` writes `ent:{user_id}` to redis-state with TTL = min(time left, 1 h), and deletes it when there is no profile.
  - Every service that changes a profile, the user's status or their rules schedules the refresh with `on_commit`, so Redis only ever shows committed state. A Redis outage never blocks the change; the TTL bounds staleness.
- **Expiry.**
  - A beat job (`apps.accounts.tasks.expire_access`, every 5 minutes) finds profiles whose `expires_at` has passed and hasn't been handled yet (`expiry_processed_for`). It handles them in batches with `SKIP LOCKED`.
  - Each one gets exactly one audit entry (`customer.access.expire`), an `expired` entitlement, and the `access_expired` signal after the commit, which M7 will use to stop sessions.
  - Extending the date re-arms the job.
  - The job also publishes the `iptv_subscriptions{status}` gauge from profile counts, until subscriptions exist.
- **Subscriptions plug in later as a second entitlement source.**
  - The commercial slice adds `billing.Subscription` (with its plan snapshot), and `build(user)` takes the rights from the current active or grace subscription when there is one. `grace_until` and `allow_download` then become real.
  - The object's shape doesn't change, so playback, Xtream and the admin UI need no change. Only `source` and the values differ.
  - Each customer's profile stays their manual override or fallback; whether it should cap a subscription is decided in that slice.

### 6. User model
- **`name`** is one field, entered and shown whole in Arabic or English; it replaces `first_name` and `last_name`.
- **`email`** is stored lowercased. It is unique case-insensitively when present (`UniqueConstraint(Lower("email"))`, blanks excluded): customers may have only a phone.
- **`phone`** is E.164, validated with phonenumberslite; numbers without a country code are read as Saudi.
- **`status`** is `active`, `suspended` or `disabled`, and it drives `is_active`: a disabled account cannot authenticate at all, while a suspended one keeps its login but loses playback.
- **Customers** get usernames `cus-xxxxxxxx` and an unusable password until the portal (M11b) lets them set one.
- **Admin passwords** use Django's Argon2 hasher (Argon2id). The admin API generates them (20 URL-safe characters) and shows them once.

## Alternatives considered
- **JWT for the admin SPA.** Rejected in ADR-0004: same-origin cookies plus CSRF are simpler and safer in a browser.
- **django-otp or django-two-factor-auth.** Both bring their own views and forms built around Django's template login. Our flow is a JSON API with a half-authenticated session. About 60 lines on pyotp, with reuse protection and encrypted secrets, are easier to audit than adapting either.
- **`django.contrib.auth` groups and permissions as the RBAC.** Its permissions are per model and action (`add_user`) and must be bound to content types. Ours are product-level strings from the spec, and the owner role's "always everything" doesn't map onto it.
- **Building plans and subscriptions now (the original plan §3.3).** The owner deferred them. The access profile gives the proof of concept the same playback rights through the same entitlement object, without pricing, renewal maths or a state machine.
- **Stronger Argon2 parameters (for example 64 MiB, t=3).** These cost about 4× the memory and time per login. Xtream apps re-authenticate on every API call, and the 5-minute cache absorbs most of them, but cold logins after a deploy or a cache flush would bunch up. OWASP's minimum at about 17 ms keeps headroom on small hosts. The parameters live in one place, and old hashes upgrade on login.

## Consequences
- **Lockout tuning:** the lockout key (username + IP) means one attacker can't lock out an admin from other addresses, but a distributed guess spreads across IPs. Traefik rate limits (M14) and CrowdSec cover that; tune them then.
- **Lost authenticator:**
  - the dev and demo admin recovers with `make seed args=--reset-admin-mfa`;
  - an admin-facing "reset another admin's MFA" action belongs with WebAuthn in M14, together with re-authentication for sensitive actions (SPEC §11).
- **The commercial slice must:**
  - add the subscription source to `entitlements.build`;
  - switch `iptv_subscriptions` to real subscriptions;
  - rename the profile's role (override or fallback) in the admin UI.
- **One place for Argon2 parameters:** if a production host measures p95 above about 50 ms, lower the memory cost there rather than the time cost, and record the new measurement here.
