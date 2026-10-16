"""rclone, run as a separate process (MIT; ADR-0025).

Each run gets a private folder (mode 0700, on the importer's in-memory /tmp) holding
an rclone config file (0600) with the one remote `[src]`. Credentials reach rclone only
through that file: never its command line, never the environment of other processes,
never a log. The folder is deleted after the run; before that, what rclone itself wrote
back is read: a refreshed OAuth token (OneDrive rotates them) and a host key pinned on
first use (SFTP).

Errors are classified into the stable codes the admin translates (`TestStatus`), and
messages are scrubbed of every secret before they are stored.
"""

import contextlib
import json
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable, Iterator, Mapping
from configparser import ConfigParser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import structlog
from django.conf import settings

from apps.core.redaction import redact_text
from apps.imports.models import TestStatus
from apps.imports.remotes import REMOTE, rclone_section

logger = structlog.get_logger(__name__)

MESSAGE_MAX: Final = 300
LIST_TIMEOUT_S: Final = 90
OBSCURE_TIMEOUT_S: Final = 15
#: Stats lines come every STATS_S; the loop also wakes this often to check for a cancel.
STATS_S: Final = 2
_OUTPUT_LIMIT: Final = 32 * 1024 * 1024
#: Exit codes (rclone docs): 3 directory not found, 4 file not found, 5 temporary error.
EXIT_DIR_NOT_FOUND: Final = 3
EXIT_FILE_NOT_FOUND: Final = 4
EXIT_TEMPORARY: Final = 5

_AUTH = re.compile(
    r"unable to authenticate|permission denied|access ?denied|invalidaccesskeyid|"
    r"signaturedoesnotmatch|401 unauthorized|403 forbidden|statuscode: 40[13]|invalid_grant|"
    r"unauthorized|invalid_client|token expired|couldn't fetch token|authentication failed|"
    r"host key mismatch|knownhosts: key mismatch|ssh: handshake failed",
    re.IGNORECASE,
)
_NOT_FOUND = re.compile(
    r"directory not found|object not found|file not found|nosuchbucket|no such file|"
    r"not found|statuscode: 404|404 not found",
    re.IGNORECASE,
)
_UNREACHABLE = re.compile(
    r"connection refused|no such host|network is unreachable|no route to host|"
    r"connection reset|dial tcp|server misbehaving|eof",
    re.IGNORECASE,
)
_TIMEOUT = re.compile(r"i/o timeout|deadline exceeded|timed out|timeout", re.IGNORECASE)
_TLS = re.compile(r"x509: |tls: failed to verify|certificate signed by unknown", re.IGNORECASE)


class RcloneError(Exception):
    """rclone failed; `code` is a TestStatus value or `transfer_failed`."""

    def __init__(self, code: str, message: str, *, transient: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.transient = transient


def binary() -> str:
    return str(getattr(settings, "RCLONE_BINARY", "") or "rclone")


def available() -> bool:
    return shutil.which(binary()) is not None


def _env(home: Path) -> dict[str, str]:
    """A clean environment: nothing of ours leaks in, no stray RCLONE_* setting."""
    env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "HOME": str(home)}
    env["LANG"] = "C.UTF-8"
    for name in ("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy"):
        if name in os.environ:
            env[name] = os.environ[name]
    return env


def extra_ca_file() -> str | None:
    """A development-only CA bundle for the throwaway HTTPS test server (make import-test)."""
    path = str(getattr(settings, "IMPORT_EXTRA_CA_FILE", "") or "")
    return path if settings.DEBUG and path and Path(path).is_file() else None


def obscure(secret: str, *, home: Path | None = None) -> str:
    """rclone's reversible obscuring, which its config wants for passwords (read from
    stdin, so the password never appears in a process list)."""
    with tempfile.TemporaryDirectory(prefix="rclone-") as scratch:
        result = subprocess.run(  # noqa: S603 (fixed argv; the secret goes to stdin)
            [binary(), "obscure", "-"],
            input=secret,
            capture_output=True,
            text=True,
            timeout=OBSCURE_TIMEOUT_S,
            env=_env(home or Path(scratch)),
            check=False,
        )
    if result.returncode != 0 or not result.stdout.strip():
        raise RcloneError(TestStatus.UNEXPECTED, "rclone could not obscure a password.")
    return result.stdout.strip()


