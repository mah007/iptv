# Streaming plane: the media edge

Nginx + njs serving `media.<domain>` (SPEC §12). It verifies a signed token on every request, asks Django's `/internal/stream-auth` at most once a minute per token (heartbeat and kick check), and then serves bytes from local disk or from an S3/HTTP origin through a slice cache. It never sees credentials and never touches Postgres. The token format, the stream-auth contract and every policy below are pinned in [ADR-0007](../docs/adr/0007-media-token-and-edge.md).

| Path | What |
| --- | --- |
| `nginx/nginx.conf` | http block: byte serving, njs, maps, redacted JSON access log, stream-auth cache |
| `nginx/edge.conf` | local disk mode: token location, `alias` into the media root |
| `nginx/edge-s3.conf` | S3 origin mode: `slice 4m`, token-free cache key, origin error interception |
| `nginx/snippets/` | checks and headers shared by both modes |
| `nginx/templates/` | the deployment-specific parts, rendered from `EDGE_*` |
| `nginx/docker-entrypoint.d/40-edge-config.sh` | validates `EDGE_*` and the key file, renders the templates |
| `nginx/njs/token.js` | token verification, log redaction, response headers |
| `nginx/njs/check_keys.js` | key file check run by the entrypoint |
| `tools/sign_token.py` | reference signer, verifier and key tool (stdlib only) |
| `tests/` | `run.sh` end to end, `vectors.json` shared with the backend, njs unit tests |
| `ffmpeg/profiles.yaml` | transcoding profiles (SPEC §7.3, owned by the media pipeline) |

## Running the edge

The image is the official `nginx:1.30.5-alpine` (ships njs 1.0.1) with these mounts:

| Container path | Source |
| --- | --- |
| `/etc/nginx/nginx.conf` | `streaming/nginx/nginx.conf` (read-only) |
| `/etc/nginx/edge/` | `streaming/nginx/` (read-only) |
| `/docker-entrypoint.d/40-edge-config.sh` | `streaming/nginx/docker-entrypoint.d/40-edge-config.sh` |
| `/run/secrets/media_token_keys.json` | the key file (a Docker secret) |
| `/srv/media` | the media root, read-only (local mode only) |

It listens on 8080 (plain HTTP; TLS ends at Traefik or the CDN in front). It runs with a read-only root file system if `/etc/nginx/conf.d`, `/var/cache/nginx`, `/run` and `/tmp` are tmpfs, and needs no capabilities beyond `CHOWN`, `SETUID` and `SETGID` (`streaming/tests/run.sh` runs it exactly so). The master process reads the key file as root at start and on reload; without `DAC_OVERRIDE` the file must be readable by root by its mode or owner (Docker secrets are 0444).

| Variable | Default | Meaning |
| --- | --- | --- |
| `EDGE_AUTH_UPSTREAM` | required | `host:port` of Django's internal endpoint, e.g. `web:8000` |
| `EDGE_AUTH_HOST` | host of `EDGE_AUTH_UPSTREAM` | `Host` sent to stream-auth; must be in Django's `INTERNAL_HOSTS` |
| `EDGE_AUTH_CACHE_TTL` | `60s` | how long a 204 from stream-auth is reused per token |
| `EDGE_KEYS_FILE` | `/run/secrets/media_token_keys.json` | the key file |
| `EDGE_MODE` | `local` | `local` (disk) or `s3` (origin) |
| `EDGE_MEDIA_ROOT` | `/srv/media` | local mode: `<root>/<title>/<rendition>.<ext>` and `<root>/<title>/<rendition>/…` |
| `EDGE_ORIGIN_URL` | required in `s3` | `http(s)://host[:port][/prefix]`, no credentials; objects at `<prefix>/<title>/…` |
| `EDGE_MEDIA_CACHE_SIZE` | `10g` | s3 mode: slice cache size on local disk |
| `EDGE_REAL_IP_FROM` | none | proxies (CIDRs, comma or space separated) whose `X-Forwarded-For` is trusted, e.g. Traefik's network |
| `EDGE_CORS_ORIGINS` | none | portal and admin origins allowed to fetch HLS, subtitles and thumbnails, e.g. `https://app.example.com https://admin.example.com` (compose sets both) |
| `EDGE_ID` | container hostname | edge name in the access log and in `X-Edge-Id` |
| `EDGE_RESOLVER` | IPv4 nameservers in `/etc/resolv.conf` | DNS for upstream names (Docker: `127.0.0.11`) |
| `EDGE_RESOLVER_VALID` | `10s` | DNS cache time |
| `EDGE_REQUEST_ERROR_LOG_LEVEL` | `emerg` | request-scoped error log; lower levels log raw request lines, which hold tokens |

