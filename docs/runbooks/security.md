# Runbook: server security

How the server is hardened (ADR-0020) and how the application protects itself (ADR-0021), what to check after installing, and the day-to-day commands: firewall and SSH, TLS, CrowdSec, the container and data-store boundaries, admin sign-in and recovery, security headers, rate limits, IP geolocation, mail authentication and personal-data retention. Other runbooks for when something goes wrong:
- [incident-response.md](incident-response.md): something is wrong;
- [leaked-credential.md](leaked-credential.md): a secret got out;
- [rotate-keys.md](rotate-keys.md): routine key and password changes;
- [backups.md](backups.md): backups and restores.

All examples use the installation's compose command:
```bash
cd /opt/iptv
alias dc='docker compose --project-directory . -f docker/compose.yml -f docker/compose.prod.yml'
# plus -f docker/compose.monitoring.yml -f docker/compose.monitoring.prod.yml with MONITORING=1,
# and -f docker/compose.crowdsec.yml with CROWDSEC=1 (the installer's summary prints the full line)
```

## After installing: the checklist
- [ ] **Firewall:** `ufw status verbose` shows deny incoming, SSH (limited, or from your networks), 80 and 443.
- [ ] **SSH:** key sign-in only, and no root password sign-in (see below).
- [ ] **TLS:** an **A+** at [SSL Labs](https://www.ssllabs.com/ssltest/) for the base name and `admin.` (see below).
- [ ] **CrowdSec:** `dc exec crowdsec cscli metrics` shows Traefik's log lines being read, and the bouncer is registered (`cscli bouncers list`).
- [ ] **Backups:** the admin's Backups page shows a database backup and a successful drill, and the backup passphrase is stored away from the server ([backups.md](backups.md)).
- [ ] **Alerts:** a test alert arrived (admin → Monitoring → **Send a test alert**).
- [ ] **Disk encryption:** if your host or provider offers encryption at rest for the volumes and the backup disk, it is on. The application doesn't encrypt Docker volumes; the backups themselves are encrypted.
- [ ] `make hardening-test` passed on the commit you deployed (it runs in `make ci`).
- [ ] **Security keys:** every owner has added a security key or passkey (admin → user menu → **Sign-in and security**), and at least two owners exist, so one can reset the other ([below](#locked-out-admins)).
- [ ] **Mail:** SPF, DKIM and DMARC are set for the sending domain ([below](#mail-authentication-spf-dkim-dmarc-g-33)).
- [ ] **IP geolocation:** admin → **Settings → Integrations** → *IP geolocation* shows both databases installed (the worker downloads them on its first start).

## Firewall and SSH (G-42)
The installer (`--firewall`, or its question) configures ufw:
- `default deny incoming`;
- SSH on every port sshd listens on, rate-limited (`ufw limit`), or only from the networks given with `--ssh-from 203.0.113.0/24,198.51.100.7`;
- 80 and 443.

By hand (a server installed without it; `--update` never touches the firewall). Check which port SSH uses first (`sshd -T | grep ^port`), and keep your session open:
```bash
ufw default deny incoming
ufw limit 22/tcp                      # or: ufw allow from 203.0.113.0/24 to any port 22 proto tcp
ufw allow 80/tcp && ufw allow 443/tcp
ufw --force enable && ufw status verbose
```
Docker publishes its ports past ufw, which is why nothing but Traefik (80, 443) and, when live ingest is on, the RTMP/SRT port is published. The databases, Valkey and Meilisearch sit on internal Docker networks with no published port.

SSH hardening is yours to apply (the installer doesn't touch sshd, to avoid locking you out). Make sure your key works first, then in `/etc/ssh/sshd_config.d/10-hardening.conf`:
```
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
MaxAuthTries 3
```
Apply it with `sshd -t && systemctl reload ssh`, and keep the current session open until a new one signs in. Optional extras:
- `fail2ban` for sshd (CrowdSec here watches only Traefik);
- unattended security updates (`apt install unattended-upgrades`).

## TLS and HSTS (G-43)
- Traefik serves TLS 1.2 and 1.3 only, with forward-secret AEAD suites and the X25519, P-256 and P-384 curves (`docker/traefik/prod/dynamic/tls.yml`).
- Certificates come from Let's Encrypt (HTTP-01), and HTTP redirects to HTTPS.
- HSTS (`Strict-Transport-Security: max-age=31536000; includeSubDomains`) comes from both Traefik and Django.

**A+ checklist:** SSL Labs grades A with TLS 1.2+ and these suites; HSTS with a year's max-age makes it A+. Check that:
- [ ] `curl -sI https://admin.<domain>/ | grep -i strict-transport` shows the header;
- [ ] SSL Labs reports no TLS 1.0/1.1 and no CBC suites;
- [ ] the certificate chain is complete;
- [ ] every host has a certificate: `admin.`, `app.`, `api.`, `media.`, the base name, and `grafana.` with monitoring.

**HSTS preload** (optional, hard to undo): browsers then never try HTTP for the domain and *all its subdomains*, and removal from the list takes months.
1. Make sure every subdomain you will ever use serves HTTPS.
2. Set `DJANGO_HSTS_PRELOAD=1` in `.env`, then `dc up -d` (Traefik and Django both add `preload`).
3. Submit the base name at [hstspreload.org](https://hstspreload.org).

## CrowdSec (opt-in, on by default in the installer)
The CrowdSec engine reads Traefik's access log through the logs-only socket proxy, and the bouncer plugin in Traefik answers **403** to banned addresses on every router.
- **Turn it on:** `install.sh --update --crowdsec`.
- **Turn it off:** `--update --no-crowdsec`.

**What it can see:** Traefik logs carry the address, method, status, size, user agent and router, but no paths (paths hold Xtream credentials and media tokens, so they are never logged). So:
- **It catches:**
  - known bad user agents (`crowdsecurity/http-bad-user-agent`);
  - floods of 4xx answers (`smart-iptv/http-4xx-flood`: 120 at once, refilling 1 every 2 s);
  - addresses that keep hitting rate limits (`smart-iptv/http-ratelimited`: 40 `429`s, refilling 1 every 10 s);
  - with the community blocklist on, addresses others have reported.
- **It can't catch:**
  - path-based scans;
  - Xtream password guessing, because failed Xtream sign-ins return HTTP 200 by contract. That one is covered by the per-username and per-address limits in Django (`xtream.auth_failures_*`).

**Admin control:** **Settings → Security**:
- `security.crowdsec_enforce`: switch blocking off without uninstalling; CrowdSec still records decisions;
- `security.crowdsec_allowlist`: addresses or networks never blocked (your office, monitoring probes).

Changes apply within seconds: the worker rewrites the bouncer's configuration and Traefik reloads it. If the engine is down, the bouncer lets traffic through (fails open).

**The community blocklist** (`--crowdsec-online`) is off by default: it sends the addresses that attack this server to CrowdSec in exchange for their list. Turn it on only if that's acceptable for you.

**Commands:**
```bash
dc exec crowdsec cscli decisions list                    # who is banned, why, for how long (default 4 h)
dc exec crowdsec cscli decisions delete --ip 203.0.113.7 # unban an address
dc exec crowdsec cscli decisions add --ip 203.0.113.7 --duration 24h --reason manual   # ban one
dc exec crowdsec cscli alerts list                       # what triggered
dc exec crowdsec cscli metrics                           # log lines read and parsed
```
`make crowdsec-test` (development) bans a test address, checks 403, switches enforcement off and on from the settings, unbans and checks 200.

## Containers (G-36, G-39)
- **No container has the Docker socket** except the two socket proxies:
  - `docker-proxy`: for Traefik, read-only container listing and events;
  - `docker-logs-proxy`: for Alloy and CrowdSec, listing, inspection and logs.
  
  Both are `wollomatic/socket-proxy` with request allowlists, on their own internal networks. The exceptions, documented in ADR-0020, are in the monitoring overlay:
  - cAdvisor mounts the host's `/var/run` (the Docker and containerd sockets), `/sys`, `/var/lib/docker` and `/` read-only, to name and measure containers. A read-only mount doesn't limit what can be asked through a socket, so cAdvisor is trusted like Docker itself: pinned by digest, no published port, internal networks only;
  - node-exporter reads the host's `/proc` and `/sys`.
- **Every service** drops all Linux capabilities and adds back only the few it needs, sets `no-new-privileges`, and runs with a read-only root filesystem (writable `tmpfs` where needed). Every image is pinned by digest.
- `make hardening-test` checks all of this on the rendered production configuration, with every overlay, and fails on any regression.

## Database roles (G-37)
| Role | Who | What it can do |
|---|---|---|
| the owner (`POSTGRES_USER`) | `migrate`, `db-roles`, backups, the restore drill | everything: owns the schema, runs migrations |
| `iptv_app` | web, worker, beat, watcher, transcoder, live | read and write rows; no DDL; the audit log is append-only; partitions and the audit purge only through `SECURITY DEFINER` functions |
| `iptv_reporting` | for read-only reporting tools (password `POSTGRES_REPORTING_PASSWORD`) | `SELECT` on everything except credentials and secrets: Xtream credentials, TOTP secrets, sessions, sign-in logs, stored settings, live-integration credentials, webhook payloads |
| `iptv_monitor` | postgres-exporter (monitoring overlay) | `pg_monitor` only |

- Grants are applied after every migration (`apps/ops/grants.py`), so a new table is covered at once.
- Running `manage.py migrate` as the app role is refused with a pointer to `dc run --rm migrate`.
- Development uses the same roles.

## Valkey users (G-38)
Each Valkey has three users, rendered into an ACL file at start from `.env`:
- `default`: the operator's account; healthchecks and `valkey-cli`;
- `app`: Django, Celery and the live relay; every command except the dangerous ones (`FLUSHALL`, `FLUSHDB`, `CONFIG`, `DEBUG`, `MODULE`, `KEYS`, `SHUTDOWN`, `ACL`, `MONITOR`, `SAVE`, replication...);
- `exporter`: redis_exporter; only the read commands it needs (`INFO`, `CONFIG GET`, `SCAN`, lengths and types).

redis-state's memory is capped (`REDIS_STATE_MAXMEMORY`, 1 GB) with `noeviction`. The RedisStateMemoryHigh alert fires at 80%, long before writes fail (G-35).

## Secrets
- `.env` (mode 600) holds the bootstrap secrets; `secrets/` holds the Docker secrets (media token keys, the edge's stream-auth secret `edge_auth_token`, backup passphrase, agent token, monitoring and CrowdSec keys).
- Both are backed up, encrypted, nightly. Integration secrets (TMDB, SMTP, payments, Telegram, the backup bucket) live encrypted in the settings (ADR-0019).
- Every key and password can be rotated with `scripts/rotate-keys.sh` ([rotate-keys.md](rotate-keys.md)).

## Admin sign-in (ADR-0021)
- **Second factor:** every admin has an authenticator app (TOTP) or a security key or passkey (WebAuthn). Keys are added under user menu → **Sign-in and security**; they can't be phished, because the browser only answers the real admin host.
- **Requiring keys:** `security.webauthn_required_for` (Settings → Security): `none` (default), `owners`, or `admins` (owners and admins). Those admins must add a key at their next sign-in and can no longer confirm with a code.
- **Confirm it's you (step-up):** settings, roles and admins, refunds, plan changes, trials and extensions, deletions, credential resets, backups and *revoke by app* ask for a fresh code or key once every `security.admin_step_up_window_min` minutes (5 by default). Protective actions (block, revoke, kill a stream, suspend) never ask. The top bar shows the time left and a **Lock** button. Five wrong answers in a row end the session.
- **Idle timeout:** `security.admin_idle_timeout_min` (30 minutes), for every admin, with a warning two minutes before.

### Locked-out admins
- **Lost phone or key, other owners available:** another owner (or an admin with *admins.manage*, for non-owners) opens **Admins** → the admin → **Reset sign-in confirmation**. Their authenticator and keys are removed, their sessions end, and their next sign-in enrols a new authenticator app. Check that it is really them asking first.
- **The only owner is locked out:** from the server, which proves you're the operator:
  ```bash
  dc exec -T web python manage.py reset_admin_mfa <username or email>
  ```
  It's audited as `admin.mfa_reset` without an actor, and prints nothing secret. A lost password is `dc exec web python manage.py changepassword <username>`.

### Admin IP allowlist
`security.admin_ip_allowlist` (Settings → Security): addresses and networks, separated by commas (`203.0.113.0/24, 198.51.100.7`). When set, the admin's API (and Grafana's sign-in) answer `403 IP_NOT_ALLOWED` to every other address; health checks and the sign-in page's branding stay reachable. The admin refuses a list that leaves out your own address. If your address changes and locks you out, clear the list from the server:
```bash
dc exec -T web python manage.py set_setting security.admin_ip_allowlist --stdin < /dev/null
```
Use it only where staff have stable addresses (an office, a VPN): mobile networks change address often.

## Security headers and CSP (G-19, G-24)
| Where | What sends them | Content-Security-Policy |
|---|---|---|
| Admin and portal pages | the frontend's nginx (`frontend/packages/ui/csp/policy.js`, rendered at image build) | `default-src 'none'`; scripts and styles only from the app with a fresh nonce per page; media and images from the media edge, `*.<DOMAIN>` and `security.csp_extra_media_origins`; frames: the portal's YouTube trailers only; `frame-ancestors 'none'` |
| API responses (`api.`, `admin./api`, `app./api`, `tv.`) | Django (`apps/core/security.py`) | `default-src 'none'; frame-ancestors 'none'` |
| Invoices and mail previews | Django, per document | inline styles and images only, no scripts; previews sandboxed |
| Media | the edge (`streaming/nginx/snippets/headers.conf`) | `default-src 'none'` |

`Referrer-Policy` is `no-referrer` on `tv.` and the media edge, `same-origin` on the admin, `strict-origin-when-cross-origin` on the portal and API. Traefik adds `nosniff`, `X-Frame-Options: DENY` and, in production, HSTS.

- **Extra media origins** (a CDN on another domain): add them to `security.csp_extra_media_origins`. The pages read the list when the frontend starts: run `scripts/install.sh --update`, or `dc up -d --no-deps --force-recreate frontend`.
- **Violations:** browsers report them to `/api/v1/csp-report` (rate-limited); they're counted in `iptv_csp_violations_total{app,directive}` and logged without query strings. A steady count after an update usually means a new media or image origin: add it as above.
- **HSTS preload** is off by default: it's hard to undo, and every subdomain must serve HTTPS first. Turn it on with `DJANGO_HSTS_PRELOAD=1` in `.env` only once you're sure, then submit the base name at hstspreload.org.

## Rate limits (ADR-0021)
| Where | Limit | Setting |
|---|---|---|
| Admin and customer sign-in, password links (Traefik, per IP) | 5 per second, bursts of 20 | compose labels |
| Xtream host (Traefik, per IP) | 10 per second, bursts of 50 | compose labels |
| Failed Xtream sign-ins | pause per username and network, per username overall (10×), per IP (doubling, up to a day); cached logins keep working | `xtream.auth_failures_per_username`, `xtream.auth_failures_per_ip`, `xtream.auth_failure_window_s` |
| Searches, playback starts (per customer, per minute) | 60, 30 | `security.rate_search_per_minute`, `security.rate_playback_start_per_minute` |
| TV app logins created or renewed, checkouts (per customer, per hour) | 10, 10 | `security.rate_device_credentials_per_hour`, `security.rate_checkout_per_hour` |
| TMDB searches and re-matches, backup runs (per admin) | 30 per minute, 6 per hour | `security.rate_admin_tmdb_search_per_minute`, `security.rate_backup_runs_per_hour` |
| CSP reports (Traefik, per IP) | 1 per second, bursts of 10 | compose labels |

Refused requests answer `429 RATE_LIMITED` with `Retry-After`.

## IP geolocation (DB-IP Lite)
Country rules and the sharing score use DB-IP's free country and ASN databases (CC BY 4.0: the admin's and the portal's About pages, and the evidence views, credit DB-IP). The worker downloads them on its first start, then checks daily at 04:50 and downloads each new monthly release (`geoip.auto_update`) into the `geoip-data` volume, validates it, and swaps it in atomically. **Settings → Integrations → IP geolocation** shows the release, looks up an address, and downloads now.
- **Local network addresses** never match country rules.
- **Unknown country** (an address the database doesn't know): `geoip.unknown_country_policy`, `deny` by default when country rules allow only some countries.
- **Missing database:** admins get a *GeoIpDatabaseMissing* message after an hour ([alerts.md](alerts.md#geoipdatabasemissing)).

## Customer two-factor sign-in (ADR-0024)
- **Optional, per customer:** in the portal under **Account → Two-factor sign-in**, after their password again. Once on, every new sign-in (the portal and apps on the API host) asks for the authenticator app's code with the password; apps already signed in stay signed in. TV logins (Xtream) are not affected: they have their own passwords.
- **Wrong codes** count like wrong passwords (django-axes) and against the account (10 in 15 minutes); they show on **Lockouts**, where they can be cleared.
- **Lost phone:** support opens the customer → **Security** → **Switch two-factor off** (*customers.edit*, then a code). Check that it is really the customer asking first (their email, phone, recent payments). It's audited as `customer.mfa_reset`; their next sign-in needs the password alone, and they can set it up again.

## Credential sharing
The sharing score and its flags are in [leaked-credential.md](leaked-credential.md#sharing-flags). Since ADR-0024 it also counts movie and episode sessions that were served far more bytes than their title explains (`antisharing.bytes_ratio`, `antisharing.bytes_min_mb` in Settings → Anti-sharing).

## Mail authentication: SPF, DKIM, DMARC (G-33)
Customers trust the service's emails (password links, new-login notices), so make forging them hard. In the DNS of the domain in **Settings → Email** (the *From* address):
1. **SPF:** a TXT record on the domain listing who may send, e.g. `v=spf1 include:<your provider's SPF domain> -all` (your mail provider documents its include).
2. **DKIM:** the provider gives one or more selector records (`<selector>._domainkey`); publish them exactly.
3. **DMARC:** a TXT record on `_dmarc.<domain>`: start with `v=DMARC1; p=none; rua=mailto:dmarc@<domain>`, read the reports for two to four weeks, then move to `p=quarantine` and finally `p=reject`.
Check with **Settings → Integrations → Email → Test**, then the headers of the received message (`spf=pass`, `dkim=pass`, `dmarc=pass`).

## Checks in `make ci` (ADR-0003, ADR-0021)
- `make audit`: pip-audit and `pnpm audit --prod` on the production dependencies. An accepted advisory goes into `scripts/audit-ignore.toml` with a reason and an expiry date.
- `make semgrep`: the project's own rules (`.semgrep/rules.yml`): credentials in logs, tokens in browser storage, `/internal` routes on public hosts, `csrf_exempt`, secrets compared with `==`, HTML without its own CSP, forbidden imports.
- `make zap`: a ZAP baseline (passive rules) against every dev host; accepted findings and their reasons are in `security/zap/plan.yaml`. The report is in `dist/zap/out/`.
- `renovate.json`: if you run Renovate, its proposals are grouped weekly, at least 14 days old, with digests pinned; each one still needs `make ci`.

## Privacy and data retention
Personal data is kept only as long as it is needed. A daily job (04:20) applies the admin's **Settings → Privacy**, and records what it removed in the audit log (`privacy.retention`).

| Data | Setting | Default | What happens |
|---|---|---|---|
| IP address and user agent of ended playback sessions | `privacy.session_ip_days` | 30 days | blanked; country and network stay for statistics |
| Playback sessions | `privacy.session_retention_days` | 365 days (min. 90) | deleted |
| Watch history (finished titles) | `privacy.watch_history_days` | 365 days | deleted |
| Resume points (unfinished titles) | `privacy.resume_days` | 730 days | deleted |
| Audit log | `privacy.audit_retention_days` | 730 days (min. 365) | deleted |
| Audit entries for billing, payments and subscriptions | `privacy.audit_billing_retention_days` | 2190 days, 6 years (min. 1826) | deleted |
| Notification log (sent, failed, skipped) | `privacy.outbox_retention_days` | 90 days | deleted |
| Failed admin sign-ins | `privacy.auth_log_retention_days` | 90 days | deleted |
| Networks, providers and countries seen per customer (sharing evidence) | `privacy.antisharing_retention_days` | 90 days (min. 7) | deleted; open flags keep their own summary |
| Payment providers' raw webhook payloads | `privacy.webhook_payload_days` | 365 days | emptied; the payment records stay |
| Expired admin and portal sessions | none | at once | deleted |

- **Invoices and payments themselves are never deleted by this job:** they are financial records with their own legal retention.
- The audit log is append-only: deleting from it is possible only through `audit_purge`, which refuses anything younger than the minimums above, even for an admin with database access.
- App tokens, password links and monitoring tickets live in redis-state with their own expiry.
- **Backups:** deleted data leaves the backups as they age out (database: 2 full weeks by default; file snapshots: up to 6 months).
- **Logs** never contain passwords or tokens (redaction in structlog, Nginx, Traefik and Alloy). With monitoring on, Loki keeps them for 30 days.