def scrub(text: str, secrets: Mapping[str, str]) -> str:
    """Remove every secret (and its obscured form) from rclone's output, then redact."""
    cleaned = text
    for value in sorted((v for v in secrets.values() if v and len(v) >= 4), key=len, reverse=True):
        cleaned = cleaned.replace(value, "***")
    return redact_text(cleaned)[:MESSAGE_MAX]


def classify(  # noqa: PLR0911 (one code per kind of failure)
    text: str, *, exit_code: int | None = None, timed_out: bool = False
) -> str:
    if timed_out:
        return TestStatus.TIMEOUT
    if _TLS.search(text):
        return TestStatus.TLS_FAILED
    if _AUTH.search(text):
        return TestStatus.AUTH_FAILED
    if exit_code in {EXIT_DIR_NOT_FOUND, EXIT_FILE_NOT_FOUND} or _NOT_FOUND.search(text):
        return TestStatus.NOT_FOUND
    if _TIMEOUT.search(text):
        return TestStatus.TIMEOUT
    if _UNREACHABLE.search(text):
        return TestStatus.UNREACHABLE
    return TestStatus.UNEXPECTED


def _last_error(stderr: str) -> str:
    """The most telling line of rclone's log (JSON or plain)."""
    best = ""
    for line in stderr.splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            entry = json.loads(text)
        except ValueError:
            entry = None
        if isinstance(entry, dict):
            if entry.get("level") in {"error", "critical", "fatal"} or "Failed" in str(
                entry.get("msg", "")
            ):
                best = str(entry.get("msg", ""))
        elif "ERROR" in text or "CRITICAL" in text or "Failed" in text:
            best = text
    if best:
        return best
    rest = stderr.strip().splitlines()
    return rest[-1] if rest else ""


@dataclass
class Session:
    """One rclone remote, configured in a private temporary folder."""

    kind: str
    options: dict[str, Any]
    secrets: dict[str, str]
    folder: Path
    config: Path
    #: What rclone wrote back (token, host_keys, drive_id, drive_type), after the run.
    updates: dict[str, str] = field(default_factory=dict)
    _section: dict[str, str] = field(default_factory=dict)

    @property
    def env(self) -> dict[str, str]:
        return _env(self.folder)

    def flags(self) -> list[str]:
        flags = [
            "--config",
            str(self.config),
            "--cache-dir",
            str(self.folder / "cache"),
            "--use-json-log",
            "--log-level",
            "NOTICE",
            "--contimeout",
            "30s",
            "--timeout",
            "5m",
            "--user-agent",
            "smart-iptv-importer",
        ]
        ca = extra_ca_file()
        if ca:
            flags += ["--ca-cert", ca]
        return flags

    def scrub(self, text: str) -> str:
        values = dict(self.secrets)
        values.update({f"rclone:{k}": v for k, v in self._section.items() if k in _SECRET_KEYS})
        return scrub(text, values)

    def read_back(self) -> None:
        parser = ConfigParser(interpolation=None)
        try:
            parser.read(self.config, encoding="utf-8")
        except (OSError, ValueError):
            return
        if not parser.has_section(REMOTE):
            return
        section = parser[REMOTE]
        for name in ("token", "host_keys", "drive_id", "drive_type"):
            value = section.get(name, "").strip()
            if value and value != self._section.get(name, ""):
                self.updates[name] = value


_SECRET_KEYS: Final = frozenset(
    {
        "access_key_id",
        "secret_access_key",
        "pass",
        "key_pem",
        "key_file_pass",
        "token",
        "client_secret",
    }
)


