# IPTVnator end-to-end job (SPEC §7.5, §15)

SPEC §7.5 asks for a Playwright job that runs IPTVnator against the dev stack. The job logs in, lists categories, opens a movie and a series, and starts playback. This page is the plan for that job. Every command in it was run on 2026-10-03, and the results are at the end.

## Facts (checked 2026-10-03)

| | |
|---|---|
| Repository | <https://github.com/4gray/iptvnator> (default branch `master`, active: last push 2026-10-03) |
| Licence | MIT (`LICENSE.md`, "Copyright (c) 2019-2026 4gray"). `TRADEMARK.md` reserves the name and logo: we may say the platform "works with IPTVnator", but never use its logo or suggest an endorsement. |
| Latest release | **v0.24.0** (2026-09-24). Desktop (Electron) builds for Linux, macOS and Windows, plus a browser PWA. |
| PWA runtime | Docker Hub `4gray/iptvnator`. The image holds the Angular PWA (nginx) and the `web-backend` Express proxy under `/api`, which makes the provider calls a browser could not make across origins. |
| Pinned image | `4gray/iptvnator:0.24.0` (the same image as `v0.24.0` and `stable`), index digest `sha256:c5c33df50735741ba169cae83bf04e2fe356d33cb2ed7612295add2ba0038674` (amd64 `sha256:9cdbd51a2500dca0b12e453309dd3b687d8ef301f96fef1df2128255538e1d77`, arm64 `sha256:98401adab33582a1be08c9f82024c2c0a69c3f0f1bf3c2a819409239d571a694`). `latest` follows `master` (0.25.0-pwa on 2026-10-03), so never use it. |
| Headless Playwright | Yes. IPTVnator's own `apps/web-e2e` suite drives this PWA with Playwright, including Xtream specs (`xtream.e2e.ts`, `xtream-series-playback.e2e.ts`) against its mock server. The selectors in `iptvnator_e2e.py` are the ones that suite uses at v0.24.0. |
| Playwright | 1.63.0 (npm and PyPI, Apache-2.0), driven from Python with `uvx --with playwright==1.63.0`. Nothing is added to the project's dependencies. |
| Codecs | Playwright's bundled Chromium lacks H.264 and AAC (Playwright docs, "Browsers"; IPTVnator's suite plays a VP8 clip for that reason). Our `compat_mp4` is H.264/AAC, so the job uses the installed **Google Chrome** (`channel="chrome"`; verified with Chrome 154.0.8037.97, headless). |
| PWA limits | No EPG or XMLTV in the PWA (`docker/README.md`). The job therefore covers VOD and series. Live TV and the guide (M12) are covered by `validate.py --live` and the client matrix ([clients.md](clients.md)). |

How IPTVnator 0.24.0 uses our API, as observed against the fixture server:

- **Calls.** `get_account_info` (never a bare login), the three `get_*_categories` actions, full `get_vod_streams`, `get_series` and `get_live_streams` lists (no `category_id` filter), `get_vod_info` and `get_series_info`. It never fetches `get.php` or `xmltv.php`.
- **Play URLs.** It builds them from the **server URL typed in**, not from `server_info`: `{server}/movie/{user}/{pass}/{stream_id}.{container_extension}` and `{server}/series/{user}/{pass}/{episode id}.{container_extension}`, with the credentials percent-encoded.
- **Live.** It prefers `.m3u8` when `allowed_output_formats` contains it.
- **Catch-up.** It uses `/timeshift/{u}/{p}/{minutes}/{start}/{id}.ts`, with `start` formatted in `server_info.timezone`, which for us is UTC.

## Topology

The server URL typed into IPTVnator has to work in two places:

- the container's proxy calls `player_api.php` with it;
- the browser plays `/movie/` and `/series/` URLs from it.

`tv.localhost` gives both places one name:

