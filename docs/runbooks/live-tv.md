# Runbook: live TV and the programme guide

Live channels and the EPG (M12, [ADR-0017](../adr/0017-live-tv-and-epg.md)). Only channels you own or are licensed to distribute: there is no playlist import, no channel list and no discovery. Every channel names its rights holder, licence reference and licence end date.

## What runs
| Service | Does |
|---|---|
| `live` | The packager. One FFmpeg per watched channel copies the source into HLS on the `live-data` volume. Recording channels also go into the catch-up archive. |
| `live-relay` | Serves what the edge can't serve as a file: the continuous `.ts` stream, the live playlist while a channel starts, and catch-up windows. It has no database access and no secrets. |
| `nginx-stream` (edge) | Checks the token and serves the segments from `live-data`, read-only. |
| `mediamtx` (optional) | An RTMP/SRT/RTSP ingest for your own encoders, cameras or OBS (`docker/compose.mediamtx.yml`). |

`scripts/install.sh --update` creates `live`, `live-relay` and the `live-data` volume with the rest of the stack, and `media-init` sets the volume's ownership. An existing server needs no new secrets or `.env` keys for live TV.

## Settings (admin → Settings → Live TV)
All of these apply at runtime, with no restart: the packager reads them again every few seconds.

| Setting | Default | Meaning |
|---|---|---|
| Stop after the last viewer | 150 s | An on-demand channel stops this long after its last session |
| Channels running at once | 20 | All running channels, recording and always-on ones first |
| Re-encoded channels at once | 1 | Real-time H.264 is CPU-heavy; copy is the rule |
| Longest catch-up | 7 days | The most a channel's catch-up days may be |
| Longest catch-up request | 480 min | The longest timeshift window an app may ask for |
| Guide history / Guide ahead | 7 / 14 days | Programmes kept before and after now |
| Catch-up archive budget | 20 GB | Total archive size across channels; the oldest hours go first |
| Largest guide file | 200 MB | Bigger XMLTV documents are refused |

Only paths and container wiring stay in the environment: `LIVE_ROOT`, the edge's `EDGE_LIVE_*` and the relay's stream-auth address.

