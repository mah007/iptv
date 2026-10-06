"""The live relay: what the media edge cannot serve from a file (ADR-0017).

A small ASGI app (`uvicorn apps.live.relay:app`, the `live-relay` service) behind
the media edge, on the internal network only. It imports no Django, holds no
secrets and never touches Postgres: it reads the live volume and asks the existing
`/internal/stream-auth` (Redis only) whether a stream may continue.

The edge has already verified the token (njs) and asked stream-auth before it
proxies a request here, and it passes the verified object as `X-Live-Object:
<channel key>/<tail>`. The relay serves:

- `live.ts`: the channel as one continuous MPEG-TS stream, joined from the HLS
  segments ffmpeg writes (IPTV apps that ask for `.ts` expect exactly that);
- `live/index.m3u8`: the live playlist, after waiting (briefly) for a channel the
  packager is starting;
- `archive/<start>-<seconds>.ts` and `.m3u8`: a catch-up window, as one stream or
  as a playlist of the archived segments (which the edge then serves as files).

Long streams keep their session alive and stop when it ends: every
`LIVE_RELAY_AUTH_INTERVAL_S` (30 s) the relay asks stream-auth with the original
token URI and the bytes sent since; a 403 (kicked, limit, expired) ends the stream
at once, and stream-auth failures end it after `LIVE_RELAY_AUTH_GRACE_S`. While a
cold channel starts, `live.ts` sends MPEG-TS null packets so players do not time out.

The relay never logs a token or a URL: only the channel key's prefix.
"""

import asyncio
import contextlib
import logging
import os
import time
from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from apps.live import hls, layout

logger = logging.getLogger("apps.live.relay")

type Scope = MutableMapping[str, Any]
type Message = MutableMapping[str, Any]
type Receive = Callable[[], Awaitable[Message]]
type Send = Callable[[Message], Awaitable[None]]

TS_TYPE = b"video/mp2t"
PLAYLIST_TYPE = b"application/vnd.apple.mpegurl"
CHUNK = 256 * 1024
NULL_PACKET = b"\x47\x1f\xff\x10" + b"\xff" * 184
POLL_S = 0.5


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


@dataclass(frozen=True, slots=True)
class RelayConfig:
    root: Path
    auth_url: str
    auth_host: str
    edge_id: str = "relay"
    auth_interval_s: float = 30.0
    auth_grace_s: float = 90.0
    #: How long `live.ts` waits (sending null packets) for a starting channel.
    start_timeout_s: float = 15.0
    #: How long the live playlist waits: under ExoPlayer's 8 s read timeout.
    playlist_timeout_s: float = 7.0
    #: A stream ends when no new segment arrives for this long.
    stall_s: float = 30.0

    @classmethod
    def from_env(cls) -> "RelayConfig":
        return cls(
            root=Path(os.environ.get("LIVE_ROOT", "/live")),
            auth_url=os.environ.get("LIVE_RELAY_AUTH_URL", "http://web:8000/internal/stream-auth"),
            auth_host=os.environ.get("LIVE_RELAY_AUTH_HOST", "web"),
            edge_id=os.environ.get("LIVE_RELAY_EDGE_ID", "relay"),
            auth_interval_s=_env_float("LIVE_RELAY_AUTH_INTERVAL_S", 30.0),
            auth_grace_s=_env_float("LIVE_RELAY_AUTH_GRACE_S", 90.0),
            start_timeout_s=_env_float("LIVE_RELAY_START_TIMEOUT_S", 15.0),
            playlist_timeout_s=_env_float("LIVE_RELAY_PLAYLIST_TIMEOUT_S", 7.0),
            stall_s=_env_float("LIVE_RELAY_STALL_S", 30.0),
        )


class StreamEnded(Exception):
    """The session ended (kicked, expired, ...) or the client went away."""


