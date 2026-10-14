"""Smart IPTV load test (M15, ADR-0022; docs/benchmarks/load.md): `make loadtest`.

IPTV apps against the local dev stack only, through Traefik on 127.0.0.1. The
customers come from `manage.py loadtest_data`: `ltv00001…` viewers and `ltc0001d1…`
contention devices, all with the password in LOADTEST_PASSWORD. No stats name or failure
message carries a credential or a token.

- **Viewer** (one per customer, one stream each):
  1. signs in (`player_api.php`: the login storm, Argon2id on a first sign-in);
  2. browses the catalog (the cached VOD categories and streams);
  3. starts a film (the 302 to the media edge: play start);
  4. then reads 32 KB of it every 4 to 6 s, as a player does. The edge verifies the token
     and asks stream-auth at most once a minute: the heartbeat.

  Now and then it browses again.
- **Contender** (four devices per customer, two streams allowed, reject policy): the
  devices race for the two slots. A device that gets one holds it for 20 to 60 s, then
  goes quiet until the slot lapses, then tries again. A refusal (403
  CONCURRENCY_LIMIT) is expected. `loadtests/run_load.py` checks the slots meanwhile:
  never more than two.
"""

from __future__ import annotations

import itertools
import os
import random
import time
from typing import Any
from urllib.parse import urlsplit

from locust import FastHttpUser, between, events, task

PASSWORD = os.environ["LOADTEST_PASSWORD"]
VIEWERS = int(os.environ.get("LT_VIEWERS", "0"))
GROUPS = int(os.environ.get("LT_GROUPS", "0"))
DEVICES_PER_GROUP = 4
TV_HOST = os.environ.get("LT_TV_HOST", "tv.localhost")
CHUNK = 32 * 1024
# Each process numbers its users per class; LT_PROCESS_STRIDE spreads processes apart.
_counters: dict[str, itertools.count[int]] = {}
_slot = {"index": 0, "stride": 1}


@events.init.add_listener
def _number_processes(environment: Any, **_: Any) -> None:
    runner = environment.runner
    index = getattr(runner, "worker_index", 0) or 0
    _slot["index"] = max(0, int(index))
    _slot["stride"] = max(1, int(os.environ.get("LT_PROCESS_STRIDE", "1")))


def _next(kind: str, limit: int) -> int:
    """A 1-based number unique across processes for `kind`, wrapping at `limit`."""
    counter = _counters.setdefault(kind, itertools.count())
    n = next(counter) * _slot["stride"] + _slot["index"]
    return n % max(1, limit) + 1


class XtreamClient(FastHttpUser):
    abstract = True
    username = ""
    streams: list[dict[str, Any]]
    media_path = ""
    media_host = ""
    offset = 0

    def api(self, action: str | None, name: str) -> Any:
        query = f"username={self.username}&password={PASSWORD}"
        if action:
            query += f"&action={action}"
        with self.client.get(
            f"/player_api.php?{query}", headers={"Host": TV_HOST}, name=name, catch_response=True
        ) as response:
            if response.status_code != 200:
                response.failure(f"HTTP {response.status_code}")
                return None
            try:
                body = response.json()
            except ValueError:
                response.failure("not JSON")
                return None
            if action is None and str(body.get("user_info", {}).get("auth")) != "1":
                response.failure("auth refused")
                return None
            return body

    def sign_in_and_browse(self) -> None:
        self.api(None, "xtream: sign in")
        self.api("get_vod_categories", "xtream: vod categories")
        streams = self.api("get_vod_streams", "xtream: vod streams")
        self.streams = [s for s in streams or [] if s.get("stream_id")]

    def play(self, name: str, *, refusal_ok: bool = False) -> bool:
        """Ask for a film; True when a media URL came back."""
        if not self.streams:
            return False
        stream = random.choice(self.streams)
        ext = stream.get("container_extension") or "mp4"
        path = f"/movie/{self.username}/{PASSWORD}/{stream['stream_id']}.{ext}"
        with self.client.get(
            path, headers={"Host": TV_HOST}, name=name, allow_redirects=False, catch_response=True
        ) as response:
            if response.status_code == 302:
                target = urlsplit(response.headers.get("Location", ""))
                self.media_host = target.netloc.split(":")[0]
                self.media_path = target.path
                self.offset = 0
                return True
            reason = response.headers.get("X-Reason", "")
            if refusal_ok and response.status_code == 403 and reason == "CONCURRENCY_LIMIT":
                response.success()
                return False
            response.failure(f"HTTP {response.status_code} {reason}".strip())
            return False

    def read_media(self, name: str) -> int:
        """32 KB of the current film from the edge; the HTTP status."""
        if not self.media_path:
            return 0
        end = self.offset + CHUNK - 1
        with self.client.get(
            self.media_path,
            headers={"Host": self.media_host, "Range": f"bytes={self.offset}-{end}"},
            name=name,
            catch_response=True,
        ) as response:
            status = response.status_code
            if status == 206:
                total = response.headers.get("Content-Range", "/0").rsplit("/", 1)[-1]
                size = int(total) if total.isdigit() else 0
                self.offset = end + 1 if size and end + 1 < size else 0
            elif status == 416:
                self.offset = 0
                response.success()
            else:
                response.failure(f"HTTP {status} {response.headers.get('X-Reason', '')}".strip())
            return status


class Viewer(XtreamClient):
    weight = 1
    wait_time = between(4, 6)

    def on_start(self) -> None:
        self.username = f"ltv{_next('viewer', VIEWERS):05d}"
        self.sign_in_and_browse()
        self.play("xtream: play start (302)")

    @task(20)
    def heartbeat(self) -> None:
        status = self.read_media("edge: media range (heartbeat)")
        if status in (401, 403, 404, 0):
            # Kicked, replaced or expired: a player asks again.
            self.play("xtream: play start (302)")

    @task(1)
    def browse(self) -> None:
        self.api("get_vod_streams", "xtream: vod streams")


class Contender(XtreamClient):
    fixed_count = GROUPS * DEVICES_PER_GROUP
    wait_time = between(2, 4)
    holding_until = 0.0
    quiet_until = 0.0

    def on_start(self) -> None:
        n = _next("contender", GROUPS * DEVICES_PER_GROUP) - 1
        self.username = f"ltc{n // DEVICES_PER_GROUP + 1:04d}d{n % DEVICES_PER_GROUP + 1}"
        self.sign_in_and_browse()

    @task
    def contend(self) -> None:
        now = time.monotonic()
        if now < self.quiet_until:
            return
        if self.holding_until:
            if now < self.holding_until:
                self.read_media("edge: media range (contention)")
                return
            # Stop watching: the slot lapses after the slot window (90 s).
            self.holding_until = 0.0
            self.media_path = ""
            self.quiet_until = now + 100
            return
        if self.play("xtream: play start (contention)", refusal_ok=True):
            self.read_media("edge: media range (contention)")
            self.holding_until = now + random.uniform(20, 60)