```text
 host                                                     IPTVnator container (127.0.0.1:4333 only)
 ┌───────────────────────────────┐   http://127.0.0.1:4333   ┌───────────────────────────────────┐
 │ Playwright + Google Chrome    │ ────────────────────────▶ │ nginx: PWA   /api: Express proxy  │
 │ (iptvnator_e2e.py)            │                            └──────────────────┬────────────────┘
 └──────────────┬────────────────┘                                               │ player_api.php
                │ /movie/... and /series/... (302 to the edge)                   │ tv.localhost → host-gateway
                ▼                                                                ▼
         tv.localhost:HTTP_PORT ─────────────────────▶ Traefik (published on all interfaces) ─▶ web
```

- **On the host,** Chrome resolves `*.localhost` to loopback by itself. `iptvnator_e2e.py` and `validate.py` do the same.
- **In the container,** `--add-host tv.localhost:host-gateway` sends the same name to the host. Traefik publishes `${HTTP_PORT}:80` on all interfaces (`docker/compose.yml`), so the port is reachable there.
- **`IPTVNATOR_PROXY_ALLOW_PRIVATE_NETWORKS=1`** is required, because the proxy refuses loopback and private targets by default. It is safe here because the container is published on 127.0.0.1 only. Never run it this way on a public interface.
- **`CLIENT_URL`** must equal the origin Playwright opens (CORS on `/api`).
- **The edge.** The browser follows each play URL's 302 to the edge, so the edge host must resolve on the host too. A `*.localhost` edge name does.
- **Service worker.** The journey blocks it so that every request reaches Playwright, and runs in a fresh browser context.

## The job (M9, against the seeded stack)

The seeded contract account needs at least one movie and one series with transcoded media, and `max_connections` of **2 or more**. The journey plays a movie and then an episode, and a slot is released only when its session expires (SPEC §7.4).

```bash
docker run --rm -d --name iptvnator-e2e \
  -p 127.0.0.1:4333:80 \
  --add-host tv.localhost:host-gateway \
  -e CLIENT_URL=http://127.0.0.1:4333 \
  -e IPTVNATOR_PROXY_ALLOW_PRIVATE_NETWORKS=1 \
  4gray/iptvnator:0.24.0@sha256:c5c33df50735741ba169cae83bf04e2fe356d33cb2ed7612295add2ba0038674
until curl -fsS http://127.0.0.1:4333/api/health; do sleep 1; done   # {"status":"ok","service":"iptvnator-web-backend"}

XC_USER=... XC_PASS=... uvx --python 3.13 --with playwright==1.63.0 python compat/iptvnator_e2e.py \
  --app http://127.0.0.1:4333 --server http://tv.localhost:8080 --artifacts dist/iptvnator-e2e

docker stop iptvnator-e2e
```

