# ADR-0007: Media token format and the Nginx media edge

- **Status:** Accepted
- **Date:** 2026-10-03
- **Milestone:** M7 (edge built ahead of the backend)

## Context
Media bytes are served by an Nginx edge on `media.<domain>` (SPEC §3, §12). Hard rules: media requests never touch Postgres, the edge verifies an HMAC token, the cached `auth_request` touches Redis only, storage paths are never exposed, and tokens and credentials are never logged. SPEC §7.4 sketches the token as "compact base64url of `kid.session.title.rendition.exp[.ip24].sig`, `sig = HMAC-SHA256(key[kid], payload)`", with current and previous kids accepted, an optional /24 binding, and a stream-auth endpoint that re-checks the token and the kick flag. Three implementations have to agree byte for byte: Django's signer (M7), Django's stream-auth check (M7) and the edge's njs verifier. So every detail is pinned here, and `streaming/tests/vectors.json` is the shared test.

## Decision

### 1. Token
A token is a 6- or 7-field ASCII string, each field URL-safe by its grammar, joined by `.`:

```
kid.session.title.rendition.exp[.net].sig
k2.0192f3a4b5c67d8e9f00112233445566.0192f3a4-b5c6-7d8e-9f00-1122334455aa.compat.1767225600.Xq…(43)
```

| Field | Grammar (whole field) | Meaning |
| --- | --- | --- |
| `kid` | `[A-Za-z0-9_-]{1,16}` | key id, looked up in the key file |
| `session` | `[0-9a-f]{32}` | playback session id: the first 32 hex digits (128 bits) of SPEC's `sha256(user_id\|device_id\|title_ref)`. The backend uses exactly this string in `sess:`, `kick:` and `conc:` |
| `title` | `[A-Za-z0-9_-]{1,64}` | the title's storage key: its directory under the media root (or origin prefix). A canonical UUID fits |
| `rendition` | `[A-Za-z0-9_-]{1,32}` | the rendition the token grants (`compat`, `uhd`, `hls`, …) |
| `exp` | `[1-9][0-9]{0,11}` | expiry, Unix seconds, decimal, no leading zero |
| `net` | `[0-9a-f]{6}` or `[0-9a-f]{16}` | optional client network binding (SPEC's `ip24`) |
| `sig` | `[A-Za-z0-9_-]{43}` | signature |

- **"Compact base64url"** means the compact form of JWS: `.`-joined URL-safe parts with a base64url signature. There is no outer base64url layer. The fields are not secret, and wrapping them would add a third to the length and a decode step.
- **Signed bytes:** the token's ASCII bytes before the last `.`, that is `kid.session.title.rendition.exp[.net]`, kid included. `sig` = base64url without padding (RFC 4648 §5) of `HMAC-SHA256(secret[kid], those bytes)`. The signer signs exactly what it sends, so there is no second canonical form (SPEC §3's `|`-joined notation is descriptive).
- **`net`:** IPv4 binds the /24 as six lowercase hex digits of the first three octets (`203.0.113.7` → `cb0071`). IPv6 binds the /64 as 16 lowercase hex digits of the first eight bytes (`2001:db8:1:2::7` → `20010db800010002`). An IPv4-mapped address (`::ffff:a.b.c.d`) counts as IPv4. Scoped addresses (`%zone`) are not client addresses. Families never match each other. The binding is optional and off by default (a setting).
- **Length:** at most 256 characters (the field grammars allow 221).
- **Scope:** a token for rendition `R` reaches the file `R.<ext>` (one extension of `[A-Za-z0-9]{1,8}`) or anything under `R/`, relative to its title. The request path after the token (the *tail*) has 1 to 8 segments of `[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}`, so no empty, hidden, `.` or `..` segment, and the last segment names a file with an extension. Storage path = `<root>/<title>/<tail>`.
- **URLs:** `https://media.<domain>/v/<token>/<tail>`. The Xtream redirect is `/v/<token>/compat.mp4`, not SPEC §3's illustrative `/v/{token}/{xc_id}.mp4`: the tail names a stored file, so the edge needs no lookup. An HLS rendition keeps its master, variant playlists, segments, audio and subtitle groups under one directory (`hls/`) with relative URIs, so one token covers the presentation.

### 2. Verification (edge and stream-auth, same order and verdicts)
1. grammar → `malformed`
2. tail grammar → `bad_path`
3. kid in the key set → `unknown_kid`
4. constant-time comparison of the received `sig` with the base64url of the recomputed MAC (so a non-canonical encoding of the right bytes fails) → `bad_signature`
5. `now >= exp + 30` → `expired`. The 30 s skew allowance covers an edge clock ahead of the signer's; NTP keeps both far closer.
6. `exp > now + 7 days` → `exp_too_far`. This catches a signer that writes milliseconds; real lifetimes are runtime + 2 h (VOD) and 6 h (live).
7. `net` present and ≠ the client's `net` → `ip_mismatch`. The client address is `$remote_addr` after the trusted-proxy step (`EDGE_REAL_IP_FROM`).
8. tail outside the rendition → `scope`

