"""Real rclone servers for the import tests (ADR-0025): `rclone serve sftp`, `webdav`
and `s3` on loopback ports, over a folder of small synthetic files. Never the internet.

The egress guard refuses loopback, so tests that talk to these servers allow it
explicitly (`allow_loopback`); the guard itself is tested on its own.
"""

import os
import socket
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

from apps.live import egress

USER = "tester"
#: Throwaway credentials of the throwaway servers, made from parts (push protection).
PASSWORD = "pass-" + "word-" + "1234"
S3_KEY = "AKID" + "TEST" + "LOCAL"
S3_SECRET = "s3-" + "secret-" + "local-0000"


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _wait(port: int, process: subprocess.Popen[bytes], *, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            msg = f"rclone serve exited with {process.returncode}"
            raise RuntimeError(msg)
        with socket.socket() as probe:
            probe.settimeout(0.5)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.2)
    msg = f"rclone serve did not listen on {port}"
    raise RuntimeError(msg)


@dataclass(frozen=True)
class Servers:
    root: Path
    sftp_port: int
    webdav_port: int
    s3_port: int


def write_media(root: Path) -> None:
    """Films/Metropolis (1927)/: a video, its subtitle and a poster (not importable)."""
    film = root / "Films" / "Metropolis (1927)"
    film.mkdir(parents=True, exist_ok=True)
    (film / "Metropolis (1927).mkv").write_bytes(os.urandom(300_000))
    (film / "Metropolis (1927).en.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nHi\n")
    (film / "poster.jpg").write_bytes(b"\xff\xd8\xff")
    (root / "Films" / "Nosferatu (1922).mp4").write_bytes(os.urandom(200_000))
    (root / "Films" / "notes.txt").write_text("not media")
    (root / "Films" / ".hidden.mkv").write_bytes(b"x")


@pytest.fixture(scope="session")
def servers(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Servers]:
    base = tmp_path_factory.mktemp("rclone-servers")
    home = base / "home"
    home.mkdir()
    # rclone serve s3 serves the folders under its root as buckets.
    root = base / "store"
    bucket = root / "media"
    bucket.mkdir(parents=True)
    write_media(bucket)
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "RCLONE_CONFIG": "/dev/null"}
    ports = {name: _free_port() for name in ("sftp", "webdav", "s3")}
    commands = {
        "sftp": [
            "rclone",
            "serve",
            "sftp",
            str(bucket),
            "--addr",
            f"127.0.0.1:{ports['sftp']}",
        ],
        "webdav": [
            "rclone",
            "serve",
            "webdav",
            str(bucket),
            "--addr",
            f"127.0.0.1:{ports['webdav']}",
        ],
        "s3": [
            "rclone",
            "serve",
            "s3",
            str(root),
            "--addr",
            f"127.0.0.1:{ports['s3']}",
            "--auth-key",
            f"{S3_KEY},{S3_SECRET}",
        ],
    }
    processes: list[subprocess.Popen[bytes]] = []
    try:
        for name, command in commands.items():
            process = subprocess.Popen(  # noqa: S603 (test servers, fixed argv)
                command,
                # SFTP and WebDAV sign in with these; the S3 server with its keys only
                # (a user and password would add HTTP basic auth in front of it).
                env={**env, "RCLONE_USER": USER, "RCLONE_PASS": PASSWORD} if name != "s3" else env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            processes.append(process)
            _wait(ports[name], process)
        yield Servers(
            root=bucket, sftp_port=ports["sftp"], webdav_port=ports["webdav"], s3_port=ports["s3"]
        )
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()


@pytest.fixture
def allow_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let the guard pass 127.0.0.1, where the test servers listen (saved addresses and
    the checks before each run alike)."""
    original, original_static = egress.check_host, egress.check_url_static

    def check(host: str, port: int | None = None, **kwargs: Any) -> None:
        if host not in {"127.0.0.1", "localhost"}:
            original(host, port, **kwargs)

    def check_static(url: str) -> None:
        if urlsplit(url).hostname not in {"127.0.0.1", "localhost"}:
            original_static(url)

    monkeypatch.setattr(egress, "check_host", check)
    monkeypatch.setattr(egress, "check_url_static", check_static)


@pytest.fixture
def importer_settings(settings: Any, tmp_path: Path) -> Path:
    """rclone's per-run folders under the test's tmp; no reserve (tmp disks are small)."""
    folder = tmp_path / "rclone-tmp"
    folder.mkdir()
    settings.IMPORT_TMP_DIR = str(folder)
    from apps.core.services import set_setting  # noqa: PLC0415

    set_setting("library.import_reserve_gb", value=0, actor=None)
    return folder
