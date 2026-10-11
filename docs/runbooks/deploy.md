# Runbook: deploy to a single host

The small tier from SPEC §13: one server runs the whole stack behind Traefik, which gets TLS certificates from Let's Encrypt. Examples use the base name `tv.example.com`; the real server's details are kept out of the repository.

## The installer
`scripts/install.sh` does everything below, interactively:

```bash
curl -fsSL https://raw.githubusercontent.com/mah007/iptv/main/scripts/install.sh -o install.sh
sudo bash install.sh                                  # new server: questions, checks, install
sudo /opt/iptv/scripts/install.sh --update            # pull, add new .env keys, rebuild, migrate, check HTTPS
sudo /opt/iptv/scripts/install.sh --reconfigure       # change domain, admin email, media folder, monitoring
sudo /opt/iptv/scripts/install.sh --update --monitoring   # turn monitoring on for an existing server
bash install.sh --help                                # every option, unattended use (--yes)
```
- **Questions:** domain, owner admin (email, sign-in name, password or a generated one), media folder and sample titles, TMDB token, SMTP, monitoring (alert email and an optional Telegram bot and chat), ufw. TMDB, SMTP and the alert answers go into the admin's settings, where they are edited afterwards (**Settings → Integrations**); `--reconfigure` asks only for the domain, the admin email, the media folder and monitoring on or off.
- **Checks before it changes anything:** OS (Ubuntu 22.04/24.04, Debian 12/13), CPU, memory and disk, ports 80/443, internet access, DNS for every host, and no other installation's containers or volumes on the server.
- **Then:**
  1. Docker from Docker's repository, and the code in `/opt/iptv`;
  2. `.env` from `scripts/secrets.sh` plus the bootstrap answers (`DOMAIN`, `TV_HOST`, `ACME_EMAIL`, `MEDIA_ROOT`, `APP_COMMIT`, `MONITORING`);
  3. the stack and migrations (with the monitoring overlay when chosen), then TMDB, SMTP, the alert recipients and Telegram into the admin's settings (`manage.py set_setting KEY --stdin`; secrets encrypted, values never on a command line);
  4. `create_owner --username --email` (password from `OWNER_PASSWORD`, or generated);
  5. `ensure_libraries` for `movies/` and `series/`, `search_reindex`, and an HTTPS check per host.
- **Where things go:**
  - the log: `/var/log/smart-iptv-install.log`, with no secrets;
  - a generated admin password: shown once and kept in `/root/smart-iptv-admin.txt` (mode 600);
  - the install directory: recorded in `/etc/smart-iptv/install.conf`.
- **The public-IP check** (for DNS) asks api.ipify.org, falling back to ifconfig.co; skip it with `--skip-dns-check`.

The sections below are the same steps by hand, for reference and troubleshooting.

## Configuration: the admin, not `.env`
Everything an operator changes lives in the admin (ADR-0019):
- **Settings → Integrations:** TMDB, TheTVDB, email (SMTP), Stripe, Moyasar and Telegram, one card each with its status and a **Test** (send a test email to yourself, check a key, send a test Telegram message). Save, then Test.
- **Settings:** every other setting (branding and logos, playback, library, live TV, billing, monitoring...).
- **Settings → System:** read-only facts: version and commit, hosts, folders, which settings still come from `.env`, the bootstrap secrets and why they stay out of the settings.

`.env` keeps only bootstrap values: the domain and hosts, folders, data-store passwords, `DJANGO_SECRET_KEY` and `FIELD_ENCRYPTION_KEY` (which encrypts the secrets stored in the settings: back it up with `.env`; without it they must be entered again).

**Servers installed before ADR-0019** keep working: a TMDB token or SMTP settings in `.env` are the settings' fallback, shown as **From server config**. After `--update` the installer prints how many settings still come from `.env`. Press **Move into settings** on each (the value is copied on the server, encrypted, never sent to the browser), then remove the variable from `.env`:
```bash
docker compose ... exec web python manage.py settings_report   # names and variables, never values
```