Clients see `X-Reason: expired`, `ip_mismatch`, `invalid` (any other verdict) or `unavailable`. The access log keeps the exact verdict. `streaming/tools/sign_token.py` is the reference (stdlib only); `token.js` and the backend mirror it.

### 3. Keys and rotation
- **Key file** (JSON, one Docker secret mounted into `web` and every edge):
  `{"current": {"kid": "k2", "secret": "<b64url>"}, "previous": {"kid": "k1", "secret": "<b64url>"} | null}`.
  - A secret is canonical unpadded base64url of 32-64 random bytes; new keys are 32.
  - Kids differ, and unknown fields are rejected.
  - Error messages name fields, never values.
- **Signing and loading:** Django signs with `current`. Verifiers accept `current` and `previous`. The edge loads the file in the master process (`js_preload_object`) at start and on `nginx -s reload`, after the entrypoint has validated it (`check_keys.js`).
- **Rotation** (`sign_token.py rotate`, mirrored by `scripts/rotate_keys.py`):
  1. Make the new file: a new `current`, with the old `current` as `previous`.
  2. Install it on every edge and reload.
  3. Only then give it to Django.
  4. Wait longer than the longest token lifetime (at least 6 h) before rotating again; a token whose kid has dropped out fails.
  - **Leaked key:** rotate twice in a row. Every outstanding token dies, and players fetch new ones through Xtream or the API.

### 4. stream-auth contract (`GET /internal/stream-auth`)
- **When:** only for requests whose token passed the edge's checks.
- **Request:** `GET http://<EDGE_AUTH_UPSTREAM>/internal/stream-auth`, no body, with these headers:
  - `Host: <EDGE_AUTH_HOST>` (default `web`, which is in `INTERNAL_HOSTS`);
  - `X-Original-URI` (the raw request URI: `/v/<token>/<tail>[?query]`);
  - `X-Real-IP` (the client address the edge used);
  - `X-Request-ID` and `X-Edge-Id`.

  No client header is forwarded (cookies, Authorization, Range, X-Forwarded-For), and the endpoint is not reachable through Traefik. `X-Bytes` is not sent: the edge only knows the bytes after the response, and they are in the access log (`bytes_sent`).
- **Django:** re-verifies the token by §2 (defence in depth), checks `kick:{session}`, refreshes `conc` and `sess` (TTL 120 s), then answers:
  - **204** to allow;
  - **403** with `X-Reason: <[a-z_]{1,32}>` (e.g. `kicked`, `session_ended`) to deny; the edge passes the reason to the client.
  - Any other status, or a timeout (connect 2 s, read 5 s), makes the edge answer **503** with `Retry-After: 5` and `X-Reason: unavailable`. It fails closed and never serves media without a 2xx. The one exception is a 401, which nginx would pass through as 401, so Django never sends one.
- **Cache:** a 204 is reused for `EDGE_AUTH_CACHE_TTL` (default **60 s**) per token, keyed by the SHA-256 of the token (the cache stores no tokens).
  - 403s and errors are never cached. Stale answers are never used. Django's `Cache-Control`, `Set-Cookie`, `Vary` and `X-Accel-*` are ignored.
  - Concurrent misses are coalesced (`proxy_cache_lock`).
  - So a playing session reaches Django at most once a minute per token and edge. A kick bites on the first request after the cached 204 expires (≤ 60 s).

### 5. Edge behaviour (`streaming/nginx`, see `streaming/README.md`)
- **Byte serving:** `sendfile on; tcp_nopush on; aio threads; directio 8m; output_buffers 2 1m`, `max_ranges 1`.
  - Methods are GET, HEAD and OPTIONS; anything else gets 405.
  - The one token location `^/v/(?<token>…)/(?<tail>…)$` runs `js_set` verification; it answers 403 unless the verdict is `ok`, then `auth_request /_auth`.
  - Local mode serves through `alias <root>/<title>/<tail>`, built from the verified token, and no other location maps to storage.
- **Headers** (set by an njs header filter from the final status, so a 416 or an error page never inherits `immutable`):
  - `Cache-Control: public, max-age=31536000, immutable` on segments, init sections and MP4 (200/206/304);
  - `max-age=60` on playlists;
  - `no-store` on everything else;
  - `Accept-Ranges: bytes` on 200 and on single-range 206;
  - `nosniff`, `Referrer-Policy: no-referrer`, a `default-src 'none'` CSP, and `X-Request-ID`.
