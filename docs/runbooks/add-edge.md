# Runbook: add a media edge on another server

A standalone edge is a second (or tenth) server that serves media to players (M15, ADR-0022). It runs nginx with its own Let's Encrypt certificate, and it signs in to this server (the *control plane*) with its own secret. Players reach it only through the signed links the control plane hands out, and each new playback session is sent to an edge by its health, weight and load.

**What stays on the control host:** live TV, the admin, the portal, the APIs, the database and transcoding. An edge only serves video-on-demand renditions.

## Before you start
- **Something for the edge to serve**, one of:
  - **The S3 origin (recommended).** Set it up in admin → **Settings → Storage**: the bucket, the publisher's key and the edges' read-only key, then **Test** on **Settings → Integrations → S3 origin**. Turn publishing on and wait until `manage.py publish_media --status` shows the library published. The edge keeps what it serves in its own cache (`--edge-cache-size`, 50 GB by default).
  - **A full copy of the renditions on the edge's disk** (storage "disk"). You keep it in sync yourself (below).
- **A server for the edge:**
  - Ubuntu 22.04/24.04 or Debian 12/13, x86_64;
  - 1 CPU and 1 GB of memory at least (2 and 2 GB recommended);
  - a fast disk with room for the cache;
  - the uplink you want it to serve.
- **A DNS name for it**, with an A record pointing at its address, e.g. `media2.example.com`. It doesn't have to be under the platform's domain.
- **Ports 80 and 443 reachable from the internet.** Let's Encrypt checks port 80.
- **The control plane up to date** (`install.sh --update`): it answers edges on `https://edges.<domain>`.

## 1. Add it in the admin
admin → **Edges** → **Add edge**:
- **Kind:** standalone.
- **Edge id:** a short name, e.g. `media2`.
- **Address:** `https://media2.example.com`.
- **Storage:** origin (the S3 bucket) or disk.
- **Weight:** how many new sessions it gets relative to the others (100 is the local edge's).
- **Capacity:** its uplink, in Mbit/s. Above 90% of it (`edges.max_utilisation_pct`) it takes no new sessions.
- **Audience:** start with **Customers**, a few canary customers, before everyone.

**Save.** The dialog shows the edge's **secret once**, with the installer command. Keep the dialog open, or copy the secret to a password manager. If it is lost, **New secret** makes another.

If the edge's address is outside the platform's domain, the admin adds it to the apps' Content-Security-Policy (`security.csp_extra_media_origins`). It applies after the next `install.sh --update` on the control plane, or a restart of the `frontend` service. Until then, the web player can't load media from that edge (Xtream apps aren't affected).

## 2. Install it
On the edge's server, as root:
```bash
curl -fsSL https://raw.githubusercontent.com/mah007/iptv/main/scripts/install.sh -o install.sh
sudo bash install.sh --edge --control-url https://edges.tv.example.com --edge-id media2 --edge-domain media2.example.com
```
The installer:
1. **Asks for the secret** (hidden). To run it unattended, pass the secret as `EDGE_TOKEN='…'` and add `--yes`.
2. **Checks the secret** against the control plane, and learns the storage, the origin's address (never its keys) and the web origins for CORS.
3. **Checks this server:** the resources, ports 80 and 443, and that the DNS name points here.
4. **Installs Docker** if needed, gets the code into `/opt/iptv`, and writes `.env` and `secrets/edge_token` (both mode 600).
5. **Starts the edge** (`docker/compose.edge.yml`, project `smart-iptv-edge`) and waits for its certificate.

For a disk edge, it also asks for the folder that holds the copy (`--media-root`).

**What is on the edge server:** only its own secret. The media signing keys, the origin's read-only key and the CDN origin secret arrive on its control channel every 15 s and stay in memory. Rotating them on the control plane reaches the edge by itself.

## 3. Check it
- admin → **Edges**: within a minute the edge shows **healthy**, its key ids and its connections. Press **Test**, which runs:
  - **health** (`/healthz` over HTTPS);
  - **media** (a signed probe object through stream-auth);
  - **report** (its last poll);
  - **origin credentials** (for an origin edge).
- **Play something as a canary customer** in the portal and in an IPTV app. The session's edge shows in admin → **Live sessions**.
- **Kick that session** from Live sessions: the player stops within a minute (stream-auth is cached 60 s per token on every edge).

## 4. Open it up
- Set **Audience** to everyone, and raise the weight step by step. Selection is sticky per session, so changing weights moves only new sessions.
- The **Edges** dashboard (Grafana) shows each edge's egress. `EdgeDown` and `EdgeOverCapacity` alert on it ([alerts.md](alerts.md#edgedown)).

## Day to day
- **Update an edge:** `sudo /opt/iptv/scripts/install.sh --update` on its host, after updating the control plane.
  - The edge restarts only if its configuration changed: a few seconds without it, and players retry.
  - To be gentle, set it **Draining** first (no new sessions), wait for its connections to fall, update, then undo Draining.
- **Rotate the media signing key** with `scripts/rotate-keys.sh media-token` on the control plane ([rotate-keys.md](rotate-keys.md)). It waits until every enabled standalone edge reports the new key before signing with it, so a down edge holds the rotation: disable it first.
- **A new edge secret** (for example, if its host was compromised):
  1. **New secret** on the Edges page. The old secret stops working at once.
  2. On the edge's host: `sudo EDGE_TOKEN='…' /opt/iptv/scripts/install.sh --reconfigure` (or type it when asked).
- **Remove an edge:**
  1. Set it **Draining**; sessions finish within their token lifetime.
  2. **Disable** it, then delete it on the Edges page.
  3. On its host: `cd /opt/iptv && docker compose -f docker/compose.edge.yml down`.

## A disk edge: keeping the copy in sync
A disk edge serves `<folder>/<asset>/…` like the control host's `media-data` volume (`/data/renditions` inside it).
- Copy it with rsync over SSH from the control host. Run this on the edge's host, from cron, every few minutes:
  ```bash
  rsync -a --delete --link-dest=/srv/media/renditions root@control.example.com:/var/lib/docker/volumes/smart-iptv_media-data/_data/renditions/ /srv/media/renditions/
  ```
- **The cost:** a new or re-encoded title is missing on that edge until the next sync, so playback of it fails there. Selection doesn't know what a disk edge holds. An origin edge avoids this, because it only gets sessions for published renditions, which is why S3 is the recommended storage.

## Troubleshooting
| Symptom | Cause and fix |
|---|---|
| The installer says the control plane doesn't know the edge (401) | Wrong id or secret, or the edge is disabled. Check the Edges page, or use **New secret**. |
| **never seen** / **down** on the Edges page | The edge can't reach `https://edges.<domain>`. Check its DNS, its firewall (outbound 443), and `docker compose -f docker/compose.edge.yml logs edge`. |
| **keys outdated** | It holds old signing keys: it isn't polling. Restart it. |
| **waiting for origin** (CDN zone) | Its origin edge isn't healthy. |
| The certificate isn't issued | Port 80 is blocked, or the DNS name doesn't point at the edge yet. It retries by itself. Check with `docker compose -f docker/compose.edge.yml logs edge \| grep -i acme`. |
| The web player fails, Xtream apps work | CSP or CORS. The edge's origin must be in `security.csp_extra_media_origins` (restart `frontend` on the control plane). The portal's origin is in the edge's `EDGE_CORS_ORIGINS`, refreshed by `install.sh --update` on the edge. |
| Every session still plays from the local edge | The edge isn't eligible. Check its status, weight, audience and capacity. An origin edge also needs the title published (`publish_media --status`). |