## Prerequisites
- **DNS:** an A record for the base name **and** a wildcard, both pointing at the server. For example `tv.example.com` and `*.tv.example.com` → the server's IP.
- **Server:** Ubuntu 24.04 with ports 80 and 443 reachable from the internet (Let's Encrypt validates on port 80).
- **Packages:** `git`, `make`, `openssl`, `python3`, `curl`, plus Docker Engine with the Compose plugin from Docker's apt repository (see "Install Docker").
- **Code:** a clone of the repository in `/opt/iptv`.

## Hostnames
`DOMAIN` is the base name. Each host is a subdomain of it, except the Xtream host, which uses the base name itself so IPTV apps get the short URL.

| Host | Serves |
|---|---|
| `tv.example.com` (`TV_HOST`) | Xtream API for IPTV apps |
| `admin.tv.example.com` | Admin SPA (its `/api` goes to Django from M2) |
| `app.tv.example.com` | Customer portal |
| `api.tv.example.com` | REST API for external clients |
| `media.tv.example.com` | Nginx media edge: artwork and signed playback URLs (ADR-0007, ADR-0010) |
| `grafana.tv.example.com` | Monitoring, when enabled: Grafana, `/prometheus`, `/alertmanager`, admins only (ADR-0018) |

## Media folder
The libraries live in a host folder, `MEDIA_ROOT` in `.env` (default `/srv/media`), mounted read-only at `/media` in every container that reads media. Each library is a folder under it, for example `/srv/media/movies` and `/srv/media/series`, created in the admin under Libraries as `/media/movies` and `/media/series`. Transcoded renditions and artwork live in the `media-data` volume.

For a demo without real content, generate legal synthetic samples (FFmpeg test patterns, about 20 MB):
```bash
scripts/sample_media.sh /srv/media
```

## First deployment
```bash
cd /opt/iptv
scripts/secrets.sh                                   # .env with fresh secrets, generated on the server
sed -i 's/^DOMAIN=.*/DOMAIN=tv.example.com/' .env
grep -q '^TV_HOST=' .env || echo 'TV_HOST=tv.example.com' >> .env
echo 'ACME_EMAIL=ops@example.com' >> .env                # Let's Encrypt contact (optional)
docker compose --project-directory . -f docker/compose.yml -f docker/compose.prod.yml \
  up -d --build --wait --wait-timeout 900
docker compose --project-directory . -f docker/compose.yml -f docker/compose.prod.yml \
  exec -T web python manage.py migrate --noinput
# The owner admin (roles only, no demo data). The password is printed once:
# keep it in a password manager. At first sign-in the admin enrols a TOTP app.
docker compose --project-directory . -f docker/compose.yml -f docker/compose.prod.yml \
  exec -T web python manage.py create_owner --username admin --email ops@example.com
```
Secrets are generated on the server and never leave it. Back up `/opt/iptv/.env` and `/opt/iptv/secrets/` securely: the database volumes only work with the passwords in `.env`, and `secrets/media_token_keys.json` signs every playback URL.

Transcoding runs on the CPU (`transcoder` service). A server with an NVIDIA or Intel GPU can add `docker/compose.gpu-nvidia.yml` or `docker/compose.gpu-intel.yml` to every compose command.

## Update to the latest `main`
```bash
cd /opt/iptv && git pull --ff-only
scripts/secrets.sh                                   # appends keys new in .env.example; never changes existing ones
docker compose --project-directory . -f docker/compose.yml -f docker/compose.prod.yml \
  up -d --build --wait --wait-timeout 600
docker compose --project-directory . -f docker/compose.yml -f docker/compose.prod.yml \
  exec -T web python manage.py migrate --noinput
```
`scripts/secrets.sh` only appends keys that are new in `.env.example` and creates `secrets/media_token_keys.json` if it's missing, so running it on every update is safe.

There's a single web replica, so an update causes a few seconds of downtime. Zero-downtime rolling updates come with the production hardening in M14–M15.

## Monitoring (M13, ADR-0018)
An opt-in overlay: Prometheus, Grafana, Loki, Grafana Alloy, Alertmanager and the exporters (node, cAdvisor, PostgreSQL, Valkey, nginx, blackbox). The installer asks for it (default yes with 8 GB of memory or more) and records the choice as `MONITORING=1` in `.env`; `--update` keeps it.

**Turn it on for a server installed without it:**
```bash
sudo /opt/iptv/scripts/install.sh --update --monitoring   # or --reconfigure, which asks
```
By hand, add the two overlay files to every compose command and run `scripts/secrets.sh` first (it appends the new keys and writes the overlay's Docker secrets into `secrets/monitoring/`):
```bash
cd /opt/iptv && scripts/secrets.sh
docker compose --project-directory . -f docker/compose.yml -f docker/compose.prod.yml \
  -f docker/compose.monitoring.yml -f docker/compose.monitoring.prod.yml up -d --build --wait --remove-orphans
```

**Open it** from the admin: **Monitoring** in the sidebar (permission `monitoring.view`; owners, admins and viewers have it). Each button opens `grafana.<domain>` with a one-time link, signed in for `monitoring.session_hours` (8 by default). Nothing on that host answers without it: not Grafana, `/prometheus` or `/alertmanager`. Suspending an admin or removing the permission closes their access within a minute.

**Alerts.** Every alert goes to Django, which sends it as the admin's **Settings → Monitoring** say: recipients, which severities go where, on or off; the SMTP server and the Telegram bot and chat are under **Settings → Integrations**. For the few alerts that mean Django itself can't deliver (web, PostgreSQL or Valkey down, delivery failing), Alertmanager also sends them itself, with the same settings: it fetches them from Django every minute (`/internal/alert-fallback`), keeps the last copy while web is down, and reloads when they change (ADR-0019). Older installations' `ALERT_EMAIL_TO` and `ALERT_TELEGRAM_*` in `.env` apply only until that first fetch.

**Prove it works (the fire drill, SPEC §17):** admin → **Monitoring** → **Send a test alert**, or `docker compose ... exec web python manage.py fire_drill`. The FireDrill alert arrives by email and Telegram within about 2 minutes and resolves 10 minutes later (or at once with **Stop the test alert** / `fire_drill --stop`). Record the date in the go-live checklist.

**What it costs on a 16-core, 15 GB host:** about 1.5–2 GB of memory (limits add up to about 5 GB), under a third of a core on average, and up to about 16 GB of disk: Prometheus keeps 30 days and at most `PROMETHEUS_RETENTION_SIZE` (10 GB), Loki keeps 30 days (`LOKI_RETENTION_PERIOD`). Those, like the host names, are set at installation and need a restart; the admin's Monitoring page shows them.

**When an alert fires:** [alerts.md](alerts.md) explains each one: what it means, what users notice, what to check and what to do.

## Verify
```bash
curl -fsS https://tv.example.com/health                    # {"status": "ok"}
curl -sI http://tv.example.com/health | head -3           # 308 Permanent Redirect to https
curl -sI https://admin.tv.example.com/ | grep -i strict   # HSTS header present
curl -s -o /dev/null -w '%{http_code}\n' https://tv.example.com/internal/health/ready  # 404: internal stays internal
curl -fsS https://media.tv.example.com/healthz                # the media edge
curl -s -o /dev/null -w '%{http_code}\n' https://media.tv.example.com/v/x/y.mp4  # 403: unsigned playback refused
```
With a test customer's IPTV login in `XC_USER` and `XC_PASS` (never on the command line), the Xtream contract suite checks the live server, play redirects included:
```bash
XC_USER=... XC_PASS=... uvx --with 'jsonschema==4.26.0' python compat/validate.py --live https://tv.example.com --play
```
On the server, `docker compose ... ps` shows every service as `healthy`.

## Install Docker (Ubuntu 24.04)
Use Docker's official apt repository, not the distribution's `docker.io`:
```bash
apt-get update && apt-get install -y ca-certificates curl
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update && apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

## Troubleshooting
- **A new hostname doesn't resolve on one network but works elsewhere:** a resolver (often the home router) cached "no such host" from before the DNS record existed. Check with a public resolver (`https://dns.google/resolve?name=<host>`), then wait for the negative cache to expire or restart that resolver.
- **A host serves `TRAEFIK DEFAULT CERT`:** no router matches it. Check the router rules in `docker compose ... config` and the ACME lines in `docker compose ... logs traefik`.

## Not covered yet
- Backups and the restore drill (M14). **Until then, nothing on this server is backed up.**
- A firewall policy (M14). Today only 22, 80 and 443 are listening; the databases are on Docker's internal network and never published.
