# ADR-0017: Live TV and EPG: channels, guide import, packager, relay and catch-up

- **Status:** Accepted
- **Date:** 2026-10-11
- **Milestone:** M12 (SPEC §6, §7.4, §7.5, §12, §16; the token and edge are ADR-0007, Xtream playback ADR-0011)

## Context
M12 adds live channels and an electronic programme guide to the Xtream API, the M3U playlist and the admin. The work is shaped by the following:
- **The Xtream contract.** It covers `get_live_categories`, `get_live_streams`, `get_short_epg` and `get_simple_data_table`, with titles and descriptions in base64. It also covers `xmltv.php`, the live entries in `get.php` with `url-tvg`, and the play URLs `/live/{u}/{p}/{id}.{ts|m3u8}`, the bare `/{u}/{p}/{id}` and `/timeshift/{u}/{p}/{minutes}/{start}/{id}.ts`. IPTV apps expect `.ts` to be one endless MPEG-TS stream, not a playlist.
- **Owned or licensed channels only.** There is no bulk import of third-party M3U playlists, no bundled or suggested channels, and no discovery or scraping. A channel names its `rights_holder`, `license_ref` and `license_expires_at`. Its source is an encoder, a camera, an ErsatzTV schedule or a MediaMTX ingest that the operator runs.
- **Media requests never touch Postgres** (the hard rule). The edge verifies the token, and the cached `auth_request` touches Redis only.
- **Sources are URLs an admin types,** fetched from inside our network (threat model G-11). The concurrency slot was keyed by `(user, device, title)`, so one credential could feed many households watching the same channel (G-13). Both gaps had to close before live TV.
- **Volume.** A guide document can run to hundreds of megabytes, and `epg_programs` is the highest-volume table in §6.

## Decision

### 1. Data model (`apps/live`)
- **`LiveChannel`.** It holds:
  - an integer `xc_id` from the catalog's shared sequence, so stream ids never collide with movies or episodes;
  - the name, a logo and a group;
  - `epg_channel_id`, mapped to a guide channel;
  - `source_encrypted` (Fernet, `apps.accounts.crypto`);
  - `output` (`ts|hls`), the bare URL's default;
  - `transcode` (`copy|h264`), `catchup_days`, `always_on`, `enabled` and `sort`;
  - the rights fields, `origin` (`manual|ersatztv|mediamtx`) with its integration, and the last probe (codec, height).
- **The source URL** is limited to http(s), rtmp(s), rtsp(s) and srt. It is never serialized or logged; the admin sees `describe()` (scheme and host only) and replaces the URL whole.
- **Enabling needs rights.** A database constraint (`live_channel_enabled_needs_rights`) refuses `enabled` without a `rights_holder`. Every 5 minutes the `live-enforce-licences` beat job disables channels past `license_expires_at` and kicks their sessions with the new `KickReason.LICENSE_EXPIRED`.
- **Groups are `Category(kind=live)`, not a new `ChannelGroup` model.** This deviates from §6. Categories already carry the ar/en names, the order, the adult flag, the plan access rules, the Xtream string ids and the cache invalidation. A second model would duplicate all of that. Deleting a live group that still has channels is refused.
- **Guides.** There are three models:
  - `EpgSource`: a URL or an uploaded file, a cron expression, and its last result;
  - `EpgChannel`: the channels a source lists, for mapping and the unmatched view;
  - `EpgProgram`: the start, stop, title, `title_ar`, description and category.
