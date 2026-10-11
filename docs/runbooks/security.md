# Runbook: server security

How the server is hardened (ADR-0020), what to check after installing, and the day-to-day commands: firewall and SSH, TLS, CrowdSec, the container and data-store boundaries, and personal-data retention. Other runbooks for when something goes wrong:
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
- `.env` (mode 600) holds the bootstrap secrets; `secrets/` holds the Docker secrets (media token keys, backup passphrase, agent token, monitoring and CrowdSec keys).
- Both are backed up, encrypted, nightly. Integration secrets (TMDB, SMTP, payments, Telegram, the backup bucket) live encrypted in the settings (ADR-0019).
- Every key and password can be rotated with `scripts/rotate-keys.sh` ([rotate-keys.md](rotate-keys.md)).

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
| Payment providers' raw webhook payloads | `privacy.webhook_payload_days` | 365 days | emptied; the payment records stay |
| Expired admin and portal sessions | none | at once | deleted |

- **Invoices and payments themselves are never deleted by this job:** they are financial records with their own legal retention.
- The audit log is append-only: deleting from it is possible only through `audit_purge`, which refuses anything younger than the minimums above, even for an admin with database access.
- App tokens, password links and monitoring tickets live in redis-state with their own expiry.
- **Backups:** deleted data leaves the backups as they age out (database: 2 full weeks by default; file snapshots: up to 6 months).
- **Logs** never contain passwords or tokens (redaction in structlog, Nginx, Traefik and Alloy). With monitoring on, Loki keeps them for 30 days.
