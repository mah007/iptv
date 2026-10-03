# ADR-0005: M2 foundations: redacted logging, problem+json, OpenAPI, settings registry, audit, metrics

- **Status:** Accepted
- **Date:** 2026-10-03
- **Milestone:** M2

## Context
Every later milestone builds on the same cross-cutting pieces, so they are decided once:
- SPEC §11 forbids credentials and tokens in logs, and Xtream play URLs carry the username and password in the path;
- SPEC §10 asks for RFC 9457 errors with stable codes;
- SPEC §8.4 asks for an OpenAPI schema with a generated, CI-checked client;
- SPEC §6 and §8.3 ask for typed settings that apply without a restart, and an append-only audit log;
- SPEC §14 asks for `iptv_*` Prometheus metrics, never exposed publicly.

## Decision

### 1. Logging and redaction (`apps/core/logs.py`, `redaction.py`, `middleware.py`)
- **One pipeline.** structlog and stdlib records (Django, Celery, libraries) share the processors: contextvars, level, logger name, ISO UTC timestamp, stack info, exception formatting, then redaction. One handler writes to **stderr only**, so stdout stays clean for command output (`manage.py spectacular`). JSON in production, the console renderer in development; `DJANGO_LOG_FORMAT=json|console` overrides either.
- **Redaction runs last before rendering**, after tracebacks are formatted, so it covers messages, bound context and exception text:
  - `redact_text`: Xtream paths `/(movie|series|live|timeshift)/<u>/<p>` become `/<kind>/***/***`; the parameters `password|pass|username|token|access_token|refresh_token|secret|api_key|key|code` become `=***`; `Bearer <token>` (8+ characters) becomes `Bearer ***`; edge tokens `/v/<token>/` become `/v/***/`. It also masks `user:password@` in URLs (connection errors) and quoted `"key": "value"` pairs under those names.
  - `redact_value`: walks structures. Values under keys containing `password|passwd|secret|token|authorization|cookie|api_key|otp|totp|credential|mfa_code|sessionid` become `***` (`otp` only at the start of a word segment, so `footprint` survives). Identifiers such as `username` stay readable in structured data, because audit diffs need them.
- **Django's default handlers are removed.** `DEFAULT_LOGGING` attaches its own plain handler to the `django` logger, which in DEBUG printed `Not Found: /movie/<u>/<p>/…` unredacted. `LOGGING` now reconfigures `django` and `django.server` without handlers, so they reach only the redacting root handler. Celery keeps Django's configuration via a `setup_logging` receiver instead of hijacking the root logger.
- **No access logs.** Uvicorn runs with `--no-access-log` and Gunicorn without `--access-logfile`, since both print raw paths. `RequestLogMiddleware` logs one line per request instead: method, host, redacted path, status, latency, `request_id` (a valid incoming `X-Request-ID`, else a UUIDv7, echoed in the response) and `user_id` when the request authenticated. The query string is never logged. Healthchecks and `/metrics` are logged only when they fail. The context is cleared when a request starts, not when it ends, because Django logs 4xx/5xx responses (`django.request`) after the middleware chain returns, and those lines must still carry the request id.
- **Proof:** `test_no_raw_credentials_in_logs` sends `/movie/alice/s3cr3t-pass/1.mp4?password=…&username=alice` through the full stack and fails if either value reaches the rendered output. A second test checks a 500 whose exception message carries the password.