- **`LiveIntegration`** holds an ErsatzTV or MediaMTX base URL (and MediaMTX's stream base), credentials (encrypted and write-only in the API), the rights holder and licence reference given to synced channels, and the group they go into.

### 2. `epg_programs` is partitioned monthly now
- The table is range-partitioned on `start` (`PARTITION BY RANGE`, primary key `(id, start)`, `ON DELETE CASCADE` to the channel in the database), created by raw SQL in the migration under `SeparateDatabaseAndState`. There is no DEFAULT partition, so a stray row fails loudly instead of landing in a catch-all.
- `apps/core/partitions.py` is reusable for the M15 tables: `ensure_monthly`, `drop_monthly_before`, `list_partitions`. It works under an advisory lock and uses `SET CONSTRAINTS ALL IMMEDIATE` before a drop.
- The `live-epg-partitions` beat job (daily, 02:40) creates the months ahead and drops those older than the longer of `live.epg_past_days` and `live.catchup_max_days`.
- **This supersedes ADR-0005's deferral for `epg_programs` only.** The other high-volume tables stay with M15.

### 3. Guide import
- **A streaming expat reader** (`apps/live/xmltv.py`):
  - it refuses DOCTYPE internal subsets and entity declarations;
  - it gunzips by magic number;
  - it stops at `live.epg_max_document_mb`;
  - it keeps only programmes inside `live.epg_past_days` and `live.epg_future_days` for mapped channels.
- **Fetching.** Fetches are conditional GETs (ETag, Last-Modified) through the egress-guarded client (§9). Each source's cron runs from `live-refresh-epg`, which checks every 5 minutes. A channel's programmes are replaced per import inside one transaction.
- **The Xtream guide.** `get_short_epg` and `get_simple_data_table` return stable 52-bit listing ids with base64 titles and descriptions. `xmltv.php` renders the visible channels' guide, gzip-compressed when the client accepts it, and is cached in redis-cache per plan.

### 4. The packager (`manage.py run_live`, the `live` service)
- **Process.** One ffmpeg runs per watched channel: `-c copy`, or a real-time re-encode when the channel asks for it. It writes MPEG-TS HLS (`profiles.yaml` `live:`, 4 s segments, 6 in the window, epoch numbering) into `<root>/<key>/live/`. The channel key is the channel's UUID.
  - `-protocol_whitelist` limits what ffmpeg may open.
  - A failed ffmpeg restarts with backoff (2 s doubling to 60 s), and its stderr is masked with the exact source URL removed.
- **What runs:**
  - recording channels (catch-up) and `always_on` channels, always;
  - other channels while a live session younger than `live.idle_stop_s` exists, or right after `start_playback` publishes `live:wake`.
  - The caps are `live.max_running_channels` in all and `live.realtime_transcode_max` re-encoding.
- **Catch-up.** Each new segment is hard-linked into `archive/YYYYMMDD/HH/<start ms>-<duration ms>.ts`, using the playlist's PROGRAM-DATE-TIME and EXTINF. The archive's first and last instants go to `live:archive:<key>` in redis-state.
  - Retention runs every 10 minutes and removes, in order: hours past the channel's `catchup_days`, archives of channels that stopped recording, then the oldest hours across channels while the total exceeds `live.archive_max_gb`.
- **The packager is control plane.** It reads channels from Postgres when it decides what to run. It also publishes `live:status:<key>` and the heartbeat `hb:live`. Media requests never wait on it.

### 5. The relay (`apps.live.relay`, the `live-relay` service)
- A raw ASGI app under uvicorn on the internal network. It does not import Django, holds no secret and never touches Postgres.
- The edge verifies the token and calls stream-auth first, then proxies the request with `X-Live-Object: <key>/<tail>`. The relay serves:
  - `live.ts`: the channel as one continuous MPEG-TS stream, joined from the segments, with null packets while a cold channel starts;
  - `live/index.m3u8`: the live playlist, waiting briefly for a starting channel;
  - `archive/<start>-<seconds>.ts|m3u8`: a catch-up window as one stream or as a VOD playlist of archived segments, with discontinuities.
- **Keeping streams honest.** Long streams call `/internal/stream-auth` again every 30 s with the original token URI and the bytes sent. A 403 (kicked, expired, over the limit) ends the stream at once; stream-auth outages end it after a 90 s grace.

### 6. The edge
- `live.conf.template` is rendered when `EDGE_LIVE_ROOT` and `EDGE_LIVE_RELAY` are set, and is included ahead of the generic token location in both `edge.conf` and `edge-s3.conf`.
  - **Entry points** (`live.ts`, `live/index.m3u8`, `archive/<start>-<s>.(ts|m3u8)`) go to the relay with buffering, caching, cookies and request headers off.
  - **Live and archive segments** are files aliased from the read-only live volume, with `open_file_cache off`.
- token.js's scope check keeps VOD tokens out of the live locations and live tokens out of the renditions.
- `liveCaching` sets the cache headers:
  - `no-store` on the live playlist;
  - `no-cache` on catch-up playlists;
  - `max-age=120` on live segments;
  - `max-age=86400` on archived segments.

### 7. Playback and the Xtream host
- **Title and rendition kinds.** `TitleKind.LIVE` and `CATCHUP`, and `RenditionKind.LIVE` and `ARCHIVE`, go through the same fail-fast `start_playback` as VOD:
  - the plan's `allow_live` gates both kinds;
  - the quality ceiling uses the probed height (unknown is allowed);
  - live tokens use `playback.token_ttl_live_s`.
- **Timeshift.** It accepts `YYYY-MM-DD:HH-MM` with optional `-SS`, in UTC. It is refused unless:
  - the channel records;
  - the window is 1 to `live.timeshift_max_minutes` long;
  - the window lies within `catchup_days`;
  - the window overlaps the recorded archive.
- **The bare `/{u}/{p}/{id}`** plays the channel's `output`. There is no `timeshift.php` alias.
- **URL names on the tv host:** `xtream-live`, `xtream-live-short` and `xtream-timeshift`. `xmltv.php` is the existing `xtream-guide`.
- **The M3U.** `get.php` lists live channels first (always), with `url-tvg`, `tvg-id`, `tvg-logo` and `group-title`. VOD entries stay behind their setting.
- **CORS.** Play-URL redirects on the tv host carry `Access-Control-Allow-Origin: *` and expose `X-Reason` and `Retry-After`. The credentials are in the URL, and no cookie is involved. Web players such as IPTVnator follow the redirect with `Origin: null`, so the dev edge also allows `*` (`EDGE_CORS_ORIGINS="*"`); production lists its origins.
- **Stopping.** `KickReason.STOPPED` replaces the generic end reason when a customer stops a stream. Progress and recommendations ignore live kinds.

### 8. Session identity includes the client network (G-13)
- `session_key(user, device, title, network)` now hashes the client's /24 (IPv4) or /64 (IPv6), from `tokens.client_net` via `playback.services.client_network`. This applies to every title kind.
- A second household on the same credential and title gets its own session, so it takes its own slot or is refused by the limit. An unknown address hashes as `""`, as before.

### 9. One egress helper (G-11)
- `apps/live/egress.py` decides where an admin-supplied URL may point. It refuses:
  - loopback, link-local (and so the cloud metadata addresses), unspecified, multicast and reserved addresses, after resolution and for every address a name resolves to;
  - the stack's own service names and their addresses.
- Private LAN ranges stay allowed, because operators run encoders, ErsatzTV and MediaMTX there. A configured integration's host is always allowed.
- It is applied to:
  - source tests and probes (`check_url` before ffprobe);
  - guide fetches, logo downloads and integration syncs (`guarded_client`, re-checked on every redirect);
  - packager starts (`guard` before each ffmpeg).
- ffmpeg follows an HLS source's own redirects and segment URLs; there, `-protocol_whitelist` is the limit.

### 10. What is a setting and what is deployment
- **Admin settings.** Everything an operator tunes is a registry key in the `live` group, with ar/en labels, read at runtime:
  - `idle_stop_s` and `max_running_channels`;
  - `realtime_transcode_max`;
  - `catchup_max_days` and `timeshift_max_minutes`;
  - `epg_past_days` and `epg_future_days`;
  - `archive_max_gb` and `epg_max_document_mb`.

  The packager reads them again on every reconcile, so a changed archive budget applies within seconds without a restart.
- **Environment.** Only deployment wiring stays there: `LIVE_ROOT`, the ffmpeg and ffprobe binaries, the edge's `EDGE_LIVE_*`, and the relay's stream-auth address. The relay's timings (`LIVE_RELAY_*_S`) are code defaults with an environment override for tests. The relay is deliberately free of the database and of secrets, so it cannot read the registry.
- **Credentials.** Integration credentials live in the database (encrypted) and are write-only in the API.
- **The optional MediaMTX ingest's** publish and read users are environment variables in `docker/compose.mediamtx.yml`, because MediaMTX is a separate process.

### 11. Integrations and MediaMTX
- **ErsatzTV sync** reads `/api/channels` and maps channel `n` to `/iptv/channel/{n}.ts` (with its `access_token` when set), with the guide from `/iptv/xmltv.xml`. **MediaMTX sync** reads `/v3/paths/list` and maps each path to `{stream base}/{path}`, read with the same user. Every stream URL is built on the configured instance, so a sync can't import a third-party playlist.
- Synced channels are created **disabled**, and an admin reviews and enables them. Channels the instance no longer has are disabled and reported, never deleted.
- **MediaMTX** (MIT, images `bluenviron/mediamtx:1.21.1` and, in dev for its test loop, `1.21.1-ffmpeg`, both pinned by digest) runs as a separate process and is never imported:
  - in dev, as the source of the legal synthetic "Test Pattern" channel (`make live-ready`, recorded with a 1 GB budget);
  - in production, optionally, as an RTMP, SRT and RTSP ingest (`docker/compose.mediamtx.yml`) for OBS, ffmpeg or a camera.

  SRT reaches the packager through MediaMTX, because our ffmpeg build has no libsrt.

### 12. Admin pages
- **Live TV** (`/live`) has:
  - overview tiles;
  - channels with search, filters, bulk enable and disable, up/down ordering, logo upload, a source test with probe results, programmes and viewers;
  - groups;
  - integrations with sync.
- **EPG** (`/epg`) has sources (URL or upload, cron, refresh), the unmatched guide channels and a programme preview.
- Both pages work in ar and en, in light and dark, are axe-checked, and are in the E2E page list and journeys.

## Alternatives considered
- **A `ChannelGroup` model** as §6 lists. Rejected: it would duplicate categories' i18n, access rules, ordering, adult flag and caches (§1).
- **MediaMTX as the core packager.** Rejected as the core: its HLS muxer is per path and on-demand, but our tokens, archive layout, catch-up windows and stream-auth re-checks would all have to be bolted onto an external process. It stays the optional ingest and the dev source.
- **Proxying the source per viewer.** Rejected: each viewer would open its own upstream connection, the source URL would be exposed to the edge, and there would be no catch-up.
- **Streaming `.ts` from Django.** Rejected: it ties a gunicorn worker to each viewer for hours and puts Postgres-backed code on the media path.
- **A DEFAULT partition.** Rejected: it hides missing partitions and must be emptied before a new range can attach.
- **A per-client nonce for G-13.** Rejected for now: it needs client cooperation that IPTV apps don't give. The network prefix works with every client, and anti-sharing scoring (G-18) can build on it.
- **Refusing RFC 1918 destinations.** Rejected: it would break the common case of an encoder or ErsatzTV on the LAN. Loopback, metadata and our own services are what matter.

## Consequences
- Live TV runs without new paid services or GPL code in the process. FFmpeg and MediaMTX run as separate processes.
- Two new always-on services (`live`, `live-relay`) and a `live-data` volume. `scripts/install.sh --update` creates them with the stack, and `media-init` prepares the volume's ownership. Disk use is bounded by `live.archive_max_gb`.
- The edge serves live segments from files. The relay holds one connection per `.ts` viewer, cheaply (asyncio, reading local files).
- A viewer moving between networks (Wi-Fi to mobile) starts a new session. Its old one is replaced on the same device, so it doesn't use a second slot for long.
- Revisit:
  - GPU real-time transcoding for live (the CPU path is capped at `realtime_transcode_max`);
  - live TV in the customer portal;
  - MediaMTX `authMethod: http` against Django, so ingest credentials can also move into the admin;
  - DVR-style recording rules beyond catch-up.
