# Load tests: IPTV apps at 100, 500, 1,000 and 5,000 viewers

- **Measured:** 2026-10-14, on the development stack, local network only (SPEC §15: never a CDN or public egress).
- **Tiers that ran to the end:** 100, 500 and 1,000 virtual users, twice: before and after the fix below.
- **5,000:** attempted twice. Before the fix it ran but collapsed (22% errors). After the fix, the memory guard stopped it 74 s in, at about 3,400 users, because this shared machine was out of memory. **The 5,000 figures below are therefore extrapolated**, not measured.
- **Tool:** `make loadtest tiers=100,500,1000,5000`:
  - `loadtests/run_load.py` drives Locust 2.46.6 (MIT; its own pinned image, host network, 127.0.0.1 only) with `loadtests/locust/locustfile.py`;
  - throwaway customers come from `manage.py loadtest_data` (development only, deleted afterwards);
  - results land in `loadtests/results/<UTC time>/` (git-ignored).

## Setup
- **Machine:** Intel Core i5-13450HX (10 cores, 16 threads), 15.3 GB of memory, one NVMe disk.
- **Shared with:** the rest of the dev stack (monitoring overlay, a dev edge, SeaweedFS, the read replica), another project's three Odoo containers (about 245 MB) and a desktop.
  - The memory guard (`--min-free-mb 1500`) reads the host's MemAvailable, so everything on the machine counts.
  - During the first run's 100 and 500 tiers, another builder's backend test suite was using 1–2 cores.
- **Web as in production:** for the run, `loadtests/compose.loadtest.yml` sets it up and the driver puts the dev web back afterwards:
  - 2 replicas × 3 gunicorn workers (Uvicorn), production settings (DEBUG off);
  - Traefik's per-address limits raised, because every virtual user comes from one address. The Django limits on this path only count failed sign-ins, so they never applied.
- **The asset:** the edge serves the synthetic sample films (`make sample-media`). Each request reads 32 KB, so these figures measure request handling, not bandwidth. Egress capacity was not measured: loopback has no uplink.

## What a virtual user does
- **Viewer** (90% of users; one customer each, one stream allowed):
  1. signs in with `player_api.php`: a first sign-in verifies the Argon2id hash, later ones hit the 5-minute auth cache;
  2. reads the VOD categories and streams (the per-plan Xtream catalog cache);
  3. starts a film: the 302 from `/movie/…` to the signed edge URL;
  4. then every 4–6 s reads 32 KB of it from the edge, like a player. The edge verifies the token and asks stream-auth at most once a minute: the heartbeat.

  One task in 21 browses the streams again.
- **Contender** (10% of users; four devices per customer, two streams allowed, reject policy):
  - the four devices race for the two slots;
  - a device that gets one holds it for 20–60 s, goes quiet until the 90 s slot window lapses, then tries again;
  - the expected 403 `CONCURRENCY_LIMIT` counts as a success.
- **The slot check:** every 10 s the driver reads every contention customer's active streams from Redis. A customer over two would be an over-allocation.
- **Ramp:** 10 new users/s at 100 users, 50/s above. Each tier runs its ramp, then 120 s at full load, with 100 s between tiers (slots lapse). Sign-ins and first plays all happen during the ramp: **the login storm**.