### 2. Errors: RFC 9457 problem details (`apps/core/errors.py`)
- Body `{type, title, status, code, detail, field_errors?}` as `application/problem+json`; `type` is `urn:smart-iptv:problem:<code-with-dashes>`. Clients branch on `code` only.
- `ErrorCode` is append-only. Default statuses: 400 `VALIDATION_ERROR`, `INVALID_CREDENTIALS`, `MFA_INVALID`; 401 `NOT_AUTHENTICATED`; 403 `PERMISSION_DENIED`, `MFA_REQUIRED`, `MFA_SETUP_REQUIRED`, `SUBSCRIPTION_EXPIRED`, `CATEGORY_NOT_ALLOWED`, `DEVICE_BLOCKED`; 404 `NOT_FOUND`; 405 `METHOD_NOT_ALLOWED`; 409 `CONFLICT`, `CONCURRENCY_LIMIT`, `DEVICE_LIMIT`, `TITLE_PREPARING`; 429 `RATE_LIMITED`, `ACCOUNT_LOCKED`; 500 `INTERNAL_ERROR`. A raise site may pass another status.
- Validation errors are flattened to dotted paths (`profile.phone`, `devices.1.name`, `non_field_errors`). Django `ValidationError`, `Http404` and `PermissionDenied` are converted too. 5xx responses never carry exception text.
- `handler400/403/404/500`, the CSRF failure view and a catch-all for unknown `/api` paths answer problem+json on the admin, api and portal URLconfs. The Xtream URLconf keeps PHP-panel shapes (SPEC §7.5).