The entrypoint stops the container with a message naming the variable (and never a key) when a value or the key file is invalid. `GET /healthz` answers 200 while the loaded key file is usable.

## What an asset holds

Each media file's renditions live in one folder under `EDGE_MEDIA_ROOT`, named by its asset key (the token's `title`). The full layout is in ADR-0014 and `backend/apps/media/layout.py`:

| Path | Token rendition | What |
| --- | --- | --- |
| `compat.mp4` | `compat` | progressive H.264/AAC MP4 (sidecar subtitles as mov_text) |
| `source.<ext>` | `source` | link to a direct-play library file |
| `uhd.<ext>` | `uhd` | the UHD version (HEVC Main10, or a link to a suitable source) |
| `hls/master.m3u8`, `hls/v*/`, `hls/a*/` | `hls` | the SDR ladder: fMP4 rungs and audio renditions |
| `hls480/`, `hls720/` | `hls480`, `hls720` | the ladder capped at a plan's quality: links to the rungs that fit, so the token cannot reach a taller one |
| `hls2160/`, `hls2160/uhd/` | `hls2160` | the ladder plus the UHD rung |
| `subs/<key>.vtt`, `.srt`, `.m3u8` | (through `<scope>/subs`) | UTF-8 subtitles and their one-segment playlists |
| `thumbs/thumbs.vtt`, `sprite_*.jpg` | (through `<scope>/thumbs`) | scrubbing previews |

Every presentation and progressive scope holds `subs -> ../subs` and `thumbs -> ../thumbs`, so a playback URL's token also reaches `<scope>/subs/…` and `<scope>/thumbs/…`. Links are relative and stay inside the asset; nginx follows them (`disable_symlinks off`, its default). Playlists get `max-age=60`, everything else that succeeds is cached as immutable.

## Keys

```json
{"current": {"kid": "k2", "secret": "<base64url, 32-64 bytes>"},
 "previous": {"kid": "k1", "secret": "<base64url>"}}
```

Django signs with `current`; edges and stream-auth accept `current` and `previous`. To rotate, write the rotated file to every edge and reload (`nginx -s reload`) **before** giving it to Django, and wait longer than the longest token lifetime before the next rotation (ADR-0007):

```sh
python3 streaming/tools/sign_token.py genkeys --kid k1 > keys.json      # first key
python3 streaming/tools/sign_token.py rotate --kid k2 keys.json > new.json
python3 streaming/tools/sign_token.py check-keys new.json
python3 streaming/tools/sign_token.py sign --keys new.json --session <32 hex> \
    --title <asset> --rendition compat --ttl 7200 [--client-ip 203.0.113.7]
```

## Tests

```sh
bash streaming/tests/run.sh
```

It checks the vectors against both implementations (Python reference and njs, plus 600 fuzz cases), then runs both edge modes against a stub of stream-auth and of an S3 origin, and finally checks the captured logs. It uses standalone containers named `prework-*` on `127.0.0.1:18080-18082`, removed on exit. `tests/make_vectors.py` regenerates `tests/vectors.json`. The backend's M7 signer and stream-auth verifier must pass the same file.
