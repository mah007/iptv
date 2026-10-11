# ADR-0019: Dynamic configuration: secret settings, the server-config fallback, Integrations and System pages

- **Status:** Accepted
- **Date:** 2026-10-11
- **Milestone:** CFG (between M13 and M14)

## Context
The owner's standing rule: every setting, password and integration must be editable in the admin, like any end-user product. `.env` keeps only what is needed before the database is reachable, or what would break the stack if changed live.

Before this work, TMDB, SMTP, Stripe, Moyasar, the transcoder's retry knobs, the watcher's polling mode and the runbook URL lived only in `.env`. Alertmanager's own fallback channels did too. The settings registry (ADR-0005) already had a `sensitive` flag that masked values in the API, but:
- it stored secrets in plain JSON;
- it had no fallback to existing `.env` values.

The live server keeps its TMDB token in `.env` and must keep working after `install.sh --update`.

## Decision

### 1. Secret settings and the server-config fallback (`apps/core/registry.py`, `services.py`)
- **New `SettingDef` fields:**
  - `env`: the server-config variables used as the fallback;
  - `env_resolve`: derives one value from several variables (`EMAIL_USE_SSL` and `EMAIL_USE_TLS` become `email.smtp_security`; `TRANSCODE_JOB_TIMEOUT_S` becomes hours);
  - `widget` (`"image"`): the logos.
- **`build_registry` refuses:**
  - sensitive keys that aren't strings;
  - malformed env names;
  - `env_resolve` without `env`;
  - unknown widgets.
- **Effective value:** the stored value, else the server config, else the default. The API reports the source (`database`, `environment` or `default`) and never the secret.
  - The server config is the Django setting of that name when `config/settings` defines it, else the environment variable. `None` or `""` counts as unset, which is why base.py's moved variables no longer carry literal defaults.
  - A server-config value gets type, range and choice checks but not the format validator, so a `.env` value from before a validator existed keeps working. An invalid one is logged without its value, and the default applies.
  - An explicitly empty stored value wins over `.env`. The owner can turn a `.env` value off from the admin.
- **Encryption at rest:** `core.Setting` gains `ciphertext`, and `value` becomes nullable (migration `core/0003`).
  - A secret row has `value = NULL` and a Fernet token from `apps.accounts.crypto` (FIELD_ENCRYPTION_KEY). An empty `ciphertext` means "set to empty".
  - The cache in redis-cache, `settings:values:v2:<version>`, holds ciphertext only.
  - `core/0004` encrypts rows already stored, for a frozen key list, through the reusable `apps/core/migration_helpers.py`. It also renames `monitoring.telegram_*` to `notifications.telegram_*`.
  - A secret still stored in plaintext reads with a warning until it is saved again. One that FIELD_ENCRYPTION_KEY can't decrypt reads as empty and is flagged `undecryptable`.
- **One accessor:** `get_secret(key)`. `get_setting` raises `SensitiveSettingError` for secrets.
- **Writers:**
  - `set_setting`, which encrypts;
  - `reset_setting` (DELETE), audited as `setting.reset`;
  - `adopt_setting`, which copies the `.env` value in and is audited as `setting.adopt`;
  - `set_settings`, a bulk change that validates everything and then writes in one transaction.
- **Audit:** secrets are recorded as `{"configured": bool, "source": ...}` and never as a value.
- **`reencrypt_secret_settings()`** is for FIELD_ENCRYPTION_KEY rotation (M14b calls it).
- **Read-your-writes:** a writer forgets its own memo and the current version's cached rows at once. Other processes reload on the version bump after the commit (unchanged from ADR-0005).
- **Defence in depth:**
  - `redact_text` also masks Stripe and Moyasar keys, `whsec_` secrets, Telegram bot tokens and JWTs;
  - the `httpx` and `httpcore` loggers are set to WARNING, because request URLs can carry a bot token.

### 2. What moved into the registry (with the old variables as the fallback)