- **CORS:** origins in `EDGE_CORS_ORIGINS` are echoed (with `Vary: Origin`). Preflights get `GET, HEAD, OPTIONS`, `Range` and max-age 600, before any token check. No credentials are involved.
- **S3 origin mode** (`EDGE_MODE=s3`): `slice 4m`, `proxy_cache_key $edge_object$slice_range` (object path, never the token), `proxy_cache_valid 200 206 30d`, cache lock, stale on origin errors.
  - Only `Host` and `Range` reach the origin. Of the origin's headers, only length, range, ETag and Last-Modified pass; the type comes from the extension.
  - Origin errors are intercepted (bodies name the bucket and the key): 404/403/400/410 → 404, 416 → 416, 5xx → 503.
  - The origin authorises edges by network (a private endpoint or bucket policy); the edge does not sign origin requests.
  - Rendition objects are immutable: a re-encode publishes under a new rendition name or asset key (or the slice cache is purged).
- **Logs:** one JSON line per request with these fields:
  - `time`, `edge`, `request_id`, `remote_addr`, `method`, `path`, `status`, `bytes_sent`, `request_time`, `range`;
  - `session` (16-hex prefix), `kid`, `title`, `rendition`, `verdict`, `reason`;
  - `auth_status`, `auth_cache`, `upstream_cache_status`, `user_agent`.

  The token is replaced by its session prefix in `path`. Xtream-shaped paths are masked and unknown paths are logged as `/***`. Query strings, Referer and raw X-Forwarded-For are never logged. Request-scoped error lines are off by default (`EDGE_REQUEST_ERROR_LOG_LEVEL=emerg`), because nginx ends each one with the raw request line. The njs code never logs.
- **Deployment-specific values** come from `EDGE_*` variables. `40-edge-config.sh` validates them and renders `templates/` with `envsubst`, and the keys file is the only secret.

### 6. Verification
`bash streaming/tests/run.sh` runs these steps:
1. the vectors through the Python reference and through njs, plus 600 fuzz cases compared with the reference;
2. a bad key file refused at start;
3. both edge modes against a stub of stream-auth and of an S3 origin, run with a read-only root file system and dropped capabilities;
4. fail-closed behaviour;
5. the captured logs.

## Alternatives considered
- **Base64url of the whole dotted string** (one literal reading of SPEC): a third longer, a decode step, and nothing hidden. Rejected.
- **JWT/JWS HS256:** a JSON header and claims to parse in njs, `alg` handling to get right, and longer URLs for the same claims. Rejected.
- **Nginx `secure_link`:** MD5, no key ids or rotation, and no IPv6 /64 binding. Rejected.
- **Signing a separately built payload** (`session|title|…`): two encodings to keep in step, and the kid is unsigned. Rejected.
- **`/v/{token}/{xc_id}.mp4` with the file chosen by rendition:** hides the stored name behind a rewrite for no client benefit (players have already chosen their demuxer from the Xtream URL). Rejected.
- **stream-auth on every request, or caching 403s, or serving stale on failure:** per-segment load on Django; slow un-kicks; and media served while concurrency checks are down. Rejected.
- **Fail open when stream-auth is down:** keeps playback alive through a Django outage, but lifts kicks and concurrency limits exactly then. Rejected; revisit if outages prove frequent.
- **SigV4 to a private S3 endpoint from njs:** more code on the hot path. Network-restricted origins suffice for SeaweedFS/MinIO and R2/B2 private endpoints. Deferred.

## Consequences
- **Backend M7** mints tokens exactly as above and passes `streaming/tests/vectors.json` with both its signer and its stream-auth verifier. That means:
  - 32-hex session ids;
  - the `/v/<token>/compat.mp4` redirect;
  - the `<title>/<rendition>.<ext>` and `<title>/<rendition>/…` storage layout;
  - relative HLS URIs;
  - 204/403 + `X-Reason` from stream-auth;
  - one key file shared with the edges;
  - edges-first rotation.
- **Compose and production (M7/M15):**
  - an `nginx-stream` service with the mounts and variables in `streaming/README.md`, on the network where `web` answers;
  - Traefik routes `media.<domain>` to port 8080, with `EDGE_REAL_IP_FROM` set to Traefik's network;
  - standalone edges need their own TLS or a CDN in front.
- **Kick latency:** a kick takes effect within the auth cache TTL (≤ 60 s), on the next request.
- **Long progressive responses:** a player that streams a whole film in one long response makes no new requests, so stream-auth sees no heartbeat until it seeks or reconnects. M7's sweeper must not end such sessions on heartbeat age alone; for example, it can count bytes from the access log (M14) or use a longer window for `compat` sessions.
- **IP binding:** with binding on, a client that reaches `tv.` over IPv6 and `media.` over IPv4 (or the reverse) is refused. Enable it only where both hosts resolve and proxy the same way.
- **Revisit:**
  - live playlists (M12) need their own location: not immutable, and not in the 30-day slice cache;
  - the njs and nginx versions are pinned by the image tag (nginx 1.30.5, njs 1.0.1; ADR-0001).
