"""The live packager (apps.live.packager): what runs, archiving, retention, status."""

import shutil
import subprocess
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from apps.core.stores import state_redis
from apps.live import hls, layout
from apps.live.packager import ChannelSpec, Limits, Packager, ffmpeg_command
from apps.media.profiles import LiveProfile, default_profiles

KEY_A = "01920000-0000-7000-8000-00000000000a"
KEY_B = "01920000-0000-7000-8000-00000000000b"
KEY_C = "01920000-0000-7000-8000-00000000000c"
SOURCE = "rtsp://encoder.example.net:8554/cam?token=packager-secret-77"
T0 = datetime(2026, 10, 6, 10, 0, tzinfo=UTC).timestamp()


@pytest.fixture(autouse=True)
def _clean_state() -> Iterator[None]:
    state_redis().flushdb()
    yield
    state_redis().flushdb()


@dataclass
class FakeProcess:
    command: list[str]
    code: int | None = None
    signals: list[int] = field(default_factory=list)
    stderr: Any = None

    def poll(self) -> int | None:
        return self.code

    def send_signal(self, number: int) -> None:
        self.signals.append(number)
        self.code = 0

    def wait(self, timeout: float | None = None) -> int:
        return self.code or 0

    def kill(self) -> None:
        self.code = -9


class World:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.now = T0
        self.specs: dict[str, ChannelSpec] = {}
        self.limits = Limits(
            idle_stop_s=150, max_running=10, max_transcode=1, archive_max_bytes=10**9
        )
        self.spawned: list[FakeProcess] = []
        self.refused: set[str] = set()
        self.packager = Packager(
            root=root,
            state=state_redis(),
            profile=LiveProfile(),
            specs=lambda: self.specs,
            limits=lambda: self.limits,
            spawn=self.spawn,
            clock=lambda: self.now,
            guard=self.guard,
        )

    def spawn(self, command: list[str]) -> FakeProcess:
        process = FakeProcess(command)
        self.spawned.append(process)
        return process

    def guard(self, url: str) -> None:
        if url in self.refused:
            raise ValueError("internal_host")

    def tick(self, seconds: float = 0.0) -> None:
        self.now += seconds
        self.packager.tick()

    def write_playlist(self, key: str, sequences: list[int], start: float) -> None:
        folder = layout.live_dir(self.root, key)
        folder.mkdir(parents=True, exist_ok=True)
        lines = ["#EXTM3U", "#EXT-X-VERSION:6", "#EXT-X-TARGETDURATION:4",
                 f"#EXT-X-MEDIA-SEQUENCE:{sequences[0]}"]  # fmt: skip
        for index, sequence in enumerate(sequences):
            stamp = datetime.fromtimestamp(start + 4 * index, tz=UTC)
            (folder / f"{sequence}.ts").write_bytes(b"\x47" * 188 * 50)
            lines += ["#EXTINF:4.000000,", f"#EXT-X-PROGRAM-DATE-TIME:{stamp.isoformat()}",
                      f"{sequence}.ts"]  # fmt: skip
        (folder / layout.PLAYLIST).write_text("\n".join(lines) + "\n")


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path / "live")


def watch(key: str, *, now: float, session: str = "a" * 32) -> None:
    client = state_redis()
    client.zadd("sess:index", {session: now})
    client.hset(f"sess:{session}", mapping={"title": f"live:{key}"})


def test_the_ffmpeg_command_copies_into_rolling_hls(tmp_path: Path) -> None:
    command = ffmpeg_command(ChannelSpec(KEY_A, SOURCE), tmp_path, default_profiles().live)
    assert command[command.index("-i") + 1] == SOURCE
    assert command[command.index("-c") + 1] == "copy"
    assert "-protocol_whitelist" in command
    assert command[command.index("-hls_segment_type") + 1] == "mpegts"
    assert command[command.index("-hls_start_number_source") + 1] == "epoch"
    assert "program_date_time" in command[command.index("-hls_flags") + 1]
    assert command[-1] == str(tmp_path / "index.m3u8")
    encode = ffmpeg_command(ChannelSpec(KEY_A, SOURCE, transcode=True), tmp_path, LiveProfile())
    assert "libx264" in encode
    assert "-c" not in encode