- **The proposed `make e2e-iptvnator` target** wraps these three steps and always stops the container ([README](README.md#wire-it-into-make-ci-m9)).
- **Exit status:** 0 when every step passed, 1 when a step failed, 2 when the journey could not run: no credentials, authentication failed, IPTVnator unreachable, or no browser.
- **Google Chrome** must be installed on the machine that runs the journey. On a fresh machine run `uvx --with playwright==1.63.0 playwright install --with-deps chrome`, which uses apt and sudo.
- **Artifacts.** `--artifacts` writes a screenshot of each failed step and a Playwright `trace.zip` (open it with `uvx --with playwright==1.63.0 playwright show-trace`). The trace contains the test account's credentials (the form input and the request URLs), so keep it out of git (`dist/` is ignored) and never attach it to a public issue.

The journey has seven steps. Each prints PASS or FAIL with its evidence:

1. **Log in:** "Add playlist", then "Xtream credentials" with name, server URL, username and password, then "Add". The app must open the account on its movies (`/xtreams/<id>/vod`).
2. **List movie categories:** every category `get_vod_categories` returns is shown.
3. **Open the movie:** the first movie `get_vod_streams` lists (or `--movie`) opens its details page.
4. **Play the movie:** "Play". The app must request `/movie/…` on the Xtream host and get a **302**. The `<video>` element must then decode a frame (`videoWidth` > 0) and reach at least 1.0 s with no `MediaError`.
5. **List series categories:** as in step 2, on `/series`.
6. **Open the series:** the first series (or `--series`); the episode list appears.
7. **Play the first episode:** as in step 4, for `/series/…`.

## Rehearsal without the stack

Until M9 exists, and whenever the IPTVnator pin changes, rehearse against the golden fixtures. Two fixture-server instances stand in for Traefik: one on loopback for the browser, and one on the docker0 bridge address for the container. Both present `http://tv.localhost:18431`.

```bash
ffmpeg -f lavfi -i testsrc2=size=426x240:rate=25 -f lavfi -i sine=frequency=440:sample_rate=48000 -t 10 \
  -c:v libx264 -profile:v main -pix_fmt yuv420p -c:a aac -b:a 96k -ac 2 -movflags +faststart /tmp/sample.mp4
python3 compat/fixture_server.py --host 127.0.0.1  --port 18431 --public-url http://tv.localhost:18431 --media /tmp/sample.mp4 &
python3 compat/fixture_server.py --host 172.17.0.1 --port 18431 --public-url http://tv.localhost:18431 --media /tmp/sample.mp4 &
docker run --rm -d --name iptvnator-rehearsal -p 127.0.0.1:18433:80 --add-host tv.localhost:host-gateway \
  -e CLIENT_URL=http://127.0.0.1:18433 -e IPTVNATOR_PROXY_ALLOW_PRIVATE_NETWORKS=1 \
  4gray/iptvnator:0.24.0@sha256:c5c33df50735741ba169cae83bf04e2fe356d33cb2ed7612295add2ba0038674
XC_USER=mah-7k3p9q XC_PASS=example-password uvx --with playwright==1.63.0 python compat/iptvnator_e2e.py \
  --app http://127.0.0.1:18433 --server http://tv.localhost:18431
docker stop iptvnator-rehearsal; kill %1 %2
```

`172.17.0.1` is the default docker0 address (`ip -4 addr show docker0`). Binding there keeps the fixture server off the LAN.

## Verification log (2026-10-03)

Host: Linux x86_64 with Docker 29.8.2, Google Chrome 154.0.8037.97 and Playwright 1.63.0 (Python, through `uvx`).

| Run | Result |
|---|---|
| Container DNS | `getent hosts tv.localhost` in the container gave `172.17.0.1` (host-gateway). `wget` from the container to `http://tv.localhost:18431/player_api.php` returned the login payload. |
| `validate.py --live http://tv.localhost:18431 --play` | 202 passed, 0 failed (161 fixture checks and 41 live checks, including `get.php` with `ts`, `m3u8` and `mp4`, `xmltv.php`, both play 302s and the identical auth failures). |
| `validate.py --live` against deliberately broken fixtures | Exit 1: `$[1].stream_id: found string "1002", expected integer`, `$[0].rating_5based: found string "4.1", expected number`, `$.episodes: found array of 2 items, expected object`. |
| Journey (twice) | 7 passed, 0 failed. Log in; 3 movie categories; The Matrix details; movie `/movie/` 302 then 426x240 at 1.1 to 1.3 s; 2 series categories; Breaking Bad (season 0 first, 1 episode); episode `/series/` 302 then 426x240 at 1.2 s. |
| Journey with no media (play URLs answer 404) | Exit 1: "play the movie" and "play the first episode" each failed with `MediaError 4 (source not supported)`, with a screenshot of each step and `trace.zip` written. |
| Credentials in logs | The fixture-server logs held only `username=***` and `password=***`, and the journey output showed `/movie/***/***/`. |

## Keeping it current

- **Bumping IPTVnator.** Change the tag and digest only on purpose:
  1. Read the release notes and the diff of `apps/web-e2e/src/xtream*.ts`, where the selectors come from.
  2. Run the rehearsal.
  3. Run the job against the stack.
- **Coverage limits.** A pass shows that an independent, maintained Xtream client can log in with our credentials, read our categories and lists with our types, open details, and play our 302-to-edge MP4 in a real browser media pipeline. It does not cover:
  - TV-app parsers and remote-control user interfaces ([clients.md](clients.md));
  - `get.php` and `xmltv.php` (`validate.py` covers them);
  - live, guide and catch-up (M12);
  - HLS playback.
