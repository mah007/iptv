"""The live relay (apps.live.relay): a raw ASGI app, driven here without a server."""

import asyncio
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from apps.live import layout
from apps.live.relay import NULL_PACKET, Relay, RelayConfig, parse_target

KEY = "01920000-0000-7000-8000-0000000000aa"
TOKEN_URI = f"/v/k2.{'ab' * 16}.{KEY}.live.1791279220.sig/live.ts"


@dataclass
class Exchange:
    status: int = 0
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""


class AuthStub:
    """stream-auth: answers from a list, then the last answer forever."""

    def __init__(self, *answers: int) -> None:
        self.answers = list(answers) or [204]
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        status = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return httpx.Response(status)


def make_relay(root: Path, auth: AuthStub | None = None, **overrides: Any) -> Relay:
    values: dict[str, Any] = {
        "root": root,
        "auth_url": "http://web:8000/internal/stream-auth",
        "auth_host": "web",
        "auth_interval_s": 0.0,
        "auth_grace_s": 0.0,
        "start_timeout_s": 0.4,
        "playlist_timeout_s": 0.3,
        "stall_s": 0.3,
    }
    values.update(overrides)
    relay = Relay(RelayConfig(**values))
    relay._client = httpx.AsyncClient(transport=httpx.MockTransport(auth or AuthStub()))
    return relay


def call(
    relay: Relay, tail: str, *, method: str = "GET", key: str = KEY, path: str = "/x"
) -> Exchange:
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [
            (b"x-live-object", f"{key}/{tail}".encode()),
            (b"x-original-uri", TOKEN_URI.encode()),
            (b"x-real-ip", b"203.0.113.9"),
        ],
    }
    result = Exchange()

    async def receive() -> dict[str, Any]:
        await asyncio.sleep(3600)
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            result.status = message["status"]
            result.headers = {k.decode(): v.decode() for k, v in message["headers"]}
        else:
            result.body += message.get("body", b"")

    asyncio.run(asyncio.wait_for(relay(scope, receive, send), timeout=10))
    return result


def write_live(root: Path, sequences: list[int]) -> list[bytes]:
    folder = layout.live_dir(root, KEY)
    folder.mkdir(parents=True, exist_ok=True)
    lines = ["#EXTM3U", "#EXT-X-TARGETDURATION:4", f"#EXT-X-MEDIA-SEQUENCE:{sequences[0]}"]
    payloads = []
    for sequence in sequences:
        data = bytes([0x47]) + sequence.to_bytes(8, "big") * 10
        (folder / f"{sequence}.ts").write_bytes(data)
        payloads.append(data)
        lines += ["#EXTINF:4.0,", f"{sequence}.ts"]
    (folder / layout.PLAYLIST).write_text("\n".join(lines) + "\n")
    return payloads


def write_archive(root: Path, start: datetime, count: int) -> list[bytes]:
    payloads = []
    for index in range(count):
        start_ms = int(start.timestamp() * 1000) + 4000 * index
        moment = datetime.fromtimestamp(start_ms / 1000, tz=UTC)
        folder = layout.archive_hour_dir(root, KEY, moment)
        folder.mkdir(parents=True, exist_ok=True)
        data = b"\x47archive" + str(index).encode()
        (folder / layout.archive_file_name(start_ms, 4000)).write_bytes(data)
        payloads.append(data)
    return payloads


def test_parse_target() -> None:
    assert parse_target(f"{KEY}/live.ts") is not None
    assert parse_target(f"{KEY}/archive/1791279220-60.m3u8") is not None
    for bad in (
        f"{KEY}/live/1.ts",
        f"{KEY}/../x",
        "abc/live.ts",
        f"{KEY.replace('-', '')}/live.ts",
        "",
    ):
        assert parse_target(bad) is None


def test_health_and_unknown_objects(tmp_path: Path) -> None:
    relay = make_relay(tmp_path)
    assert call(relay, "live.ts", path="/healthz").status == 200
    assert call(relay, "live/1.ts").status == 404
    assert call(relay, "live.ts", method="POST").status == 405


def test_the_live_playlist_waits_then_gives_up(tmp_path: Path) -> None:
    relay = make_relay(tmp_path)
    missing = call(relay, "live/index.m3u8")
    assert missing.status == 503
    assert missing.headers["retry-after"] == "2"
    write_live(tmp_path, [10, 11])
    found = call(relay, "live/index.m3u8")
    assert found.status == 200
    assert found.headers["content-type"] == "application/vnd.apple.mpegurl"
    assert b"10.ts" in found.body


def test_live_ts_joins_the_newest_segments(tmp_path: Path) -> None:
    payloads = write_live(tmp_path, [10, 11, 12])
    auth = AuthStub(204)
    result = call(make_relay(tmp_path, auth), "live.ts")
    assert result.status == 200
    assert result.headers["content-type"] == "video/mp2t"
    # Starts two segments from the end, then stops when nothing new arrives.
    assert result.body == payloads[1] + payloads[2]
    # stream-auth is asked with the edge's headers and the bytes sent.
    assert auth.calls
    sent = auth.calls[-1]
    assert sent.headers["X-Original-URI"] == TOKEN_URI
    assert sent.headers["X-Real-IP"] == "203.0.113.9"
    assert sent.headers["Host"] == "web"


def test_a_cold_channel_gets_null_packets_until_it_starts(tmp_path: Path) -> None:
    result = call(make_relay(tmp_path), "live.ts")
    assert result.status == 200
    assert result.body
    assert result.body.startswith(NULL_PACKET)
    assert len(result.body) % 188 == 0


def test_a_denied_session_ends_the_stream(tmp_path: Path) -> None:
    write_live(tmp_path, [10, 11, 12])
    result = call(make_relay(tmp_path, AuthStub(403)), "live.ts")
    assert result.status == 200
    assert result.body == b""


def test_stream_auth_outages_end_streams_after_the_grace(tmp_path: Path) -> None:
    write_live(tmp_path, [10, 11])
    result = call(make_relay(tmp_path, AuthStub(503), stall_s=5.0), "live.ts")
    assert result.body == b""


def test_catch_up_playlists_and_streams(tmp_path: Path) -> None:
    start = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
    payloads = write_archive(tmp_path, start, 5)
    stamp = int(start.timestamp())
    playlist = call(make_relay(tmp_path), f"archive/{stamp}-12.m3u8")
    assert playlist.status == 200
    text = playlist.body.decode()
    assert "#EXT-X-PLAYLIST-TYPE:VOD" in text
    assert "#EXT-X-ENDLIST" in text
    assert text.count("#EXTINF:4.000,") == 3
    assert "20261004/12/" in text
    stream = call(make_relay(tmp_path), f"archive/{stamp}-12.ts")
    assert stream.status == 200
    assert stream.body == b"".join(payloads[:3])
    gap = call(make_relay(tmp_path), f"archive/{stamp - 3600}-60.ts")
    assert gap.status == 404


def test_the_relay_never_imports_django() -> None:
    code = (
        "import sys, apps.live.relay\n"
        "loaded = [m for m in sys.modules if m.split('.')[0] == 'django']\n"
        "assert not loaded, loaded\n"
    )
    completed = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        cwd=str(Path(__file__).resolve().parents[3]),
    )
    assert completed.returncode == 0, completed.stderr
