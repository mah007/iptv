# ADR-0018: Observability: metrics, logs, dashboards and alerts (M13)

- **Status:** Accepted
- **Date:** 2026-10-11
- **Milestone:** M13

## Context
SPEC §14 asks for `iptv_*` metrics, seven provisioned Grafana dashboards, eleven alerts routed to email and Telegram, logs in Loki with redaction and 30-day retention, and request ids correlated across Traefik, Django and the Nginx edge. §16 adds "Loki redaction verified by test", §17 "alerting verified with a fire drill" and "no secrets in logs (automated test)".

The constraints:
- Grafana and Loki are AGPL-3.0, so they may only run as separate, unmodified containers.
- Production is one 16-core, 15 GB host without a GPU that runs everything.
- The dev box (15 GB) is shared with other work, so the overlay is opt-in there.
- The UIs must be admin-only and never publicly open.
- The owner requires every setting to be editable in the admin, not only in `.env`.

## Decision

### 1. An opt-in compose overlay
- **Files:** `docker/compose.monitoring.yml` (base), `compose.monitoring.dev.yml` (short retention, small limits, Mailpit), and `compose.monitoring.prod.yml` (TLS, public probes).
- **Turning it on:**
  - dev: `make up MONITORING=1` (or `MONITORING=1` in `.env`);
  - production: the installer's question, which records `MONITORING=1`, or `scripts/install.sh --update --monitoring`.
- **Service hardening:** every service has `mem_limit`, json-file rotation, `read_only`, `cap_drop: ALL` and `no-new-privileges`, and a healthcheck where the image has a client. Loki and nginx-exporter have none; Prometheus's `up` watches them.

Images, checked 2026-10-04 with `dep-check` and pinned by tag and digest:

| Image | Version | Licence |
|---|---|---|
| prom/prometheus | v3.13.4 (3.13 LTS, EOL 2027-07) | Apache-2.0 |
| prom/alertmanager | v0.34.1 | Apache-2.0 |
| grafana/grafana | 13.2.3 (`grafana-oss` has no 13.x images) | AGPL-3.0, separate container |
| grafana/loki | 3.7.8 | AGPL-3.0, separate container |
| grafana/alloy | v1.20.1 | Apache-2.0 |
| prom/node-exporter | v1.12.1 | Apache-2.0 |
| ghcr.io/google/cadvisor | v0.60.6 (gcr.io stops at 0.55) | Apache-2.0 |
| quay.io/prometheuscommunity/postgres-exporter | v0.20.1 | Apache-2.0 |
| oliver006/redis_exporter | v1.93.0-alpine | MIT |
| nginx/nginx-prometheus-exporter | 1.5.3 | Apache-2.0 |
| prom/blackbox-exporter | v0.28.0 | Apache-2.0 |
| utkuozdemir/nvidia_gpu_exporter | 1.15.1 (only with `GPU=nvidia`) | MIT |

No Python or npm dependency was added: the code uses `prometheus-client`, `httpx` and Django, which are already pinned.

### 2. Metrics
- **Event counters** live where the event happens:
  - **web:** Gunicorn's multiprocess mode, as before.
  - **Celery workers:** a new endpoint (`apps.core.worker_metrics`). `config.celery.prepare_worker_metrics` switches only a `celery … worker` process with `CELERY_METRICS_PORT` set to a fresh `PROMETHEUS_MULTIPROC_DIR`, before prometheus_client is imported. The pool processes write there; the main process serves the sum on :9100, inside the backend network. Healthchecks and `inspect` commands in the same container keep in-memory metrics, so they never leave files behind.
  - **Task outcomes:** `iptv_celery_tasks_total{task,state}` and `iptv_celery_task_duration_seconds{task}` come from Celery's prerun/postrun signals. Only `apps.*` task names become labels; anything else counts as `other`.
- **The §14 metrics that were defined but never observed are now wired:**
  - Xtream requests and latency are counted by `RequestLogMiddleware` with a fixed action set, so `apps/xtream_api` is untouched.
  - Scan outcomes are counted at scan end, match confidence at the match decision, TMDB calls in the client.
  - Transcode jobs, speed and the review queue are published by a new beat job.
- **New metrics:**
  - payment webhooks by result (`iptv_payment_webhooks_total`);
  - **business and state gauges**, published every minute by `apps.dashboard.tasks.publish_metrics`: customers, devices, trials, MRR, month revenue, titles by status, oldest transcode job, failing webhooks, the notification outbox;
  - **read when web's `/metrics` is scraped**, so they stay visible when the workers are down: heartbeat ages, Celery queue lengths, build info, and `iptv_edge_capacity_bits` (from the setting).