def test_permanent_channels_run_and_watched_ones_start(world: World) -> None:
    world.specs = {
        KEY_A: ChannelSpec(KEY_A, SOURCE, catchup_days=1),
        KEY_B: ChannelSpec(KEY_B, SOURCE),
        KEY_C: ChannelSpec(KEY_C, SOURCE, always_on=True),
    }
    world.tick()
    assert set(world.packager.running) == {KEY_A, KEY_C}
    watch(KEY_B, now=world.now)
    world.tick(5.0)
    assert KEY_B in world.packager.running
    assert len(world.spawned) == 3
    # Nobody watches B any more: it stops after live.idle_stop_s.
    world.tick(100.0)
    assert KEY_B in world.packager.running
    world.tick(100.0)
    assert KEY_B not in world.packager.running
    assert world.spawned[2].signals  # SIGINT


def test_a_wake_starts_a_channel_at_once(world: World) -> None:
    world.specs = {KEY_B: ChannelSpec(KEY_B, SOURCE)}
    world.tick()
    assert not world.packager.running
    world.packager.wake(KEY_B)
    world.tick(0.5)
    assert KEY_B in world.packager.running


def test_caps_prefer_permanent_channels_and_limit_transcoding(world: World) -> None:
    world.limits = Limits(idle_stop_s=150, max_running=2, max_transcode=1, archive_max_bytes=10**9)
    world.specs = {
        KEY_A: ChannelSpec(KEY_A, SOURCE, transcode=True),
        KEY_B: ChannelSpec(KEY_B, SOURCE, transcode=True),
        KEY_C: ChannelSpec(KEY_C, SOURCE, always_on=True),
    }
    for key in (KEY_A, KEY_B):
        world.packager.wake(key)
    world.tick()
    running = set(world.packager.running)
    assert KEY_C in running
    assert len(running) == 2
    assert len(running & {KEY_A, KEY_B}) == 1


def test_a_changed_or_removed_channel_restarts_or_stops(world: World) -> None:
    world.specs = {KEY_A: ChannelSpec(KEY_A, SOURCE, always_on=True)}
    world.tick()
    world.specs = {KEY_A: ChannelSpec(KEY_A, SOURCE + "&v=2", always_on=True)}
    world.tick(5.0)
    world.tick(0.1)
    assert world.packager.running[KEY_A].spec.source_url.endswith("v=2")
    assert len(world.spawned) == 2
    world.specs = {}
    world.tick(5.0)
    assert not world.packager.running


def test_a_playlist_makes_the_channel_live_and_is_archived(world: World) -> None:
    world.specs = {KEY_A: ChannelSpec(KEY_A, SOURCE, catchup_days=1)}
    world.tick()
    world.write_playlist(KEY_A, [1791279220, 1791279221], start=T0)
    world.tick(1.0)
    item = world.packager.running[KEY_A]
    assert item.state == "live"
    assert item.bitrate_kbps > 0
    segments = layout.archive_segments(world.root, KEY_A, int(T0 * 1000), int((T0 + 8) * 1000))
    assert [segment.duration_ms for segment in segments] == [4000, 4000]
    assert segments[0].start_ms == int(T0 * 1000)
    window = state_redis().hgetall(layout.archive_key(KEY_A))
    assert int(window[b"first"]) == int(T0 * 1000)
    assert int(window[b"last"]) == int((T0 + 8) * 1000)
    status = state_redis().hgetall(layout.status_key(KEY_A))
    assert status[b"state"] == b"live"
    assert state_redis().get("hb:live") is not None


def test_a_failed_ffmpeg_restarts_with_backoff_and_a_redacted_error(world: World) -> None:
    world.specs = {KEY_A: ChannelSpec(KEY_A, SOURCE, always_on=True)}
    world.tick()
    world.spawned[0].code = 1
    world.packager.running[KEY_A].redactor = __import__(
        "apps.live.sources", fromlist=["Redactor"]
    ).Redactor(SOURCE)
    world.tick(1.0)
    item = world.packager.running[KEY_A]
    assert (item.state, item.error) == ("failed", "exit_1")
    world.tick(1.0)
    assert len(world.spawned) == 1  # still backing off (2 s)
    world.tick(2.0)
    assert len(world.spawned) == 2


def test_a_stalled_channel_restarts(world: World) -> None:
    world.specs = {KEY_A: ChannelSpec(KEY_A, SOURCE, always_on=True)}
    world.tick()
    world.tick(31.0)
    item = world.packager.running[KEY_A]
    assert (item.state, item.error) == ("failed", "no_output")
    assert world.spawned[0].signals or world.spawned[0].code is not None


def test_refused_sources_never_start(world: World) -> None:
    world.refused = {SOURCE}
    world.specs = {KEY_A: ChannelSpec(KEY_A, SOURCE, always_on=True)}
    world.tick()
    item = world.packager.running[KEY_A]
    assert (item.state, item.error) == ("failed", "unsafe_destination")
    assert not world.spawned


