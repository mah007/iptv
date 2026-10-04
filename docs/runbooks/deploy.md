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
| `media.tv.example.com` | Nginx media edge: artwork and signed playback URLs (ADR-0007, ADR-0010) |

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
docker compose --project-directory . -f docker/compose.yml -f docker/compose.prod.yml \
  up -d --build --wait --wait-timeout 900
docker compose --project-directory . -f docker/compose.yml -f docker/compose.prod.yml \
  exec -T web python manage.py migrate --noinput
# The owner admin (roles only, no demo data). The password is printed once:
# keep it in a password manager. At first sign-in the admin enrols a TOTP app.
docker compose --project-directory . -f docker/compose.yml -f docker/compose.prod.yml \
  exec -T web python manage.py create_owner
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

There's a single web replica, so an update causes a few seconds of downtime. Zero-downtime rolling updates come with the production hardening in M13–M15.

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
- Monitoring and alerting (M13).
- Backups and the restore drill (M14). **Until then, nothing on this server is backed up.**
- A firewall policy (M14). Today only 22, 80 and 443 are listening; the databases are on Docker's internal network and never published.
