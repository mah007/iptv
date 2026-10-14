# Runbook: Bunny CDN in front of an edge

> **Verified against vectors only.** The token code matches Bunny's reference implementation, run as an oracle on our URL shapes, and Bunny's 15 published test vectors (`backend/apps/edges/tests/test_bunny.py`). The edge's `/c/` origin location is covered by `make edge-test`. **No live Bunny pull zone was used.** Bunny's panel names below follow its documentation and may differ slightly. Try it with canary customers first.

A Bunny pull zone caches renditions at Bunny's edge locations and serves viewers from there (M15, ADR-0022). The control plane signs each viewer's URL with Bunny's token authentication. Bunny fetches what it lacks from one of our edges (its *origin edge*), proving itself with a shared secret.

## The trade-off
Bunny serves cache hits without asking us, so the control plane's per-request checks don't reach those requests:
- **Concurrency:** a CDN session takes its slot at start, like any session. The slot then lapses after the heartbeat TTL (Settings → Playback), because no heartbeat arrives. A customer could start more streams than the plan allows by waiting out that TTL.
- **Kicks:** a kick or a subscription ending bites only when the session's token expires (its runtime plus `playback.token_ttl_vod_s`).
- **Live TV** never goes through the CDN.

Keep a CDN zone for audiences where that is acceptable (its **Audience** can be a set of canary customers), or for peaks.

## 1. Secrets on the control plane
admin → **Settings → Integrations → Bunny CDN**:
- **Token key** (`cdn.bunny_token_key`): the pull zone's *Token Authentication key* (pull zone → Security → Token Authentication).
- **Origin secret** (`cdn.origin_secret`): a long random value you create, e.g. `openssl rand -base64 33`. Bunny sends it to our edge with every pull, and our edge refuses pulls without it.
- **Bind tokens to the viewer's address** (`cdn.bunny_ip_binding`): leave it **off** unless the portal and the APIs are reached over IPv4 only. Bunny turns IPv6 off for zones with token authentication, so a viewer whose browser reached us over IPv6 would be refused at Bunny.

Both secrets are stored encrypted, and their changes are audited without values.

## 2. The origin edge
Pick the edge Bunny pulls from:
- **A standalone edge (recommended).** It receives the origin secret on its control channel within 15 s. Turn pulls on in its `.env`:
  ```bash
  cd /opt/iptv && echo 'EDGE_CDN=on' >> .env && docker compose -f docker/compose.edge.yml up -d --force-recreate
  ```
- **The control host's local edge.** It has no control channel, so it needs the secret in a file: `secrets/cdn_origin_secret` (mode 600). Add an override for `nginx-stream`:
  - `EDGE_CDN: "on"` and `EDGE_CDN_SECRET_FILE: /run/secrets/cdn_origin_secret`;
  - that file as a Docker secret.

  Keep the file and the setting equal.

The origin location is `https://<edge>/c/<title>/<path>`. It answers 403 without the secret, maps paths exactly as a signed token would (a capped presentation reaches only its rungs), serves from the same cache, and skips the per-viewer rate limit. The zone caches for its viewers.

## 3. The pull zone (Bunny's panel)
- **Origin URL:** `https://media2.example.com/c` (the origin edge's address plus `/c`).
- **Security → Token Authentication:** on.
- **Edge Rules:** add a rule that sets the request header `X-Origin-Auth` to the origin secret on requests to the origin, for all URLs.
- **Caching:** respect the origin's Cache-Control. Our edge sends a year for segments and files (they never change: a re-encode gets a new path) and 60 s for playlists and subtitles.
- **CORS:** add CORS headers for `m3u8, m4s, ts, mp4, vtt, jpg` (the web player loads them from another origin).
- **Hostname:** the zone's `*.b-cdn.net` name, or your own (e.g. `cdn.example.com`, with Bunny's certificate).
- **Optional:** block countries or IPv6 in the zone itself.

## 4. The zone in the admin
admin → **Edges** → **Add edge**:
- **Kind:** CDN.
- **Address:** the zone's hostname.
- **Origin edge:** the edge from step 2.
- **Weight**, and **Audience** (start with canary customers).

Then admin → **Settings → Integrations → Bunny CDN → Test** runs the probe end to end: a URL signed with Bunny's token, fetched through the zone, which pulls `/c/edgeprobe/probe.txt` from the origin edge with the secret. The Edges page's **Test** on the zone does the same.

The zone takes sessions only while Bunny's token key is set and its origin edge is healthy. Otherwise, selection skips it.

## How a viewer's URL looks
- **HLS:** `https://cdn.example.com/bcdn_token=HS256-…&token_path=%2F<title>%2Fhls720%2F&expires=<exp>/<title>/hls720/master.m3u8`. The token covers the presentation's folder, so playlists and segments resolve relative to it.
- **A file:** `https://cdn.example.com/bcdn_token=HS256-…&expires=<exp>/<title>/compat.mp4`.

The expiry is the session's. `<title>` is the published key on an S3 origin edge, and the storage key on a disk edge.

## Troubleshooting
| Symptom | Cause and fix |
|---|---|
| Test fails at **media** with 403 | Bunny refused the token: the token key in Settings differs from the zone's, or IP binding is on and the address differs. |
| Test fails with 403 from our edge | The edge rule's header is missing or differs from `cdn.origin_secret`. On a standalone edge: `EDGE_CDN=on`, and it polled since the secret changed. |
| 404 for a capped rung | Expected: the presentation's ceiling. |
| The zone never gets sessions | Its origin edge isn't healthy, the token key is empty, or its weight or audience exclude everyone. |
| Viewers keep playing after a kick | Expected until the token expires (see the trade-off). Use a shorter `playback.token_ttl_vod_s` if that matters. |

## Turning it off
Disable the zone on the Edges page: new sessions go to the other edges at once. Sessions already on the CDN finish with their tokens. Then remove the zone at Bunny, and clear the two secrets.
