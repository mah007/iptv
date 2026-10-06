"""The live packager: one ffmpeg per watched channel, copying its source into HLS
(ADR-0017). `manage.py run_live` runs it as the `live` service.

What runs:
- channels with catch-up (they record) and `always_on` channels, always;
- any other enabled, licensed channel while someone watches it: a live session
  record in redis-state (`sess:index` and the record's title) less than
  `live.idle_stop_s` old, or a wake-up (`live:wake`) published by `start_playback`.

Caps: `live.max_running_channels` in all, `live.realtime_transcode_max` of them
re-encoding; recording and always-on channels come first, then the most recent.

Each ffmpeg copies (or, when the channel says so, re-encodes in real time) into
`<root>/<key>/live/` as MPEG-TS segments in a rolling HLS window (profiles.yaml
`live:`). The packager then, every second:
- checks the playlist: `live` once it has a segment, a restart when it stalls;
- hard-links each new segment of a recording channel into the archive as
  `archive/YYYYMMDD/HH/<start ms>-<duration ms>.ts`, from the playlist's
  PROGRAM-DATE-TIME and EXTINF (`apps.live.layout`), and records the archive's
  first and last instants in `live:archive:<key>`;
- publishes `live:status:<key>` for the admin.

A failed ffmpeg restarts with exponential backoff (2 s, doubling to 60 s). Its
stderr never reaches a log or Redis unredacted: the exact source URL is masked
first (`sources.Redactor`).

Retention (every 10 minutes): hours older than a channel's catch-up window go,
archives of channels that no longer record go, and the oldest hours across
channels go while the total exceeds `LIVE_ARCHIVE_MAX_GB`.

The packager is control plane: it reads channels from Postgres when deciding what
to run. Media requests never wait on it: the edge serves segments from disk, and
the relay only reads files.
"""

import contextlib
import json
import os
import shutil
import signal
import socket
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import redis
import structlog

from apps.live import hls, layout, sources
from apps.media.profiles import LiveProfile, render_args

logger = structlog.get_logger(__name__)

HEARTBEAT_KEY = "hb:live"
STATUS_TTL_S = 30
TICK_S = 1.0
DESIRE_EVERY_S = 5.0
RETENTION_EVERY_S = 600.0
BACKOFF_MIN_S = 2.0
BACKOFF_MAX_S = 60.0
STOP_GRACE_S = 5.0
STDERR_LINES = 50


@dataclass(frozen=True, slots=True)
class ChannelSpec:
    """What the packager needs to run one channel (built from a `LiveChannel`)."""

    key: str
    source_url: str
    transcode: bool = False
    catchup_days: int = 0
    always_on: bool = False

    @property
    def permanent(self) -> bool:
        return self.catchup_days > 0 or self.always_on


@dataclass(frozen=True, slots=True)
class Limits:
    idle_stop_s: float = 150.0
    max_running: int = 20
    max_transcode: int = 1
    archive_max_bytes: int = 50 * 1024**3


# --- The ffmpeg command ---------------------------------------------------------------------


def ffmpeg_command(
    spec: ChannelSpec, out_dir: Path, profile: LiveProfile, *, ffmpeg: str = "ffmpeg"
) -> list[str]:
    """Copy (or re-encode) the source into a rolling HLS window of MPEG-TS segments."""
    command = [
        ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "warning",
        *sources.input_args(spec.source_url),
        "-fflags", "+genpts+discardcorrupt",
        "-i", spec.source_url,
        "-map", "0:v:0", "-map", "0:a?", "-sn", "-dn",
    ]  # fmt: skip
    if spec.transcode:
        settings = profile.transcode
        gop = str(2 * 25)
        command += render_args(
            settings.video_args,
            {
                "bitrate": f"{settings.video_k}k",
                "maxrate": f"{int(settings.video_k * 1.1)}k",
                "bufsize": f"{settings.video_k * 2}k",
                "gop": gop,
            },
        )
        command += [
            # A key frame every 2 s whatever the frame rate: segments cut on time.
            "-force_key_frames", "expr:gte(t,n_forced*2)",
            "-vf", f"scale=-2:'min({settings.max_height},ih)'",
            "-c:a", "aac", "-b:a", f"{settings.audio_k}k", "-ac", str(settings.audio_channels),
        ]  # fmt: skip
    else:
        command += ["-c", "copy"]
    command += [
        "-f", "hls",
        "-hls_time", str(profile.segment_s),
        "-hls_list_size", str(profile.list_size),
        "-hls_delete_threshold", "3",
        "-hls_flags",
        "delete_segments+program_date_time+independent_segments+temp_file+omit_endlist",
        "-hls_start_number_source", "epoch",
        "-hls_segment_type", "mpegts",
        "-hls_segment_filename", str(out_dir / "%d.ts"),
        str(out_dir / layout.PLAYLIST),
    ]  # fmt: skip
    return command