## Results after the fix (connection pool)
p95 in milliseconds over the whole tier (the ramp's login storm included); failures over requests.

| Users | Sign in | VOD categories | VOD streams | Play start (302) | Contention play | Edge read (heartbeat) | Failures | Requests/s |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 100 | 100 | 22 | 33 | 54 | 50 | 10 | 0 / 2,984 | 23 |
| 500 | 400 | 150 | 130 | 230 | 140 | 13 | 0 / 14,901 | 114 |
| 1,000 | 400 | 160 | 140 | 230 | 140 | 13 | 0 / 30,796 | 218 |
| 5,000 (stopped at ~3,400 users, 74 s) | 3,600 | 3,900 | 4,100 | 4,100 | 3,400 | 120 | 2,153 / 37,302 | 525 |

**Steady state** (after the ramp; the median and the worst of the 10-second windows' p95):

| Users | VOD streams (cached catalog) | Contention play | Edge read (heartbeat) |
|---:|---:|---:|---:|
| 100 | 31 (worst 39) | 44 (62) | 4 (17) |
| 500 | 35 (130) | 44 (160) | 4 (29) |
| 1,000 | 52 (140) | 51 (190) | 3 (16) |

**Slots: no over-allocation in any tier, either run.**
- Every sample showed at most 2 streams for every contention customer.
- At 1,000 users, 50 streams were held by 25 customers, the full two each. At the stopped 5,000 tier, 250 by 125.
- At 1,000 users, 900 viewers held their one stream each.

**Where the time went at 1,000 users** (`docker stats`; 100% = one core):
- **Steady state:** web 50–110% (two replicas together), Traefik 20–30%, the edge 12–21%, PostgreSQL under 15%, Redis under 10%.
- **The login storm (50 first sign-ins a second):** web reached about 700%, because Argon2id is expensive by design.

## Against the targets (SPEC §15)
| Target | Result |
|---|---|
| Cached Xtream catalog p95 < 150 ms | **Met in steady state:** 31–52 ms median, worst window 140 ms at 1,000 users. During the login storm it reaches 150–160 ms. |
| Sign-in and play start p95 < 300 ms | **Play start met:** 230 ms at 1,000 users, including the storm. **Sign-in met at 100 users** (100 ms). At 500 and 1,000 it is **400 ms** while 50 users a second sign in for the first time: each first sign-in verifies Argon2id, and a cached sign-in costs what the catalog does. |
| Zero slot over-allocation under contention | **Met**, at every tier, in both runs. |
| Edge bandwidth headroom ≥ 30% at expected peak | **Not measured here:** loopback has no uplink. Each edge reports its egress against its capacity on the Edges page, the `EdgeOverCapacity` alert fires at 80%, and selection stops at 90% (ADR-0022). |

## What the first run found: connection exhaustion (fixed)
The first run had no pool (`CONN_MAX_AGE` 0, as before M15):
- **At 1,000 users:** 45 requests failed with 500 in a 5-second burst during the ramp. PostgreSQL said *"remaining connection slots are reserved for roles with the SUPERUSER attribute"*. Its CPU reached 440%, opening a connection per request.
- **At 5,000:** 17,298 of 78,425 requests failed (22%): connection exhaustion, then 502 and 503 as the workers fell behind.
- **The cause:** under ASGI, Django runs each concurrent request in its own thread with its own connection, so a burst has no upper bound.

**The fix:** a psycopg connection pool per web process (`POSTGRES_POOL_MAX_SIZE`, 8 in production: at most 2 × 3 × 8 = 48 of PostgreSQL's 100 connections).
- Requests beyond it wait up to 10 s for a connection instead of failing. Celery and the tests keep plain connections.
- It adds `psycopg-pool` 3.3.3 (LGPL-3.0, used unmodified like psycopg itself).

| p95 (ms) at 1,000 users | No pool | Pool |
|---|---:|---:|
| Sign in | 960 | 400 |
| VOD categories | 540 | 160 |
| VOD streams | 570 | 140 |
| Play start | 840 | 230 |
| Failures | 45 | 0 |

## 5,000 viewers: extrapolated, not measured
The stopped run and the 1,000-user steady state give the shape:
- **Steady state scales with viewers.** 5× the 1,000-user figures would be:
  - about 1,100 edge reads/s and 80 stream-auth calls/s (one per stream a minute);
  - about 80 catalog requests/s;
  - web at about 2.5–5.5 cores, Traefik about 1.5, the edge about 1, PostgreSQL under 1.

  Six gunicorn workers can use about six cores, so 5,000 steady viewers fit the production layout, with little room to spare.
- **The login storm is what breaks first.** At 50 first sign-ins a second, web used about 7 cores at 1,000 users. At about 3,400 users the ramp saturated all six workers: p95 4 s, with 502 and 503 answers, and the edge's stream-auth calls queued behind sign-ins. 5,000 users who all sign in for the first time within two minutes need more than one host's six workers.
- **What to do for 5,000+:**
  - raise `WEB_CONCURRENCY` (or `WEB_REPLICAS`) on a bigger host;
  - add a second app host;
  - spread a mass reconnect (apps retry with back-off);
  - and, if real traffic shows storms, consider a cheaper Argon2id cost for TV-app credentials (an owner decision: it weakens offline guessing).

  Measure 5,000 from a separate load-generator machine on the same network: here, Locust and the stack shared 15 GB.

## Reproduce
```bash
make loadtest                                   # 100, 500 and 1,000 (about 15 minutes)
make loadtest tiers=5000 args="--min-free-mb 2000"
```
- **The driver refuses** while an E2E run, `make ci` or ZAP is going, or with too little free memory.
- **Results:** Locust's CSVs, `summary.json` (with the slot samples, the memory guard and `docker stats` every 15 s) and `summary.md`.
- **Cleanup, always, even after a failure:** it deletes the customers and puts web back.
