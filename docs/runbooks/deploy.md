# Runbook: deploy to a single host

The small tier from SPEC §13: one server runs the whole stack behind Traefik, which gets TLS certificates from Let's Encrypt. Examples use the base name `tv.example.com`; the real server's details are kept out of the repository.

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

## First deployment
```bash
cd /opt/iptv
scripts/secrets.sh                                   # .env with fresh secrets, generated on the server
sed -i 's/^DOMAIN=.*/DOMAIN=tv.example.com/' .env
grep -q '^TV_HOST=' .env || echo 'TV_HOST=tv.example.com' >> .env
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
curl -fsS https://tv.example.com/health                    # {"status": "ok"}
curl -sI http://tv.example.com/health | head -3           # 308 Permanent Redirect to https
curl -sI https://admin.tv.example.com/ | grep -i strict   # HSTS header present
curl -s -o /dev/null -w '%{http_code}\n' https://tv.example.com/internal/health/ready  # 404: internal stays internal
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
- Monitoring and alerting (M13).
- Backups and the restore drill (M14). **Until then, nothing on this server is backed up.**
- A firewall policy (M14). Today only 22, 80 and 443 are listening; the databases are on Docker's internal network and never published.