- **Edge metrics from the access log:** Alloy derives `iptv_edge_requests_total{status_class,cache_status}`, sent bytes, request time by kind (playlist, segment, progressive), stream-auth cache results and refusal reasons.
- **Edge egress for the alert** comes from cAdvisor's network counters of the `nginx-stream` container. A log line is written only when its request ends, which for a progressive MP4 can be the whole film.
- **Stores, Traefik and the edge:**
  - **PostgreSQL:** the exporter signs in as a least-privilege `iptv_monitor` role (`pg_monitor`), created idempotently by a one-shot service.
  - **Valkey:** one redis_exporter scrapes both instances (multi-target, password map built in tmpfs from secret files).
  - **Traefik:** metrics on an unpublished `:8082` entry point.
  - **Edge:** a `stub_status` server on `:8081` (`streaming/nginx/status.conf`, one include line).
  - **Health and certificates:** the blackbox exporter probes internal health endpoints, and in production the public hosts over HTTPS (availability and certificate expiry).
- **Host:** node-exporter reads the host through `/host` with its network collectors off; host egress is Traefik's container traffic.

### 3. Logs
- **Redacted at the source:**
  - **Django:** structlog redaction, which now also masks Telegram bot tokens.
  - **Edge:** session prefix instead of the token, as before.
  - **Traefik:** the access log (new, JSON) drops `RequestPath`, `RequestLine` and `ClientUsername` and every header except `X-Request-Id` and `User-Agent`. That block is identical in the dev and production static configs, and the test checks it.
  - **Uvicorn:** found while testing. Its access log reappeared in dev despite `--no-access-log`, because `uvicorn.access` propagated to uvicorn's own unredacted handler. It no longer propagates (regression test).
- **Alloy second line:** Alloy tails the project's containers, applies `redaction.alloy` again (URL user info, Xtream paths, `/v/<token>/`, bot tokens, JWTs, bearer and basic credentials, cookies, secret-named JSON keys, `ticket=`, `source_url`) and sends to Loki with low-cardinality labels (service, container, project, stream).
- **Correlation:** `request_id` becomes structured metadata, taken from Django's and the edge's JSON, from development's coloured key=value lines, and from Traefik's `request_`/`downstream_X-Request-Id`. Grafana's Loki datasource links one request across all three.
- **Loki:** 30 days (compactor), filesystem TSDB. `LOKI_WAL_DISK_FULL_THRESHOLD` defaults to 0.90; development uses 0.99 because the dev disk sits above 90%, and Loki otherwise refuses logs.

### 4. Admin-only access through the admin's own sign-in
- **Where the UIs live:** Grafana, Prometheus (`/prometheus`) and Alertmanager (`/alertmanager`) are on a dedicated host, `grafana.<domain>`. It is a different origin from the admin, so a Grafana XSS can't act with the admin API's session.
- **The admin's Monitoring page:**
  - It needs the new `monitoring.view` permission, granted to admin and viewer; owners hold everything.
  - It gets a one-time ticket (redis-state, 60 s, stored hashed, audited `monitoring.sign_in`).
  - `grafana.<domain>/_sso/callback` consumes the ticket and sets a host-only, HttpOnly `__Host-iptv_monitor` cookie for `monitoring.session_hours`. `next` is limited to relative paths.
- **Every request to the host:**
  1. Traefik strips `X-WEBAUTH-*`.
  2. It asks Django's internal `/internal/monitoring-auth`. The answer comes from Redis; the admin's status and permission are re-read every 60 s, so suspension or a removed permission ends access within a minute.
  3. On success, Traefik sets the identity headers. Grafana's auth proxy accepts them only from Traefik's fixed address on a dedicated `monitoring-ingress` network (172.31.254.2, outside the range Docker hands out there).
- **Grafana itself:** the login form and basic auth are off, and the built-in admin's password is a generated secret. There are no outbound calls (no plugins, update checks, news or analytics).

### 5. Alerts and delivery
- **Rules:**
  - The eleven §14 alerts, plus WebNotReady, PublicEndpointDown, MediaEdgeDown, PostgresDown, RedisDown, CeleryBeatStalled, WatcherStalled, CeleryQueueBacklog, DiskSpaceCritical, HostMemoryLow, ContainerOOMKilled, MonitoringTargetDown and AlertDeliveryFailing.
  - **Watchdog** always fires; **FireDrill** is the drill.
  - Every rule has a promtool unit test (fires on its condition, quiet below it) and a heading in `docs/runbooks/alerts.md`.
- **Delivery goes through Django, configured in the admin (Settings → Monitoring):**
  1. Alertmanager's default receiver is a webhook to web's internal `/internal/alerts`, with a bearer shared secret (`ALERT_WEBHOOK_TOKEN`, also a Docker secret).
  2. Django (`apps.notifications.alerts`) sends synchronously, so "workers down" alerts still go out. It emails through the platform's SMTP settings and sends to Telegram through the Bot API.
  3. The settings registry chooses recipients, the bot token (`sensitive`), the chat id, the severities per channel (all, critical, none) and on/off.
  4. Results are counted (`iptv_alert_deliveries_total`). When every configured channel fails, Django answers 503 and Alertmanager retries.