def archive_hour(root: Path, key: str, moment: datetime, size: int = 1000) -> Path:
    folder = layout.archive_hour_dir(root, key, moment)
    folder.mkdir(parents=True, exist_ok=True)
    start_ms = int(moment.timestamp() * 1000)
    (folder / layout.archive_file_name(start_ms, 4000)).write_bytes(b"x" * size)
    return folder


def test_retention_applies_catch_up_windows_and_the_budget(world: World) -> None:
    now = datetime.fromtimestamp(T0, tz=UTC)
    world.specs = {
        KEY_A: ChannelSpec(KEY_A, SOURCE, catchup_days=1),
        KEY_B: ChannelSpec(KEY_B, SOURCE),  # no catch-up: its archive goes
    }
    world.packager.known = dict(world.specs)
    old = archive_hour(world.root, KEY_A, now - timedelta(days=2))
    keep_old = archive_hour(world.root, KEY_A, now - timedelta(hours=20))
    keep_new = archive_hour(world.root, KEY_A, now - timedelta(hours=1))
    stray = archive_hour(world.root, KEY_B, now - timedelta(hours=1))
    orphan = archive_hour(world.root, KEY_C, now - timedelta(hours=1))  # an unknown channel
    total = world.packager.retention(T0)
    assert not old.exists()
    assert keep_old.exists()
    assert keep_new.exists()
    assert not stray.exists()
    assert not orphan.exists()
    assert total == 2000
    first = state_redis().hget(layout.archive_key(KEY_A), "first")
    assert first is not None
    assert int(first) == int((now - timedelta(hours=20)).timestamp() * 1000)
    # Over budget: the oldest hours go first.
    world.limits = Limits(archive_max_bytes=1500)
    assert world.packager.retention(T0) == 1000
    assert not keep_old.exists()
    assert keep_new.exists()


def test_start_cleans_stale_live_folders(world: World) -> None:
    stale = layout.live_dir(world.root, KEY_A)
    stale.mkdir(parents=True)
    (stale / layout.PLAYLIST).write_text("#EXTM3U\n")
    world.packager.clean_live_dirs()
    assert not stale.exists()


def test_viewers_of_other_kinds_are_ignored(world: World) -> None:
    world.specs = {KEY_A: ChannelSpec(KEY_A, SOURCE)}
    client = state_redis()
    client.zadd("sess:index", {"b" * 32: world.now})
    client.hset(f"sess:{'b' * 32}", mapping={"title": f"movie:{KEY_A}"})
    world.tick()
    assert not world.packager.running


# --- The real ffmpeg -------------------------------------------------------------------------


@pytest.fixture
def served_ts(tmp_path: Path) -> Iterator[str]:
    """A synthetic MPEG-TS clip served over HTTP on loopback (ffmpeg only)."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is not installed here")
    folder = tmp_path / "www"
    folder.mkdir()
    subprocess.run(  # noqa: S603
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",  # noqa: S607
         "-i", "testsrc2=size=320x180:rate=25", "-f", "lavfi", "-i", "sine=frequency=440",
         "-t", "9", "-c:v", "libx264", "-preset", "ultrafast", "-g", "50", "-keyint_min", "50",
         "-sc_threshold", "0", "-c:a", "aac", "-f", "mpegts", str(folder / "clip.ts")],
        check=True,
        timeout=60,
    )  # fmt: skip
    handler = partial(SimpleHTTPRequestHandler, directory=str(folder))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/clip.ts"
    server.shutdown()


def test_the_real_command_writes_segments_ffmpeg_and_the_relay_can_read(
    tmp_path: Path, served_ts: str
) -> None:
    out = tmp_path / "out"
    out.mkdir()
    command = ffmpeg_command(ChannelSpec(KEY_A, served_ts), out, LiveProfile(segment_s=2))
    completed = subprocess.run(command, capture_output=True, timeout=60, check=False)  # noqa: S603
    assert completed.returncode == 0, completed.stderr[-500:]
    playlist = hls.parse_media_playlist((out / "index.m3u8").read_text())
    assert playlist is not None
    assert playlist.segments
    assert all(segment.program_date_time is not None for segment in playlist.segments)
    first = (out / playlist.segments[0].uri).read_bytes()
    assert first[0] == 0x47  # MPEG-TS sync byte
    assert playlist.segments[0].sequence > 1_000_000_000  # epoch-based: unique across restarts