| Group | Keys | Fallback |
|---|---|---|
| email (new) | smtp_host, smtp_port, smtp_security (starttls/ssl/none), smtp_username, smtp_password (secret), from_address, timeout_s | EMAIL_HOST, EMAIL_PORT, EMAIL_USE_SSL/TLS, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD, DEFAULT_FROM_EMAIL, EMAIL_TIMEOUT |
| metadata | tmdb_read_access_token, tmdb_api_key (secrets), tmdb_language, tmdb_rate_limit_per_s, tmdb_cache_ttl_s, tvdb_api_key, tvdb_pin (secrets) | TMDB_*, THETVDB_API_KEY |
| billing | stripe_secret_key, stripe_webhook_secret, moyasar_secret_key, moyasar_webhook_secret (secrets) | STRIPE_*, MOYASAR_* |
| notifications (new) | telegram_bot_token (secret), telegram_chat_id (the admins' chat; alerts use it) | ALERT_TELEGRAM_BOT_TOKEN, ALERT_TELEGRAM_CHAT_ID |
| monitoring | alert_emails gains a fallback; runbook_base_url | ALERT_EMAIL_TO, RUNBOOK_BASE_URL |
| branding | support_phone, support_whatsapp, logo_light, logo_dark (uploads), default_language, time_zone | none |
| library | watcher_polling, transcode_max_attempts, transcode_retry_backoff_s, transcode_job_timeout_h | LIBRARY_WATCHER_POLLING, TRANSCODE_* |

The existing branding (names, colour, support email) and billing settings (currency, VAT rate and number) are reused.

**Where the new branding settings are used:**
- **The logo:** emails (a header image), invoices, the admin's sidebar and sign-in, and the portal's header.
- **Support phone and WhatsApp:** email template variables, the invoice seller block, and the portal's footer.
- **Default language and time zone:** callable model defaults for new users (`accounts/0006`), the customer wizard, a portal visitor's first visit, the dashboard's days, and the fallback for notifications and invoices.
- **The public endpoint:** `GET /api/v1/branding` on the admin, app and api hosts, cached 60 s.
- **Logo uploads:** checked in web (PNG, JPEG or WebP, at most 2 MB, no SVG). A worker re-encodes them to a PNG of at most 512 px and stores them under `images/branding/` with a content-hashed name, so the edge serves them with immutable caching. PNG because email clients don't all show WebP.

There is no WhatsApp channel placeholder: `notifications.Channel` names only email.

### 3. What stays in `.env` (bootstrap only), and why
- **DJANGO_SECRET_KEY, FIELD_ENCRYPTION_KEY:** needed before anything else runs. The second one decrypts the settings themselves.
- **Data-store hosts and passwords, MEILI_*, POSTGRES_CONN_MAX_AGE:** needed to reach the database where the settings live.
- **DOMAIN, the hosts, ports, scheme, MEDIA_BASE_URL, ACME_EMAIL:** Traefik labels and ALLOWED_HOSTS are fixed at start; `--reconfigure` changes them.
- **Paths and binaries:** MEDIA_ROOT, LIBRARY_ROOT, DATA_ROOT, LIVE_*, the profiles and caps files.
- **The media token keys file:** the edge reads it at start; M14b rotates it.
- **Monitoring overlay:** ALERT_WEBHOOK_TOKEN, GRAFANA_ADMIN_PASSWORD, POSTGRES_MONITOR_PASSWORD and the retention flags. These are read by containers at start.
- **MediaMTX:** LIVE_INGEST, LIVE_INGEST_BIND and the MEDIAMTX users.
- **Build and deployment facts:** APP_VERSION, APP_COMMIT, DJANGO_SETTINGS_MODULE.
- **Logging:** DJANGO_LOG_LEVEL and DJANGO_LOG_FORMAT are configured at process start.
- **DJANGO_SECURE_SSL_REDIRECT and the HSTS settings:** read when the middleware starts, and M14a owns them.
- **TRANSCODER_CONCURRENCY:** a Celery pool size is fixed per process and depends on the GPU.
- **Internal protocol timings:** PLAYBACK_* slot, heartbeat and kick timings, TRANSCODE_LEASE_TTL_S and LIVE_RELAY_*. They are coupled to the edge's 60 s auth cache, the sweeper and the relay, so changing one alone breaks playback; they are not operator knobs.
- **Process wiring:** EDGE_HEALTH_URLS, GRAFANA_URL, CELERY_METRICS_PORT, PROMETHEUS_MULTIPROC_DIR.

The System page lists these secrets with their reason and whether each is present, never a value or a length.

### 4. Runtime pickup, per process
Every process reads through the version token, so a change needs no restart:

| Process | How it picks up a change | Seen on the dev stack (2026-10-11) |
|---|---|---|
| web | every read checks the version | the E2E journey; the test email used the saved SMTP |
| worker | per task. The TMDB client and its Arabic genre cache are keyed by a fingerprint of the TMDB settings and rebuilt when they change. The email backend reads the settings per connection, and the providers and Telegram per call. | an email queued after SMTP was moved to a wrong port failed with "connection refused"; after the port was fixed, the retry delivered it to Mailpit, with no restart |
| transcoder | `media/conf.py` reads the registry per job | unit-tested |
| beat | reads no settings (a static schedule); its tasks run in the worker | not applicable |
| live packager | `load_limits()` every loop, logged as `live.limits_changed` | logged 1 s after the change |
| watcher | stable_s per event; `library.watcher_polling` at each library sync (60 s) swaps inotify and polling | "library watcher mode changed" 31 s after the change |
| Alertmanager | see §5 | reloaded 49 s after the change |

### 5. Alertmanager's fallback comes from Django too
The fallback channels (the alerts about web, PostgreSQL or Valkey being down) used `.env`. Now:
- Django serves `GET /internal/alert-fallback`. It is on the internal URLconf, takes the existing ALERT_WEBHOOK_TOKEN as a bearer token, and is never cached.
- It answers the admin's effective settings, one per line as `name<TAB>base64`: recipients, SMTP smarthost, sender, user and password, `require_tls`, and the Telegram bot and chat.
- A channel the admin switched off (alerts off, or severity "none") is empty.

The Alertmanager entrypoint:
1. fetches the settings at start and every `ALERT_FALLBACK_POLL_S` (60 s);
2. keeps the last copy in the data volume, so restarts while web is down still have it;
3. re-renders the configuration atomically and sends SIGHUP when the settings change;
4. refuses an invalid copy and keeps the running configuration.

Before the first fetch succeeds, the old `.env` values apply (installations from before this change). The URL names the host fully qualified (`web.`), because busybox's wget mishandles Docker's `search .` for short names.

**Deviation from the plan:** the plan said Django would write the values into a shared volume. A pull from an internal API, which is also one of the owner's sanctioned patterns, needs no writable volume in web, no write at web startup and no extra service. It still reloads within a minute and still works while web is down.

### 6. Admin UI and API
- **New endpoints:**
  - `PATCH settings` (bulk; `null` resets a key)
  - `DELETE settings/{key}`
  - `POST settings/{key}/adopt`
  - `POST settings/{key}/image`
  - `GET integrations`
  - `POST integrations/{name}/test`
  - `GET system`
  - the public `GET /api/v1/branding`
- **Integrations page:** one card each for TMDB, TheTVDB, Email, Stripe, Moyasar and Telegram.
  - Status: `configured`, `server_config`, `partial` or `not_set`.
  - Secret inputs are never prefilled. The actions are Set or Replace, and Clear (DELETE, back to `.env` or the default). For a value from `.env` they are Move into settings and Turn off (an explicitly empty value).
  - Payment cards show the webhook URL.
- **Test actions:** Save, then Test; tests use saved values only. They run on the server, are limited to 5 a minute per admin and integration, and are audited as `integration.test` with the outcome only. Each answers 200 with `{ok, code, message, details}`.
  - **Email:** a message with a reference to the signed-in admin.
  - **TMDB:** `GET /3/authentication`.
  - **TheTVDB:** `/v4/login`.
  - **Stripe:** `/v1/balance`, plus whether our webhook endpoint is registered.
  - **Moyasar:** `/v1/payments`, plus its webhooks.
  - **Telegram:** `getMe`, then a message to the admins' chat.

  Timeouts are 10 s. Messages are redacted and never echo a secret.
- **System page:** environment, version and commit, domain and hosts, folders, the monitoring and ingest overlays, the settings still in `.env`, the bootstrap secrets (where each lives and why), and the last rotation of each key as M14b audits it (`keys.rotate`, target = kind).
- **Generic Settings page:** shows the source badge, write-only secrets and logo uploads. Reset uses DELETE.

### 7. Installer
- A new installation still asks for TMDB and SMTP (optional) and the monitoring recipients and Telegram. These are written with `manage.py set_setting --stdin` after the stack starts; nothing goes into `.env`.
- `--reconfigure` covers the domain, admin email, media folder and the monitoring overlay. The summary points to Admin → Settings → Integrations.
- `--update` writes APP_COMMIT, keeps old `.env` values working through the fallback, and prints how many settings still come from `.env` (`manage.py settings_report --count`, names only).

### 8. For M14a
Step-up re-authentication belongs in front of:
- `PATCH settings`, `PATCH settings/{key}` and `DELETE settings/{key}`;
- `POST settings/{key}/adopt` and `POST settings/{key}/image`;
- `POST integrations/{name}/test`.

## Alternatives considered
- **A JSON wrapper (`{"ciphertext": ...}`) in `Setting.value` instead of a column:** no schema change, but ambiguous. A NULL value next to a ciphertext column says plainly what the row is.
- **Reading the fallback from `os.environ` only:** the existing tests and dev.py configure through Django settings (Mailpit in dev). Reading the Django setting first keeps both, and env-only names still work.
- **Applying the format validators to `.env` values:** safer-looking, but a stricter validator could silently turn off a production credential at upgrade.
- **Testing unsaved values (drafts):** that would put secrets in test requests and make the stored state ambiguous. Save, then Test is simpler and audited.
- **Storing logos in the database and serving them through Django:** synchronous, but media must not go through Django. The artwork pattern (a worker task, the edge) is consistent and cacheable.
- **A shared volume for Alertmanager's fallback:** see §5.

## Consequences
- **Everything an operator configures lives in the admin.** `.env` is bootstrap only, and the System page says what stays there and why.
- **New secrets** are `sensitive=True` registry keys read with `get_secret`. A key that becomes sensitive later needs a data migration (copy `0004`'s use of `encrypt_rows`).
- **FIELD_ENCRYPTION_KEY rotation** must call `reencrypt_secret_settings()`. Losing the key loses the stored secrets; they show as "can't be read" and are set again.
- **Alertmanager** depends on Django for its fallback's settings only once: it keeps the last copy.
- **Revisit:**
  - the runtime accent colour in the UIs (SPEC §8.1), which the branding endpoint now makes possible;
  - localised registry validation messages (the API sends English for invalid values, as before);
  - TheTVDB matching itself, which Q1's client will add on top of `metadata.tvdb_*`.