### 3. OpenAPI (drf-spectacular)
- OAS 3.1.0, `COMPONENT_SPLIT_REQUEST`, `SERVE_INCLUDE_SCHEMA = False`, API version `1.0.0` (not the release number, so the generated client doesn't change on releases). `GET /api/v1/schema` on the admin host is staff-only, and open in DEBUG for local tooling.
- Operation ids and tags come from the path after `/api/v1[/admin]` (`settings_list`, `audit_list`), so hooks read `useSettingsList`.
- A postprocessing hook adds the `Problem` and `ErrorCode` components and declares `Problem` as every operation's `default` response. The client therefore knows the error shape and the full code list.
- **Every shared enum gets an `ENUM_NAME_OVERRIDES` entry.** Otherwise spectacular names enums after fields and disambiguates collisions with hashes and a warning, and the generator runs with `--fail-on-warn`.
- PATCH endpoints are true partial updates, because spectacular documents every PATCH body as partial: `PATCH /admin/settings/{key}` without `value` changes nothing.
- The pagination envelope is described in 3.1 terms, with every key required and `next`/`previous` typed `string | null`.

### 4. Settings registry (`apps/core/registry.py`, `services.py`, `core.Setting`)
- Definitions live in code (`SettingDef`: key, kind `bool|int|float|str`, default, description, group, validator, sensitive, min/max, choices) and are checked at import. The table holds admin overrides only.
- **Reads:** `settings:version` in redis-cache holds an opaque token, and `settings:values:<token>` caches the overrides. Each process memoises the overrides of the token it last saw, so a read costs one Redis GET. A version's values never change, so a stale memo is impossible. An evicted version key starts a new token. If redis-cache is down, reads go to Postgres.
- **Writes** (`set_setting`): validate, store under `select_for_update`, audit `setting.update` with before/after, and replace the token **on commit**, so no reader can cache the old row under the new token. Writing the current value again changes and records nothing. A stored value that no longer validates (after a code change) falls back to the default.
- Sensitive settings are never returned by the API and are audited as `***`.

### 5. Audit log (`apps/audit`)
- `AuditLog` (UUIDv7 id, `at`, actor, actor_ip, action, target_type/target_id, before/after JSON) is not a `BaseModel`: an entry is never updated. `services.record()` stores redacted, JSON-normalised snapshots and validates the IP. Model targets are recorded as `app_label.model` plus the primary key.
- **Append-only at the database:** a trigger refuses UPDATE and DELETE for every role with SQLSTATE 42501 (`insufficient_privilege`), the same error the M14 role split will raise. The model refuses `save()` on existing rows and `delete()` too. TRUNCATE stays possible for the table owner: test-database flushes and restores need it, and the M14 application role won't have it.
- `actor` is `PROTECT`: users are disabled, not deleted, and `SET_NULL` would need an UPDATE the trigger refuses.
- **Monthly partitioning is deferred to M15** with `playback_sessions`, `watch_history` and `epg_programs` (SPEC §6). Partitioning needs the partition key in the primary key and a table rewrite, and it is worth doing once, with the load tests, for all high-volume tables. Until then the `at` index and the composite indexes on `(target_type, target_id, at)` and `(action, at)` keep the admin queries cheap.
- `GET /api/v1/admin/audit`: newest first; filters on actor, action, target and an `at` range; page numbers (default 25, `page_size` up to 100); two queries per page.

### 6. Metrics (`apps/core/metrics.py`)
- django-prometheus middleware labels requests by view, never by path. `/metrics` exists only in the internal URLconf; Traefik's `api` and `tv` routers also exclude it, and production exempts it from the HTTPS redirect so Prometheus can scrape over the Docker network.
- **Gunicorn multiprocess mode:** `config/gunicorn_conf.py` sets `PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus-multiproc`, wiped in `on_starting` before workers fork, and marks dead workers in `child_exit`. It is set inside Gunicorn only, not in the image, so Celery and one-off commands from the same image stay in single-process mode.
- **Event metrics** (counters, histograms) live where the event happens. Ones observed in Celery workers (scans, notifications, metadata calls) need a worker exporter, which comes with the monitoring overlay (M13).
- **State metrics** (`iptv_subscriptions{status}`, `iptv_active_streams`, `iptv_transcode_jobs`, `iptv_review_queue_open`, `iptv_transcode_speed_ratio`) are snapshot gauges. A periodic job computes them in any process and publishes them to redis-cache with a TTL; `/metrics` exports the latest snapshot. This is correct whichever container computed them, and a stale job shows up as a missing series. The M3 billing job publishes `iptv_subscriptions`.

### 7. Smaller decisions
- **Client IP:** the right-most `X-Forwarded-For` entry (the address Traefik saw; Traefik doesn't trust client-supplied forwarding headers), validated, else `REMOTE_ADDR` (`apps.core.http.client_ip`).
- **`make secrets` merge mode:** on an existing `.env` it appends keys that are new in `.env.example` (placeholders generated) without touching existing lines, and prints only the key names. `__GENERATE_FERNET__` produces a Fernet key (urlsafe base64 of 32 random bytes), used for `FIELD_ENCRYPTION_KEY`.
- **Make targets:** `typecheck` = `typecheck-backend` + `typecheck-frontend`; `api-client`, `api-client-check` (ADR-0004). `make api-client` runs pnpm with `verify-deps-before-run=false`: it uses the installed `node_modules` and never re-links them to another store.

## Alternatives considered
- **Redaction only in Alloy (M13):** logs would leave the container unredacted, and dev logs would leak today. Defence in depth needs Django to redact too.
- **Unstructured logs with a redacting formatter:** loses fields and request correlation, which SPEC §14 requires.
- **Django's cache API with per-key TTLs for settings:** changes would apply only after the TTL; the version token applies them on the next read.
- **App-level check only for audit immutability:** bulk `update()`/`delete()` and SQL bypass it; the trigger cannot be bypassed short of dropping it.
- **A shared multiprocess directory volume between web and workers:** PID collisions across containers corrupt samples.

## Consequences
- New code logs through structlog (`structlog.get_logger(__name__)`) with fields, and never formats secrets into messages. Redaction is a safety net, not permission. Endpoints that move credentials in new places (e.g. Xtream live URLs as bare `/<u>/<p>/<id>` in M9/M12) must extend `redact_text` and its tests.
- Services raise `ProblemError(ErrorCode.X)` (or Django/DRF validation errors); views stay thin.
- Thresholds, TTLs and flags go into the registry with a validator, and are read with `get_setting`/`is_enabled`.
- Every admin, billing and subscription mutation calls `audit.services.record()` inside its transaction.
- Revisit: audit partitioning and DB roles (M14–M15), a worker metrics exporter (M13), Traefik access logs with the path dropped once Alloy exists (M13).