class Session:
    """The client's session as stream-auth sees it, checked every interval."""

    def __init__(
        self, config: RelayConfig, client: httpx.AsyncClient, headers: dict[str, str]
    ) -> None:
        self.config = config
        self.client = client
        self.headers = headers
        self.unreported = 0
        self.last_ok = time.monotonic()
        self.next_check = time.monotonic() + config.auth_interval_s

    def sent(self, count: int) -> None:
        self.unreported += count

    async def check(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now < self.next_check:
            return
        self.next_check = now + self.config.auth_interval_s
        headers = {**self.headers, "Host": self.config.auth_host, "X-Bytes": str(self.unreported)}
        try:
            response = await self.client.get(self.config.auth_url, headers=headers)
        except httpx.HTTPError:
            status = 0
        else:
            status = response.status_code
        if status == 204:
            self.unreported = 0
            self.last_ok = now
            return
        if status == 403:
            raise StreamEnded("denied")
        if now - self.last_ok > self.config.auth_grace_s:
            raise StreamEnded("auth_unavailable")


@dataclass(frozen=True, slots=True)
class Target:
    key: str
    tail: str


def parse_target(value: str) -> Target | None:
    key, slash, tail = value.partition("/")
    if not slash or not layout.CHANNEL_KEY.fullmatch(key):
        return None
    if tail in (layout.LIVE_TS_TAIL, layout.LIVE_PLAYLIST_TAIL) or layout.WINDOW_TAIL.fullmatch(
        tail
    ):
        return Target(key, tail)
    return None


def _headers(scope: Scope) -> dict[str, str]:
    return {
        name.decode("latin-1").lower(): value.decode("latin-1")
        for name, value in scope.get("headers", [])
    }


async def _start(send: Send, status: int, content_type: bytes, **extra: str) -> None:
    headers = [(b"content-type", content_type), (b"cache-control", b"no-store")]
    headers += [(name.encode(), value.encode()) for name, value in extra.items()]
    await send({"type": "http.response.start", "status": status, "headers": headers})


async def _empty(send: Send, status: int, **extra: str) -> None:
    await _start(send, status, b"text/plain", **extra)
    await send({"type": "http.response.body", "body": b""})


def _read(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


class Relay:
    def __init__(self, config: RelayConfig | None = None) -> None:
        self.config = config or RelayConfig.from_env()
        self._client: httpx.AsyncClient | None = None

    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(5.0, connect=2.0), follow_redirects=False
            )
        return self._client

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self._lifespan(receive, send)
            return
        if scope["type"] != "http":
            return
        if scope.get("path") == "/healthz":
            await _start(send, 200, b"text/plain")
            await send({"type": "http.response.body", "body": b"ok\n"})
            return
        if scope.get("method") not in ("GET", "HEAD"):
            await _empty(send, 405)
            return
        headers = _headers(scope)
        target = parse_target(headers.get("x-live-object", ""))
        if target is None:
            await _empty(send, 404)
            return
        auth_headers = {
            "X-Original-URI": headers.get("x-original-uri", ""),
            "X-Real-IP": headers.get("x-real-ip", ""),
            "X-Request-ID": headers.get("x-request-id", ""),
            "X-Edge-Id": self.config.edge_id,
        }
        session = Session(self.config, self.client(), auth_headers)
        head = scope.get("method") == "HEAD"
        gone = asyncio.Event()
        watcher = asyncio.create_task(self._watch_disconnect(receive, gone))
        try:
            await self._serve(target, session, send, head=head, gone=gone)
        except StreamEnded as ended:
            logger.info("relay stream ended", extra={"channel": target.key[:12], "why": str(ended)})
        finally:
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher

    async def _lifespan(self, receive: Receive, send: Send) -> None:
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                if self._client is not None:
                    await self._client.aclose()
                await send({"type": "lifespan.shutdown.complete"})
                return

    @staticmethod
    async def _watch_disconnect(receive: Receive, gone: asyncio.Event) -> None:
        while True:
            message = await receive()
            if message.get("type") == "http.disconnect":
                gone.set()
                return

    async def _serve(
        self, target: Target, session: Session, send: Send, *, head: bool, gone: asyncio.Event
    ) -> None:
        if target.tail == layout.LIVE_PLAYLIST_TAIL:
            await self._live_playlist(target.key, send, head=head)
        elif target.tail == layout.LIVE_TS_TAIL:
            await self._live_ts(target.key, session, send, head=head, gone=gone)
        else:
            match = layout.WINDOW_TAIL.fullmatch(target.tail)
            if match is None:  # pragma: no cover (parse_target checked it)
                await _empty(send, 404)
                return
            start_ms = int(match["start"]) * 1000
            end_ms = start_ms + int(match["seconds"]) * 1000
            if match["ext"] == "m3u8":
                await self._archive_playlist(target.key, start_ms, end_ms, send, head=head)
            else:
                await self._archive_ts(
                    target.key, start_ms, end_ms, session, send, head=head, gone=gone
                )

    # -- the live playlist ------------------------------------------------------------------

    def _playlist(self, key: str) -> tuple[bytes, hls.MediaPlaylist] | None:
        raw = _read(layout.playlist_path(self.config.root, key))
        if raw is None or len(raw) > hls.MAX_PLAYLIST_BYTES:
            return None
        playlist = hls.parse_media_playlist(raw.decode("utf-8", "replace"))
        if playlist is None or not playlist.segments:
            return None
        return raw, playlist

    async def _live_playlist(self, key: str, send: Send, *, head: bool) -> None:
        deadline = time.monotonic() + self.config.playlist_timeout_s
        found = self._playlist(key)
        while found is None and time.monotonic() < deadline:
            await asyncio.sleep(POLL_S)
            found = self._playlist(key)
        if found is None:
            await _empty(send, 503, **{"retry-after": "2"})
            return
        await _start(send, 200, PLAYLIST_TYPE)
        await send({"type": "http.response.body", "body": b"" if head else found[0]})

    # -- continuous MPEG-TS -----------------------------------------------------------------

    async def _write(self, send: Send, data: bytes, session: Session, gone: asyncio.Event) -> None:
        for offset in range(0, len(data), CHUNK):
            if gone.is_set():
                raise StreamEnded("client_gone")
            chunk = data[offset : offset + CHUNK]
            await send({"type": "http.response.body", "body": chunk, "more_body": True})
            session.sent(len(chunk))

    async def _live_ts(
        self, key: str, session: Session, send: Send, *, head: bool, gone: asyncio.Event
    ) -> None:
        await _start(send, 200, TS_TYPE)
        if head:
            await send({"type": "http.response.body", "body": b""})
            return
        folder = layout.live_dir(self.config.root, key)
        next_sequence: int | None = None
        last_progress = time.monotonic()
        try:
            while True:
                await session.check()
                found = self._playlist(key)
                fresh = False
                if found is not None:
                    playlist = found[1]
                    segments = playlist.segments
                    if next_sequence is None or next_sequence < segments[0].sequence:
                        # Start (or after a restart, rejoin) two segments from the end.
                        next_sequence = segments[max(0, len(segments) - 2)].sequence
                    for segment in segments:
                        if segment.sequence < next_sequence or "/" in segment.uri:
                            continue
                        data = await asyncio.to_thread(_read, folder / segment.uri)
                        next_sequence = segment.sequence + 1
                        if data:
                            await self._write(send, data, session, gone)
                            fresh = True
                if fresh:
                    last_progress = time.monotonic()
                else:
                    idle = time.monotonic() - last_progress
                    limit = (
                        self.config.start_timeout_s
                        if next_sequence is None
                        else self.config.stall_s
                    )
                    if idle > limit:
                        raise StreamEnded("no_segments")
                    if next_sequence is None or found is None:
                        await self._write(send, NULL_PACKET * 7, session, gone)
                    await asyncio.sleep(POLL_S)
        finally:
            with contextlib.suppress(Exception):
                await send({"type": "http.response.body", "body": b""})

    # -- catch-up ------------------------------------------------------------------------------

    def _segments(self, key: str, start_ms: int, end_ms: int) -> list[layout.ArchiveSegment]:
        return layout.archive_segments(self.config.root, key, start_ms, end_ms)

    async def _archive_playlist(
        self, key: str, start_ms: int, end_ms: int, send: Send, *, head: bool
    ) -> None:
        segments = await asyncio.to_thread(self._segments, key, start_ms, end_ms)
        if not segments:
            await _empty(send, 404)
            return
        complete = end_ms <= int(time.time() * 1000)
        body = hls.archive_playlist(segments, complete=complete).encode()
        await _start(send, 200, PLAYLIST_TYPE)
        await send({"type": "http.response.body", "body": b"" if head else body})

    async def _archive_ts(  # noqa: PLR0913 (the window and the request's context)
        self,
        key: str,
        start_ms: int,
        end_ms: int,
        session: Session,
        send: Send,
        *,
        head: bool,
        gone: asyncio.Event,
    ) -> None:
        segments = await asyncio.to_thread(self._segments, key, start_ms, end_ms)
        if not segments:
            await _empty(send, 404)
            return
        await _start(send, 200, TS_TYPE)
        if head:
            await send({"type": "http.response.body", "body": b""})
            return
        sent: set[Path] = set()
        reached = start_ms
        last_progress = time.monotonic()
        try:
            while True:
                await session.check()
                fresh = False
                for segment in segments:
                    if segment.path in sent:
                        continue
                    sent.add(segment.path)
                    reached = max(reached, segment.end_ms)
                    data = await asyncio.to_thread(_read, segment.path)
                    if data:
                        await self._write(send, data, session, gone)
                        fresh = True
                if reached >= end_ms - 1000:
                    return
                if fresh:
                    last_progress = time.monotonic()
                elif time.monotonic() - last_progress > self.config.stall_s:
                    return  # the window reaches into a gap, or the recording stopped
                # A window still being recorded: follow the archive as it grows.
                await asyncio.sleep(1.0)
                segments = await asyncio.to_thread(self._segments, key, reached - 1000, end_ms)
        finally:
            with contextlib.suppress(Exception):
                await send({"type": "http.response.body", "body": b""})


app = Relay()