- **Fallback:** for the few alerts that mean Django can't deliver (WebNotReady, PostgresDown, RedisDown, AlertDeliveryFailing), Alertmanager also sends directly to `ALERT_EMAIL_TO` and `ALERT_TELEGRAM_*` from `.env`. The installer fills both places from the same answers; they arrive twice while both paths work.
- **The edge capacity** behind EdgeEgressHigh is a setting (`monitoring.edge_capacity_mbps`, default 1000), exported as `iptv_edge_capacity_bits`.
- **The installer** writes the recipients and Telegram into the settings with `manage.py set_setting KEY --stdin` (never argv; audited; `--if-unset` on updates).
- **The Monitoring page** shows delivery and has **Send a test alert**, which runs the fire drill (`settings.edit`). It also shows, labelled read-only, what needs a restart: retention, and whether the overlay and the fallback channels are on.

### 6. Tests in the gate
`make monitoring-test` runs in `make ci` after `compat`, needs no stack, and takes about 2 minutes. It runs:
- `promtool check config` on the rendered configuration, the rules, and the rule unit tests;
- every dashboard query, parsed by a throwaway Prometheus, and the dashboards' freshness against their generator;
- amtool on two rendered Alertmanager configurations, and the templates;
- Loki, Alloy (validate and `alloy fmt`) and blackbox config checks;
- compose merges for dev and production;
- `test_log_pipeline.py`:
  - 38 real-shaped lines from every service, filled with fresh random secrets, go through the real Alloy and Loki;
  - a real Traefik with the project's access-log block receives credentialed requests;
  - it asserts Traefik's raw output and everything in Loki (lines, labels, metadata) hold none of the 66 secrets;
  - it also checks the expected redactions, `request_id` metadata, and the edge metrics.

With the overlay up, `make monitoring-smoke` and `make monitoring-firedrill` check the live overlay; the fire drill reads the email back from Mailpit.

## Alternatives considered
- **celery-exporter** (in the spec). Dropped at review: its image is 14 months old and 475 MB, and it needs task events. Our signal-based task metrics give the same task-level view, and the queue lengths and worker `up` come from the scrape.
- **Grafana under `admin.<domain>/grafana`.** Simpler (the session cookie would just work), but it puts a large third-party app on the admin API's origin.
- **Basic auth from a Docker secret.** No MFA, a shared password, and a second login.
- **An IP allowlist.** The owner's addresses change.
- **Alertmanager emailing and messaging directly** (the first draft). Recipients and the Telegram bot would live only in `.env`, contradicting the owner's settings rule. It is kept as the fallback.
- **A rendered recording rule for the edge capacity** (env → Prometheus). Replaced by a setting, for the same reason.
- **Grafana-managed alerting.** Prometheus rules have promtool unit tests and live in the repository.
- **Pushgateway, or Redis-backed counters, for workers.** The multiprocess endpoint is the standard pattern.
- **node-exporter on the host network** for NIC stats. That would expose port 9100 on the host; cAdvisor's per-container network counters (edge and Traefik) answer the actual questions.

## Consequences
- **Security notes:**
  - Alloy reads the Docker API through a read-only socket mount, and cAdvisor reads `/var/lib/docker`, `/sys` and the socket read-only. A read-only socket still allows `docker inspect`, including containers' environment variables, which hold the app's secrets today. Both therefore stay on the internal `monitoring` network with no route in, and Alloy's UI is never exposed.
  - The socket proxy planned for Traefik in M14 should front Alloy and cAdvisor too.
  - All exporters run without capabilities.
  - Secrets reach the overlay as files in `secrets/monitoring/`, written from `.env` by `scripts/secrets.sh` on every run (empty for unset optional channels). A later builder adds encryption at rest and an env fallback for all secrets in the settings registry.
- **Cost on the production host:**
  - memory: about 1.5–2 GB resident (limits add up to about 5 GB);
  - CPU: under a third of a core on average;
  - disk: up to about 16 GB (Prometheus 30 days with a 10 GB cap at a 30 s scrape interval; Loki 30 days). Dev: about 1 GB of memory, 3 days and 72 hours.
- **Production upgrade:** `scripts/install.sh --update` picks everything up: new `.env` keys (`ALERT_WEBHOOK_TOKEN`, `GRAFANA_ADMIN_PASSWORD`, `POSTGRES_MONITOR_PASSWORD`, …), the secret files, the migration that adds `monitoring.view`, and Traefik's access log and metrics entry point. Monitoring itself stays off until `--update --monitoring` or `--reconfigure`.
- **Revisit:**
  - the fallback's duplicate deliveries, if they annoy;
  - per-edge capacity once standalone edges exist (M15);
  - Intel GPU metrics (no exporter yet);
  - PostgresReplicationLag becomes meaningful with M15's replica.
