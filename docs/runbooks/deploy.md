# Runbook: deploy to a single host

The small tier from SPEC §13: one server runs the whole stack behind Traefik, which gets TLS certificates from Let's Encrypt. This runbook is first used for `tv.mah007.net`.

## Prerequisites
- **DNS:** an A record for the base name **and** a wildcard, both pointing at the server. For example `tv.mah007.net` and `*.tv.mah007.net` → `62.84.182.71`.
- **Server:** Ubuntu 24.04 with ports 80 and 443 reachable from the internet (Let's Encrypt validates on port 80).
- **Packages:** `git`, `make`, `openssl`, `python3`, `curl`, plus Docker Engine with the Compose plugin from Docker's apt repository (see "Install Docker").
- **Code:** a clone of the repository in `/opt/iptv`.

## Hostnames
`DOMAIN` is the base name. Each host is a subdomain of it, except the Xtream host, which uses the base name itself so IPTV apps get the short URL.

| Host | Serves |
|---|---|
| `tv.mah007.net` (`TV_HOST`) | Xtream API for IPTV apps |
| `admin.tv.mah007.net` | Admin SPA (its `/api` goes to Django from M2) |
| `app.tv.mah007.net` | Customer portal |
| `api.tv.mah007.net` | REST API for external clients |

## First deployment
```bash
cd /opt/iptv
scripts/secrets.sh                                   # .env with fresh secrets, generated on the server
sed -i 's/^DOMAIN=.*/DOMAIN=tv.mah007.net/' .env
grep -q '^TV_HOST=' .env || echo 'TV_HOST=tv.mah007.net' >> .env
docker compose --project-directory . -f docker/compose.yml -f docker/compose.prod.yml \
  up -d --build --wait --wait-timeout 600
docker compose --project-directory . -f docker/compose.yml -f docker/compose.prod.yml \
  exec -T web python manage.py migrate --noinput
```
Secrets are generated on the server and never leave it. Back up `/opt/iptv/.env` securely: the database volumes only work with the passwords in it.

## Update to the latest `main`
```bash
cd /opt/iptv && git pull --ff-only
docker compose --project-directory . -f docker/compose.yml -f docker/compose.prod.yml \
  up -d --build --wait --wait-timeout 600
docker compose --project-directory . -f docker/compose.yml -f docker/compose.prod.yml \
  exec -T web python manage.py migrate --noinput
```
If a release adds keys to `.env.example`, run `scripts/secrets.sh` again: from M2 it appends missing keys without touching existing ones.

There's a single web replica, so an update causes a few seconds of downtime. Zero-downtime rolling updates come with the production hardening in M13–M15.

## Verify
```bash
curl -fsS https://tv.mah007.net/health                     # {"status": "ok"}
curl -sI http://tv.mah007.net/health | head -3            # 308 redirect to https
curl -sI https://admin.tv.mah007.net/ | grep -i strict    # HSTS header present
curl -s -o /dev/null -w '%{http_code}\n' https://tv.mah007.net/internal/health/ready   # 404: internal stays internal
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

## Not covered yet
- Monitoring and alerting (M13).
- Backups and the restore drill (M14). **Until then, nothing on this server is backed up.**
- A firewall policy (M14). Today only 22, 80 and 443 are listening; the databases are on Docker's internal network and never published.