@contextlib.contextmanager
def session(
    kind: str,
    options: Mapping[str, Any],
    secrets: Mapping[str, str],
    *,
    pin_host_key: bool = False,
) -> Iterator[Session]:
    """A configured remote for the duration of the block; the folder is removed after."""
    base = getattr(settings, "IMPORT_TMP_DIR", None) or None
    folder = Path(tempfile.mkdtemp(prefix="rclone-", dir=base))
    os.chmod(folder, 0o700)
    try:
        section = rclone_section(
            kind,
            options,
            secrets,
            obscure=lambda text: obscure(text, home=folder),
            pin_host_key=pin_host_key,
        )
        config = folder / "rclone.conf"
        descriptor = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(f"[{REMOTE}]\n")
            for name, value in section.items():
                handle.write(f"{name} = {value}\n")
        current = Session(
            kind=kind,
            options=dict(options),
            secrets=dict(secrets),
            folder=folder,
            config=config,
            _section=section,
        )
        try:
            yield current
        finally:
            current.read_back()
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _run(current: Session, args: list[str], *, timeout: int) -> str:
    try:
        result = subprocess.run(  # noqa: S603 (fixed binary; credentials are in the config file)
            [binary(), *args, *current.flags()],
            capture_output=True,
            timeout=timeout,
            env=current.env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise RcloneError(
            TestStatus.TIMEOUT, "rclone took too long to answer.", transient=True
        ) from None
    except FileNotFoundError:
        raise RcloneError(TestStatus.UNEXPECTED, "rclone is not installed here.") from None
    stdout = result.stdout[:_OUTPUT_LIMIT].decode("utf-8", errors="replace")
    stderr = result.stderr[-65536:].decode("utf-8", errors="replace")
    if result.returncode != 0:
        message = current.scrub(_last_error(stderr) or f"rclone exited with {result.returncode}.")
        code = classify(stderr, exit_code=result.returncode)
        transient = result.returncode == EXIT_TEMPORARY or code in {
            TestStatus.UNREACHABLE,
            TestStatus.TIMEOUT,
        }
        raise RcloneError(code, message, transient=transient)
    return stdout


def lsjson(
    current: Session,
    remote: str,
    *,
    recursive: bool = False,
    stat: bool = False,
    timeout: int = LIST_TIMEOUT_S,
) -> list[dict[str, Any]]:
    """`rclone lsjson`: one folder (or, with `recursive`, every file under it; with `stat`,
    the one entry `remote` names)."""
    args = ["lsjson", remote, "--retries", "1", "--low-level-retries", "2", "--no-mimetype"]
    if recursive:
        args += ["-R", "--files-only"]
    if stat:
        args += ["--stat"]
    output = _run(current, args, timeout=timeout)
    try:
        data = json.loads(output or "[]")
    except ValueError:
        raise RcloneError(TestStatus.UNEXPECTED, "rclone's listing was unreadable.") from None
    if isinstance(data, dict):
        return [data]
    return [entry for entry in data if isinstance(entry, dict)]


def discover_onedrive(current: Session, *, timeout: int = LIST_TIMEOUT_S) -> dict[str, str]:
    """drive_id and drive_type of a OneDrive whose token is all we have, through rclone's
    own non-interactive configuration (it lists the account's drives; the default drive
    is taken). The token refresh it may do is written back like any other."""
    args = [
        "config",
        "update",
        REMOTE,
        "config_type",
        "onedrive",
        "config_refresh_token",
        "false",
        "--non-interactive",
    ]
    for _round in range(6):
        output = _run(current, args, timeout=timeout)
        try:
            question = json.loads(output) if output.strip().startswith("{") else {}
        except ValueError:
            question = {}
        if question.get("Error"):
            message = current.scrub(str(question["Error"]))
            raise RcloneError(classify(message), message)
        state = str(question.get("State") or "")
        if not state:
            break
        option = question.get("Option") or {}
        answer = option.get("Default")
        if isinstance(answer, list):
            answer = answer[0] if answer else ""
        if answer in (None, "") and option.get("Examples"):
            answer = option["Examples"][0].get("Value", "")
        args = [
            "config",
            "update",
            REMOTE,
            "--continue",
            "--state",
            state,
            "--result",
            "true" if isinstance(answer, bool) and answer else str(answer or ""),
            "--non-interactive",
        ]
    parser = ConfigParser(interpolation=None)
    parser.read(current.config, encoding="utf-8")
    section = parser[REMOTE] if parser.has_section(REMOTE) else {}
    found = {name: str(section.get(name, "")) for name in ("drive_id", "drive_type")}
    if not all(found.values()):
        raise RcloneError(TestStatus.NOT_FOUND, "No OneDrive drive was found for this account.")
    return found


@dataclass(frozen=True, slots=True)
class Stats:
    bytes: int
    total: int
    speed: float
    eta: int | None
    transfers: int
    total_transfers: int


def _stats(line: str) -> Stats | None:
    try:
        entry = json.loads(line)
    except ValueError:
        return None
    stats = entry.get("stats") if isinstance(entry, dict) else None
    if not isinstance(stats, dict):
        return None
    eta = stats.get("eta")
    return Stats(
        bytes=int(stats.get("bytes") or 0),
        total=int(stats.get("totalBytes") or 0),
        speed=float(stats.get("speed") or 0.0),
        eta=int(eta) if isinstance(eta, int | float) else None,
        transfers=int(stats.get("transfers") or 0),
        total_transfers=int(stats.get("totalTransfers") or 0),
    )


class Stopped(Exception):
    """The copy was stopped because `should_stop` said so."""


def copy(  # noqa: PLR0912, PLR0913, PLR0915 (one process loop; keyword-only arguments)
    current: Session,
    *,
    root: str,
    destination: Path,
    files: list[str],
    bwlimit_bytes: int = 0,
    on_stats: Callable[[Stats], None],
    should_stop: Callable[[], bool],
) -> None:
    """`rclone copy` of exactly `files` (paths relative to `root`) into `destination`.

    rclone retries a failed file (3 times) and every low-level call (10 times), skips
    files a previous attempt already finished, and checks a hash after each transfer
    where both sides have one in common (S3's MD5, Drive's MD5 and SHA-1, Dropbox's and
    OneDrive's own hashes; SFTP when the server lets it run md5sum)."""
    listing = current.folder / "files.txt"
    listing.write_text("".join(f"{name}\n" for name in files), encoding="utf-8")
    args = [
        binary(),
        "copy",
        root,
        str(destination),
        "--files-from-raw",
        str(listing),
        "--no-traverse",
        "--retries",
        "3",
        "--low-level-retries",
        "10",
        "--retries-sleep",
        "10s",
        "--stats",
        f"{STATS_S}s",
        "--stats-log-level",
        "NOTICE",
        "--transfers",
        "2",
        "--checkers",
        "4",
        *current.flags(),
    ]
    if bwlimit_bytes > 0:
        args += ["--bwlimit", f"{max(1, bwlimit_bytes // 1024)}Ki"]
    try:
        process = subprocess.Popen(  # noqa: S603 (fixed binary; credentials are in the config file)
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env=current.env,
        )
    except FileNotFoundError:
        raise RcloneError(TestStatus.UNEXPECTED, "rclone is not installed here.") from None
    lines: queue.Queue[str | None] = queue.Queue()
    tail: list[str] = []

    def pump() -> None:
        assert process.stderr is not None  # noqa: S101 (Popen with stderr=PIPE)
        for raw in process.stderr:
            lines.put(raw.decode("utf-8", errors="replace"))
        lines.put(None)

    reader = threading.Thread(target=pump, name="rclone-stderr", daemon=True)
    reader.start()
    stopped = False
    try:
        while True:
            if should_stop():
                stopped = True
                break
            try:
                line = lines.get(timeout=1.0)
            except queue.Empty:
                continue
            if line is None:
                break
            stats = _stats(line)
            if stats is not None:
                on_stats(stats)
            else:
                tail = [*tail[-40:], line]
    finally:
        if stopped or process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        reader.join(timeout=5)
    if stopped:
        raise Stopped
    code = process.wait()
    if code != 0:
        text = "".join(tail)
        message = current.scrub(_last_error(text) or f"rclone exited with {code}.")
        kind = classify(text, exit_code=code)
        transient = code == EXIT_TEMPORARY or kind in {TestStatus.UNREACHABLE, TestStatus.TIMEOUT}
        raise RcloneError(
            kind if kind != TestStatus.UNEXPECTED else "transfer_failed",
            message,
            transient=transient,
        )