# --- Running channels -----------------------------------------------------------------------


class _Stderr(threading.Thread):
    """Drains a process's stderr into a bounded buffer (so ffmpeg never blocks on it)."""

    def __init__(self, stream: Any) -> None:
        super().__init__(daemon=True)
        self.stream = stream
        self.lines: deque[str] = deque(maxlen=STDERR_LINES)

    def run(self) -> None:
        with contextlib.suppress(Exception):
            for raw in iter(self.stream.readline, b""):
                self.lines.append(raw.decode("utf-8", "replace").rstrip())

    def last(self) -> str:
        return self.lines[-1] if self.lines else ""


@dataclass(slots=True)
class Running:
    spec: ChannelSpec
    process: Any = None
    stderr: _Stderr | None = None
    started: float = 0.0
    state: str = "starting"
    since: float = 0.0
    failures: int = 0
    retry_at: float = 0.0
    error: str = ""
    detail: str = ""
    last_change: float = 0.0
    playlist_mtime: float = 0.0
    archived_sequence: int = -1
    bitrate_kbps: int = 0
    redactor: Callable[[str], str] = field(default=lambda text: text)


def _process_alive(process: Any) -> bool:
    return process is not None and process.poll() is None


def default_spawn(command: list[str]) -> Any:
    return subprocess.Popen(  # noqa: S603
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )


class Packager:
    """The supervisor loop. Everything it touches is injected, so tests drive it."""

    def __init__(  # noqa: PLR0913 (collaborators)
        self,
        *,
        root: Path,
        state: redis.Redis,
        profile: LiveProfile,
        specs: Callable[[], Mapping[str, ChannelSpec]],
        limits: Callable[[], Limits],
        spawn: Callable[[list[str]], Any] = default_spawn,
        clock: Callable[[], float] = time.time,
        ffmpeg: str = "ffmpeg",
        guard: Callable[[str], None] = lambda url: None,
    ) -> None:
        self.root = root
        self.state = state
        self.profile = profile
        self.specs = specs
        self.limits = limits
        self.spawn = spawn
        self.clock = clock
        self.ffmpeg = ffmpeg
        #: Raises ValueError for a source the packager must never open (apps.live.egress).
        self.guard = guard
        self.running: dict[str, Running] = {}
        self.demand: dict[str, float] = {}
        self.known: dict[str, ChannelSpec] = {}
        self.archive_bytes: dict[str, int] = {}
        self._next_desire = 0.0
        self._next_retention = 0.0
        self.host = socket.gethostname()

    # -- lifecycle ---------------------------------------------------------------------------

    def clean_live_dirs(self) -> None:
        """At start: no playlist from an earlier run may be served as if it were live."""
        if not self.root.is_dir():
            return
        for entry in self.root.iterdir():
            if layout.CHANNEL_KEY.fullmatch(entry.name):
                shutil.rmtree(entry / layout.LIVE_DIR, ignore_errors=True)

    def wake(self, key: str) -> None:
        if layout.CHANNEL_KEY.fullmatch(key):
            self.demand[key] = self.clock()
            self._next_desire = 0.0

    def shutdown(self) -> None:
        for key in list(self.running):
            self._stop(key, reason="shutdown")

    # -- one pass ----------------------------------------------------------------------------

    def tick(self) -> None:
        now = self.clock()
        if now >= self._next_desire:
            self._next_desire = now + DESIRE_EVERY_S
            self._reconcile(now)
        for key in list(self.running):
            self._observe(key, now)
        if now >= self._next_retention:
            self._next_retention = now + RETENTION_EVERY_S
            self.retention(now)
        self._publish(now)

    def _viewer_keys(self, now: float, idle_s: float) -> dict[str, float]:
        """Channel keys of live sessions active within `idle_s`, with their last activity
        (the records' titles and `sess:index` scores)."""
        try:
            sessions = cast(
                "list[tuple[bytes, float]]",
                self.state.zrangebyscore("sess:index", now - idle_s, "+inf", withscores=True),
            )
            if not sessions:
                return {}
            pipe = self.state.pipeline(transaction=False)
            for session, _score in sessions:
                pipe.hget(f"sess:{session.decode()}", "title")
            titles = cast("list[bytes | None]", pipe.execute())
        except redis.RedisError:
            logger.warning("live.demand_unavailable")
            return dict.fromkeys(self.running, now)  # keep what runs rather than stop it all
        keys: dict[str, float] = {}
        for (_session, score), title in zip(sessions, titles, strict=True):
            kind, _, ident = (title or b"").decode().partition(":")
            if kind == "live":
                keys[ident] = max(keys.get(ident, 0.0), float(score))
        return keys

    def _reconcile(self, now: float) -> None:  # noqa: PLR0912
        limits = self.limits()
        try:
            self.known = dict(self.specs())
        except Exception:  # a database outage keeps what runs
            logger.exception("live.channels_unavailable")
            return
        for key, seen in self._viewer_keys(now, limits.idle_stop_s).items():
            self.demand[key] = max(self.demand.get(key, 0.0), seen)
        for key, seen in list(self.demand.items()):
            if now - seen > limits.idle_stop_s:
                del self.demand[key]
        wanted = [spec for spec in self.known.values() if spec.permanent or spec.key in self.demand]
        wanted.sort(key=lambda spec: (not spec.permanent, -self.demand.get(spec.key, 0.0)))
        chosen: list[ChannelSpec] = []
        transcoding = 0
        for spec in wanted:
            if len(chosen) >= limits.max_running:
                break
            if spec.transcode:
                if transcoding >= limits.max_transcode:
                    continue
                transcoding += 1
            chosen.append(spec)
        chosen_keys = {spec.key for spec in chosen}
        for key in list(self.running):
            current = self.running[key]
            if key not in chosen_keys:
                self._stop(key, reason="idle" if key in self.known else "removed")
            elif self.known[key] != current.spec:
                self._stop(key, reason="changed")
        for spec in chosen:
            if spec.key not in self.running:
                self.running[spec.key] = Running(
                    spec=spec, since=now, redactor=sources.Redactor(spec.source_url)
                )

    def _start(self, item: Running, now: float) -> None:
        try:
            self.guard(item.spec.source_url)
        except ValueError as refused:  # egress.UnsafeDestination (threat model G-11)
            item.state, item.error, item.detail = "failed", "unsafe_destination", str(refused)
            self._backoff(item, now)
            return
        out = layout.live_dir(self.root, item.spec.key)
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir(parents=True, exist_ok=True)
        command = ffmpeg_command(item.spec, out, self.profile, ffmpeg=self.ffmpeg)
        try:
            item.process = self.spawn(command)
        except OSError:
            item.process = None
            item.state, item.error, item.detail = "failed", "ffmpeg_missing", ""
            self._backoff(item, now)
            return
        stream = getattr(item.process, "stderr", None)
        item.stderr = _Stderr(stream) if stream is not None else None
        if item.stderr is not None:
            item.stderr.start()
        item.started = item.last_change = now
        item.playlist_mtime = 0.0
        item.archived_sequence = -1
        if item.state != "failed":
            item.state = "starting"
        logger.info("live.channel_started", channel=item.spec.key[:12])

    def _backoff(self, item: Running, now: float) -> None:
        item.failures += 1
        delay = min(BACKOFF_MAX_S, BACKOFF_MIN_S * 2 ** (item.failures - 1))
        item.retry_at = now + delay

    def _terminate(self, process: Any) -> None:
        if not _process_alive(process):
            return
        with contextlib.suppress(OSError):
            process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=STOP_GRACE_S)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(OSError):
                process.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=STOP_GRACE_S)

    def _stop(self, key: str, *, reason: str) -> None:
        item = self.running.pop(key, None)
        if item is None:
            return
        self._terminate(item.process)
        shutil.rmtree(layout.live_dir(self.root, key), ignore_errors=True)
        with contextlib.suppress(redis.RedisError):
            self.state.delete(layout.status_key(key))
        logger.info("live.channel_stopped", channel=key[:12], reason=reason)

    def _observe(self, key: str, now: float) -> None:
        item = self.running[key]
        if item.process is None:
            if now >= item.retry_at:
                self._start(item, now)
            return
        if not _process_alive(item.process):
            code = item.process.poll()
            item.detail = item.redactor(item.stderr.last() if item.stderr else "")[:300]
            item.state, item.error, item.process = "failed", f"exit_{code}", None
            self._backoff(item, now)
            shutil.rmtree(layout.live_dir(self.root, key), ignore_errors=True)
            logger.warning("live.channel_failed", channel=key[:12], code=code, detail=item.detail)
            return
        path = layout.playlist_path(self.root, key)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        if mtime and mtime != item.playlist_mtime:
            item.playlist_mtime, item.last_change = mtime, now
            self._read_playlist(item, path, now)
        stall = max(30.0, 6.0 * self.profile.segment_s)
        if now - item.last_change > stall:
            item.detail = item.redactor(item.stderr.last() if item.stderr else "")[:300]
            item.error = "stalled" if item.state == "live" else "no_output"
            item.state = "failed"
            self._terminate(item.process)
            item.process = None
            self._backoff(item, now)
            shutil.rmtree(layout.live_dir(self.root, key), ignore_errors=True)
            logger.warning("live.channel_stalled", channel=key[:12])

    def _read_playlist(self, item: Running, path: Path, now: float) -> None:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[: hls.MAX_PLAYLIST_BYTES]
        except OSError:
            return
        playlist = hls.parse_media_playlist(text)
        if playlist is None or not playlist.segments:
            return
        if item.state != "live":
            item.state, item.since, item.error, item.detail = "live", now, "", ""
            item.failures = 0
        sizes, seconds = 0, 0.0
        for segment in playlist.segments[-3:]:
            with contextlib.suppress(OSError):
                sizes += (path.parent / segment.uri).stat().st_size
                seconds += segment.duration_s
        if seconds > 0:
            item.bitrate_kbps = int(sizes * 8 / seconds / 1000)
        if item.spec.catchup_days > 0:
            self._archive(item, playlist, path.parent)

    def _archive(self, item: Running, playlist: hls.MediaPlaylist, folder: Path) -> None:
        first_new: int | None = None
        last_end: int | None = None
        for segment in playlist.segments:
            if segment.sequence <= item.archived_sequence or segment.program_date_time is None:
                continue
            start_ms = int(segment.program_date_time.timestamp() * 1000)
            duration_ms = max(1, round(segment.duration_s * 1000))
            target_dir = layout.archive_hour_dir(
                self.root, item.spec.key, segment.program_date_time
            )
            target = target_dir / layout.archive_file_name(start_ms, duration_ms)
            source = folder / segment.uri
            if "/" in segment.uri or not layout.ARCHIVE_FILE.fullmatch(target.name):
                continue
            try:
                target_dir.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    os.link(source, target)
                    self.archive_bytes[item.spec.key] = (
                        self.archive_bytes.get(item.spec.key, 0) + target.stat().st_size
                    )
            except OSError:
                continue
            item.archived_sequence = segment.sequence
            first_new = start_ms if first_new is None else first_new
            last_end = start_ms + duration_ms
        if last_end is None:
            return
        key = layout.archive_key(item.spec.key)
        with contextlib.suppress(redis.RedisError):
            pipe = self.state.pipeline(transaction=False)
            pipe.hsetnx(key, "first", str(first_new))
            pipe.hset(key, "last", str(last_end))
            pipe.execute()

    # -- retention ---------------------------------------------------------------------------

    def _hours(self, key: str) -> list[tuple[datetime, Path]]:
        hours = []
        archive = layout.archive_dir(self.root, key)
        for day in sorted(archive.glob("[0-9]" * 8)):
            for hour in sorted(day.glob("[0-9][0-9]")):
                try:
                    moment = datetime.strptime(f"{day.name}{hour.name}", "%Y%m%d%H").replace(
                        tzinfo=UTC
                    )
                except ValueError:
                    continue
                hours.append((moment, hour))
        return hours

    @staticmethod
    def _size(folder: Path) -> int:
        total = 0
        with contextlib.suppress(OSError), os.scandir(folder) as entries:
            for entry in entries:
                with contextlib.suppress(OSError):
                    total += entry.stat(follow_symlinks=False).st_size
        return total

    def retention(self, now: float) -> int:
        """Apply catch-up windows and the disk budget; returns the bytes the archive holds."""
        if not self.root.is_dir():
            return 0
        moment = datetime.fromtimestamp(now, tz=UTC)
        hours: list[tuple[datetime, str, Path, int]] = []
        for entry in self.root.iterdir():
            key = entry.name
            if not layout.CHANNEL_KEY.fullmatch(key):
                continue
            spec = self.known.get(key)
            if spec is None or spec.catchup_days <= 0:
                shutil.rmtree(entry / layout.ARCHIVE_DIR, ignore_errors=True)
                with contextlib.suppress(redis.RedisError):
                    self.state.delete(layout.archive_key(key))
                if key not in self.running and spec is None:
                    shutil.rmtree(entry, ignore_errors=True)
                continue
            oldest = moment - timedelta(days=spec.catchup_days)
            for start, folder in self._hours(key):
                if start + timedelta(hours=1) <= oldest:
                    shutil.rmtree(folder, ignore_errors=True)
                else:
                    hours.append((start, key, folder, self._size(folder)))
        total = sum(size for *_, size in hours)
        budget = self.limits().archive_max_bytes
        hours.sort()
        while total > budget and hours:
            _start, _key, folder, size = hours.pop(0)
            shutil.rmtree(folder, ignore_errors=True)
            total -= size
        self._refresh_archive_bounds(hours)
        return total

    def _refresh_archive_bounds(self, hours: Iterable[tuple[datetime, str, Path, int]]) -> None:
        per_key: dict[str, list[tuple[datetime, Path, int]]] = {}
        for start, key, folder, size in hours:
            per_key.setdefault(key, []).append((start, folder, size))
        self.archive_bytes = {
            key: sum(size for *_, size in items) for key, items in per_key.items()
        }
        for key, items in per_key.items():
            items.sort()
            earliest = None
            for _start, folder, _size in items:
                names = sorted(
                    entry.name
                    for entry in folder.iterdir()
                    if layout.ARCHIVE_FILE.fullmatch(entry.name)
                )
                if names:
                    earliest = names[0].split("-", 1)[0]
                    break
            with contextlib.suppress(redis.RedisError):
                if earliest is None:
                    self.state.delete(layout.archive_key(key))
                else:
                    self.state.hset(layout.archive_key(key), "first", str(int(earliest)))
        for day in self.root.glob(f"*/{layout.ARCHIVE_DIR}/*"):
            with contextlib.suppress(OSError):
                day.rmdir()  # only empty day folders go

    # -- status ------------------------------------------------------------------------------

    def _publish(self, now: float) -> None:
        try:
            pipe = self.state.pipeline(transaction=False)
            for key, item in self.running.items():
                status = {
                    "state": item.state,
                    "since": f"{item.since:.0f}",
                    "error": item.error,
                    "detail": item.detail,
                    "bitrate_kbps": str(item.bitrate_kbps),
                    "archive_bytes": str(self.archive_bytes.get(key, 0)),
                }
                pipe.hset(layout.status_key(key), mapping=status)
                pipe.expire(layout.status_key(key), STATUS_TTL_S)
            summary = {
                "host": self.host,
                "running": len(self.running),
                "live": sum(1 for item in self.running.values() if item.state == "live"),
                "archive_bytes": sum(self.archive_bytes.values()),
                "archive_budget_bytes": self.limits().archive_max_bytes,
                "at": int(now),
            }
            pipe.set(layout.PACKAGER_KEY, json.dumps(summary), ex=STATUS_TTL_S)
            pipe.set(HEARTBEAT_KEY, str(int(now)), ex=STATUS_TTL_S * 4)
            pipe.execute()
        except redis.RedisError:
            logger.warning("live.status_unpublished")
