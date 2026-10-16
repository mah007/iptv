"""Downloads of one direct link (ADR-0025): HTTPS, resumable, guarded.

- Every request, redirects included, goes through the egress guard
  (`apps.live.egress.guarded_client`): no loopback, link-local or metadata addresses and
  none of our own services. Links must be HTTPS; plain HTTP only with DEBUG (the dev
  stack's test server), and a redirect may never drop to HTTP.
- An interrupted transfer resumes with an HTTP `Range` request from the bytes already
  on disk (up to `ATTEMPTS` times, waiting longer each time); a server that ignores the
  range makes it start over.
- An expected checksum (SHA-256, SHA-1 or MD5, chosen by its length) is verified once
  the file is complete.
"""

import hashlib
import re
import ssl
import time
from collections.abc import Callable
from dataclasses import dataclass
from email.message import Message
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

import certifi
import httpx
from django.conf import settings

from apps.imports.files import name_from_url
from apps.imports.rclone import extra_ca_file
from apps.live import egress

CHUNK: Final = 1024 * 1024
ATTEMPTS: Final = 6
BACKOFF_S: Final = (5, 15, 30, 60, 120)
PROGRESS_S: Final = 2.0
SPACE_CHECK_S: Final = 10.0
_HASHES: Final = {32: "md5", 40: "sha1", 64: "sha256"}
_HEX = re.compile(r"^[0-9a-fA-F]+$")
_CONTENT_RANGE = re.compile(r"bytes (\d+)-(\d+)/(\d+|\*)")
_TOTAL_RANGE = re.compile(r"bytes \*/(\d+)")

#: Tests route requests through a transport and trust their own CA.
_transport: httpx.BaseTransport | None = None
_verify: ssl.SSLContext | None = None