## Add a channel (admin → Live TV → Channels → New channel)
1. **Name** (and an Arabic name), **group** (a live category, which plans' access rules apply to) and a logo.
2. **Source URL:** `http(s)`, `rtmp(s)`, `rtsp(s)` or `srt`. **Test** probes it and shows the codecs, resolution and whether it can be copied as is (H.264, HEVC or MPEG-2 video with AAC, AC-3, E-AC-3, MP2 or MP3 audio). Otherwise choose **Real-time H.264**. The URL is stored encrypted and never shown again: the admin sees the scheme and host only, and replaces it whole.
3. **Default output** for the short URL `/{user}/{pass}/{id}`: MPEG-TS (most IPTV apps) or HLS.
4. **Catch-up days** (0 = none). A recording channel runs all the time and fills the archive.
5. **Always on**, for a channel that should start instantly.
6. **Rights:** the rights holder (required to enable), licence reference and licence end. At the licence end the channel is disabled within 5 minutes and its viewers are stopped.
7. **Guide channel ID:** the `tvg-id` of the channel in a guide source (see EPG below).

Order channels with the up/down buttons. Groups are managed on the **Groups** tab; a group with channels can't be deleted.

### Where sources may point
Sources, guide feeds, logos and integrations are fetched from inside the server, so some addresses are refused (threat model G-11):
- loopback, link-local and cloud metadata addresses;
- the stack's own services (`postgres`, `web`, `redis-state`, …).

LAN addresses (192.168.x, 10.x, 172.16–31.x) are allowed, for encoders and ErsatzTV on your network. A refused source shows **The source address is refused**.

## Your own encoders and cameras: the MediaMTX ingest
1. Turn it on and choose where it listens:
   ```bash
   cd /opt/iptv
   sed -i 's/^LIVE_INGEST=.*/LIVE_INGEST=1/' .env
   # Optional: listen on one address only, such as a VPN or LAN interface.
   # Docker's published ports bypass ufw, so this is the way to keep them private.
   sed -i 's/^LIVE_INGEST_BIND=.*/LIVE_INGEST_BIND=10.8.0.1/' .env
   sudo scripts/install.sh --update
   ```
   `scripts/secrets.sh` (run by the installer) generates `MEDIAMTX_PUBLISH_PASS`, `MEDIAMTX_READ_PASS` and `MEDIAMTX_AUTH_TOKEN` the first time.
2. **Sign-ins live in the admin (ADR-0023).** MediaMTX asks the server about every publish, read and API call (`authMethod: http`, `/internal/mediamtx-auth` on the backend network, with `MEDIAMTX_AUTH_TOKEN` as its own password). Open **Settings → Integrations → Live ingest**:
   - the publish user and password your encoders use, and the read user and password the packager uses;
   - until you save them there, the generated `MEDIAMTX_*` values of `.env` apply (the card says "from the server config"; **Move into settings** copies them in);
   - **Test** reads MediaMTX's API with the read user, which proves the whole loop: MediaMTX up, asking this server, and the read user accepted.

   Changing a password takes effect at an encoder's next connection. After 20 failed sign-ins from one address, counted from its first failure, that address is refused until 15 minutes have passed since that first failure (both numbers in **Settings → Live TV**). Readers must come from the Docker networks; MediaMTX's own `runOnDemand` hooks may publish from its loopback. Failures show in the metric `iptv_live_ingest_auth_total{result="denied"}`.
3. Publish a stream as the `publisher` user. The path name is yours, here `studio`:
   - **OBS:** Settings → Stream → Custom, server `rtmp://tv.example.com:1935/studio?user=publisher&pass=<MEDIAMTX_PUBLISH_PASS>`, with an empty stream key. Use H.264 and AAC with a keyframe every 2 s.
   - **FFmpeg:** `ffmpeg -re -i input.mp4 -c copy -f flv "rtmp://tv.example.com:1935/studio?user=publisher&pass=$PUBLISH_PASS"`
   - **SRT encoder:** `srt://tv.example.com:8890?streamid=publish:studio:publisher:<MEDIAMTX_PUBLISH_PASS>`
   - **An IP camera** that only offers RTSP needs no ingest: add `rtsp://user:pass@camera-ip:554/...` as a channel directly.
4. In the admin: **Live TV → Integrations → Add instance → MediaMTX** with:
   - Address `http://mediamtx:9997`;
   - Stream address `rtsp://mediamtx:8554`;
   - Reader user `reader` and its password (the read password of step 2). An instance's own user may read too, so it can also be a separate user.

   Add the rights holder and licence reference, then **Sync now**. Each published path becomes a **disabled** channel; review it and enable it. Or add the channel by hand with the source `rtsp://reader:<pass>@mediamtx:8554/studio`.

SRT goes through MediaMTX: the packager's FFmpeg build has no SRT input.

## 24/7 channels from your own library: ErsatzTV
ErsatzTV (a separate program you run) builds scheduled channels from media you own.
1. Run ErsatzTV on the LAN or in its own Docker project, pointing at your library.
2. In the admin: **Live TV → Integrations → Add instance → ErsatzTV** with:
   - its address (for example `http://192.168.1.20:8409`);
   - the access token, if you set one in ErsatzTV;
   - the group, rights holder and licence reference.
3. **Sync now** creates one disabled channel per ErsatzTV channel (`/iptv/channel/{n}.ts`) and an EPG source for its guide (`/iptv/xmltv.xml`, ids `C{n}.…`). Enable the channels after review.

A channel that disappears from ErsatzTV is disabled, never deleted.

## The programme guide (admin → EPG)
- **Sources:** an XMLTV URL (plain or gzip) with a refresh schedule in cron syntax (for example `0 */6 * * *`), or an uploaded file. **Refresh** imports now. A URL source is fetched with a conditional GET, so an unchanged guide costs one request.
- **Mapping:** a channel's **Guide channel ID** must equal the XMLTV `<channel id>`. **Unmatched** lists guide channels that no channel uses. **Preview** shows a channel's next programmes.
- **What apps see:** `get_short_epg`, `get_simple_data_table` and `xmltv.php` (gzip). The M3U's `url-tvg` points at `xmltv.php`.

Guide documents with a DOCTYPE or entity declarations are refused. So are documents larger than **Largest guide file**.

## Catch-up and disk sizing
The archive keeps each recording channel's segments for its catch-up days, within **Catch-up archive budget**. Per channel and day:

| Bitrate | Per day | 7 days |
|---|---|---|
| 2 Mb/s (SD) | 22 GB | 151 GB |
| 5 Mb/s (HD) | 54 GB | 378 GB |
| 8 Mb/s (full HD sports) | 86 GB | 605 GB |

GB per day ≈ Mb/s × 10.8. Set the budget below the free space on the volume holding `live-data`. Docker's volume directory is usually on `/`. The admin's channel sheet estimates a channel's need from its probed bitrate. The **Live TV** overview shows the archive's use against the budget.

Apps ask for catch-up as `/timeshift/{user}/{pass}/{minutes}/{YYYY-MM-DD:HH-MM}/{id}.ts`, in UTC. The request is refused unless the channel records, the window is within its catch-up days and the archive covers part of it.

## Verify
```bash
# The packager's heartbeat and channel states:
docker compose --project-directory . -f docker/compose.yml -f docker/compose.prod.yml ps live live-relay
docker compose --project-directory . -f docker/compose.yml -f docker/compose.prod.yml logs --tail 50 live
```
With a test customer's IPTV login in `XC_USER` and `XC_PASS`, the contract suite plays a live channel and a catch-up window through the real edge:
```bash
XC_USER=... XC_PASS=... uvx --with 'jsonschema==4.26.0' python compat/validate.py --live https://tv.example.com --play
```

## Troubleshooting
| Symptom | Look at |
|---|---|
| Channel **Failed: The source stopped / stalled / No picture** | The source itself: test it again from the channel sheet. `logs live` shows FFmpeg's last lines with the URL masked. Restarts back off from 2 s to 60 s. |
| **FFmpeg is missing** | The `live` service runs the media image. Rebuild with `install.sh --update`. |
| **The source address is refused** | The URL points at loopback, a metadata address or one of the stack's services (see "Where sources may point"). |
| An app shows nothing, or gets 403 with `X-Reason` | The play URL's 302 or refusal: `curl -sI "https://tv.example.com/live/$XC_USER/$XC_PASS/<id>.ts"`. `X-Reason` names the cause: the plan doesn't allow live TV, a category rule, the stream limit, an expired subscription. |
| `.ts` plays, HLS doesn't (or the reverse) | Both come from the same packager; HLS needs the first segment (4–8 s after a cold start). The relay waits up to 7 s for it. |
| A web player (IPTVnator) fails with a CORS error | The media edge's `EDGE_CORS_ORIGINS` must list the player's origin. Play redirects on the tv host already allow any origin. |
| Catch-up is refused | The channel has no catch-up days, the window is older than they allow, or the archive started later (it fills from when the channel began recording). |
| The disk fills | Lower **Catch-up archive budget** or a channel's catch-up days. The packager prunes within 10 minutes. |

## Development
`make live-ready` makes the legal synthetic "Test Pattern" channel:
- `scripts/sample_media.sh` writes `media/live/test-pattern.mp4`;
- the dev MediaMTX loops it as `rtsp://mediamtx:8554/test-pattern`;
- `manage.py live_demo` creates the channel (1 day of catch-up), its group and a generated guide, sets a 1 GB archive budget, and waits until 3 minutes are recorded.

Reruns take about a second. `make compat-live` and `make e2e-iptvnator` play it.