class DownloadError(Exception):
    def __init__(self, code: str, message: str, *, transient: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.transient = transient


class Stopped(Exception):
    """`should_stop` asked to stop."""


def checksum_algorithm(value: str) -> str | None:
    text = value.strip().lower()
    if not text:
        return None
    if not _HEX.match(text) or len(text) not in _HASHES:
        msg = "Give a SHA-256, SHA-1 or MD5 checksum in hexadecimal."
        raise ValueError(msg)
    return _HASHES[len(text)]


def allowed_schemes() -> frozenset[str]:
    return frozenset({"https", "http"}) if settings.DEBUG else frozenset({"https"})


class LinkRefused(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def check_link(url: str) -> None:
    """What the web process can check before saving a link (no DNS there)."""
    parts = urlsplit(url)
    if parts.scheme not in allowed_schemes() or not parts.hostname:
        raise LinkRefused("link_not_https", "Use an https:// link.")
    if parts.username or parts.password:
        raise LinkRefused(
            "credentials_in_url", "Links with a user name or password aren't supported."
        )
    egress.check_url_static(url)


def display(url: str) -> str:
    """The link without user info and query string, for the history."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{host}{port}{parts.path}"[:500]


def _context() -> ssl.SSLContext | bool:
    if _verify is not None:
        return _verify
    context = ssl.create_default_context(cafile=certifi.where())
    extra = extra_ca_file()
    if extra:
        context.load_verify_locations(cafile=extra)
    return context


def client() -> httpx.Client:
    schemes = allowed_schemes()

    def scheme(request: httpx.Request) -> None:
        if request.url.scheme not in schemes:
            raise egress.UnsafeDestination("insecure_redirect")

    guarded = egress.guarded_client(
        follow_redirects=True,
        timeout=httpx.Timeout(30.0, read=120.0),
        # No compression: byte counts and Range offsets are those of the file itself.
        headers={"User-Agent": "smart-iptv-importer", "Accept-Encoding": "identity"},
        verify=_context(),
        transport=_transport,
    )
    guarded.event_hooks["request"].insert(0, scheme)
    return guarded


def _status_error(response: httpx.Response) -> DownloadError:
    code = response.status_code
    if code in {401, 403}:
        return DownloadError("auth_failed", f"The server refused the link (HTTP {code}).")
    if code in {404, 410}:
        return DownloadError("not_found", f"Nothing at this link (HTTP {code}).")
    transient = code == 429 or code >= 500
    return DownloadError(
        "transfer_failed", f"The server answered HTTP {code}.", transient=transient
    )


def _connection_error(exc: httpx.HTTPError, message: str) -> DownloadError:
    """A certificate that doesn't verify is final (retrying won't fix it); other
    connection failures are worth another try."""
    if isinstance(exc, httpx.ConnectError) and "CERTIFICATE_VERIFY_FAILED" in str(exc):
        return DownloadError("tls_failed", "The server's certificate couldn't be verified.")
    return DownloadError("unreachable", message, transient=True)


@dataclass(frozen=True, slots=True)
class LinkInfo:
    size: int | None
    name: str


def _disposition_name(header: str) -> str:
    if not header:
        return ""
    message = Message()
    message["content-disposition"] = header
    name = message.get_filename() or ""
    return name.replace("/", "").replace("\\", "").strip()


def probe(url: str) -> LinkInfo:
    """The file's size (None when the server won't say) and name, before downloading."""
    with client() as http:
        try:
            response = http.head(url)
            if response.status_code in {405, 501} or (
                response.is_success and "content-length" not in response.headers
            ):
                response = http.get(url, headers={"Range": "bytes=0-0"})
                response.close()
        except egress.UnsafeDestination as exc:
            raise DownloadError("unsafe_destination", f"Not allowed: {exc.reason}.") from None
        except httpx.TimeoutException:
            raise DownloadError("timeout", "The server took too long.", transient=True) from None
        except httpx.HTTPError as exc:
            raise _connection_error(exc, "The server could not be reached.") from None
    if not response.is_success:
        raise _status_error(response)
    size: int | None = None
    total = _CONTENT_RANGE.match(response.headers.get("content-range", ""))
    if response.status_code == 206 and total and total[3] != "*":
        size = int(total[3])
    elif response.status_code == 200 and response.headers.get("content-length", "").isdigit():
        size = int(response.headers["content-length"])
    name = _disposition_name(response.headers.get("content-disposition", "")) or name_from_url(
        str(response.url)
    )
    return LinkInfo(size=size, name=name)


class _Throttle:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.start = time.monotonic()
        self.sent = 0

    def take(self, count: int) -> None:
        if self.limit <= 0:
            return
        self.sent += count
        ahead = self.sent / self.limit - (time.monotonic() - self.start)
        if ahead > 0:
            time.sleep(min(ahead, 5.0))


def download(  # noqa: PLR0912, PLR0913, PLR0915 (one resumable loop)
    url: str,
    destination: Path,
    *,
    expected_size: int | None,
    bwlimit_bytes: int = 0,
    on_progress: Callable[[int, int | None, float], None],
    should_stop: Callable[[], bool],
    check_space: Callable[[], None],
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Download `url` to `destination`, resuming from what is there. Returns its size."""
    throttle = _Throttle(bwlimit_bytes)
    total = expected_size
    last: DownloadError | None = None
    for attempt in range(ATTEMPTS):
        if attempt:
            sleep(BACKOFF_S[min(attempt - 1, len(BACKOFF_S) - 1)])
        if should_stop():
            raise Stopped
        have = destination.stat().st_size if destination.exists() else 0
        if total is not None and have == total and have > 0:
            return have
        headers = {"Range": f"bytes={have}-"} if have else {}
        try:
            with client() as http, http.stream("GET", url, headers=headers) as response:
                if response.status_code == 416 and have:
                    matched = _TOTAL_RANGE.match(response.headers.get("content-range", ""))
                    if matched and int(matched[1]) == have:
                        return have
                    destination.unlink(missing_ok=True)
                    continue
                if not response.is_success:
                    error = _status_error(response)
                    if not error.transient:
                        raise error
                    last = error
                    continue
                if response.status_code == 206:
                    matched = _CONTENT_RANGE.match(response.headers.get("content-range", ""))
                    if not matched or int(matched[1]) != have:
                        destination.unlink(missing_ok=True)
                        have = 0
                        last = DownloadError(
                            "transfer_failed", "The server sent another range.", transient=True
                        )
                        continue
                    if matched[3] != "*":
                        total = int(matched[3])
                    mode = "ab"
                else:  # 200: the server ignored the range; start over
                    have = 0
                    mode = "wb"
                    length = response.headers.get("content-length", "")
                    total = int(length) if length.isdigit() else total
                done = have
                tick = space_tick = time.monotonic()
                window_bytes, window_start = 0, tick
                with destination.open(mode) as handle:
                    # As the bytes arrive (not 1 MiB at a time): a drop loses nothing
                    # that reached us, and the next attempt resumes right after it.
                    for chunk in response.iter_raw():
                        handle.write(chunk)
                        done += len(chunk)
                        window_bytes += len(chunk)
                        throttle.take(len(chunk))
                        now = time.monotonic()
                        if now - tick >= PROGRESS_S:
                            speed = window_bytes / max(now - window_start, 1e-6)
                            on_progress(done, total, speed)
                            window_bytes, window_start, tick = 0, now, now
                            if should_stop():
                                raise Stopped
                        if now - space_tick >= SPACE_CHECK_S:
                            check_space()
                            space_tick = now
                on_progress(done, total, 0.0)
                if total is not None and done != total:
                    last = DownloadError(
                        "transfer_failed",
                        "The connection ended before the file did.",
                        transient=True,
                    )
                    continue
                return done
        except egress.UnsafeDestination as exc:
            raise DownloadError("unsafe_destination", f"Not allowed: {exc.reason}.") from None
        except httpx.TimeoutException:
            last = DownloadError("timeout", "The server took too long.", transient=True)
        except httpx.HTTPError as exc:
            last = _connection_error(exc, "The connection failed.")
            if not last.transient:
                raise last from None
    raise last or DownloadError("transfer_failed", "The download failed.", transient=True)


def verify(path: Path, expected: str) -> None:
    """Compare the file's checksum with the one the admin gave."""
    algorithm = checksum_algorithm(expected)
    if algorithm is None:
        return
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        while block := handle.read(CHUNK * 8):
            digest.update(block)
    if digest.hexdigest() != expected.strip().lower():
        raise DownloadError("checksum_mismatch", f"The file's {algorithm.upper()} doesn't match.")
