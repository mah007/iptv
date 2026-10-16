"""Direct links: HTTPS only, every hop guarded, resumed with Range requests after a
dropped connection, checksums verified, refusals named (ADR-0025). The server is a local
HTTPS server with a throwaway certificate; never the internet."""

import datetime as dt
import hashlib
import ipaddress
import os
import socket
import ssl
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar, cast

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from apps.imports import direct, services
from apps.imports.models import ImportFile, ImportJob, JobStatus
from apps.library.models import Library
from apps.live import egress

pytestmark = pytest.mark.django_db

FILM = os.urandom(400_000)
NAME = "Night of the Living Dead (1968).mp4"


class Handler(BaseHTTPRequestHandler):
    drops: ClassVar[list[str]] = []
    seen: ClassVar[list[tuple[str, str, str]]] = []

    def log_message(self, *_args: Any) -> None:
        return

    def _film(self, head: bool) -> None:
        wanted = self.headers.get("Range", "")
        Handler.seen.append((self.command, self.path, wanted))
        start = int(wanted.removeprefix("bytes=").rstrip("-")) if wanted else 0
        body = FILM[start:]
        self.send_response(206 if start else 200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(len(body)))
        if start:
            self.send_header("Content-Range", f"bytes {start}-{len(FILM) - 1}/{len(FILM)}")
        if self.path.startswith("/download"):
            self.send_header("Content-Disposition", 'attachment; filename="Plan 9 (1957).mp4"')
        self.end_headers()
        if head:
            return
        plain = self.path.partition("?")[0]
        if plain in Handler.drops:
            Handler.drops.remove(plain)
            self.wfile.write(body[: len(body) // 2])
            self.wfile.flush()
            self.connection.shutdown(2)
            return
        self.wfile.write(body)

    def _route(self, head: bool) -> None:
        if self.path in {"/forbidden.mp4", "/missing.mp4"}:
            self.send_response(403 if "forbidden" in self.path else 404)
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif self.path == "/to-http.mp4":
            self.send_response(302)
            port = cast("ThreadingHTTPServer", self.server).server_port
            self.send_header("Location", f"http://127.0.0.1:{port}/films/x.mp4")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self._film(head)

    def do_HEAD(self) -> None:
        self._route(head=True)

    def do_GET(self) -> None:
        self._route(head=False)


@pytest.fixture(scope="module")
def https(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, ssl.SSLContext]]:
    folder = tmp_path_factory.mktemp("tls")
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = folder / "cert.pem", folder / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = ssl.create_default_context(cafile=str(cert_path))
    try:
        yield f"https://127.0.0.1:{server.server_port}", client
    finally:
        server.shutdown()


@pytest.fixture
def trusted(https: tuple[str, ssl.SSLContext], monkeypatch: pytest.MonkeyPatch) -> str:
    """The server's certificate trusted, no waits between attempts."""
    base, context = https
    monkeypatch.setattr(direct, "_verify", context)
    monkeypatch.setattr(direct, "BACKOFF_S", (0, 0, 0, 0, 0))
    Handler.drops.clear()
    Handler.seen.clear()
    return base


@pytest.fixture
def library(make_library: Callable[..., Library]) -> Library:
    return make_library()


def link_job(library: Library, url: str, **extra: Any) -> ImportJob:
    values: dict[str, Any] = {
        "file_name": "",
        "checksum": "",
        "subfolder": "",
        "rights_holder": "Public domain",
        "license_ref": "https://archive.org/details/night_of_the_living_dead",
        "license_expires_at": None,
        **extra,
    }
    return services.create_link_job(url=url, library=library, actor=None, ip=None, **values)


def run(job: ImportJob) -> str | None:
    services.dispatch()
    job.refresh_from_db()
    return services.run(job.pk, job.dispatch_no)


@pytest.mark.usefixtures("allow_loopback", "importer_settings")
def test_a_dropped_download_resumes_and_is_verified(
    trusted: str, library: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    from apps.library import tasks  # noqa: PLC0415

    monkeypatch.setattr(tasks.scan_path, "delay", lambda *_args: None)
    path = "/films/Night%20of%20the%20Living%20Dead%20(1968).mp4"
    Handler.drops.append(path)
    job = link_job(
        library,
        f"{trusted}{path}?token=abc",
        subfolder="Horror",
        checksum=hashlib.sha256(FILM).hexdigest(),
    )
    assert job.url_display == f"{trusted}{path}"
    assert "abc" not in job.url_display
    assert job.file_name == NAME
    assert run(job) == JobStatus.DONE
    placed = Path(library.path, "Horror", NAME)
    assert placed.read_bytes() == FILM
    gets = [(method, wanted) for method, _path, wanted in Handler.seen if method == "GET"]
    assert gets == [("GET", ""), ("GET", f"bytes={len(FILM) // 2}-")]
    record = ImportFile.objects.get(job=job)
    assert (record.storage_key, record.size) == (f"Horror/{NAME}", len(FILM))
    assert not Path(library.path, ".smart-iptv-import").exists()


@pytest.mark.usefixtures("allow_loopback", "importer_settings")
def test_a_wrong_checksum_fails_and_leaves_nothing(trusted: str, library: Library) -> None:
    job = link_job(library, f"{trusted}/films/{NAME}", checksum="0" * 64)
    assert run(job) == JobStatus.FAILED
    job.refresh_from_db()
    assert job.error_code == "checksum_mismatch"
    assert not list(Path(library.path).rglob("*"))


@pytest.mark.usefixtures("allow_loopback", "importer_settings")
@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("/forbidden.mp4", "auth_failed"),
        ("/missing.mp4", "not_found"),
        ("/to-http.mp4", "unsafe_destination"),
    ],
)
def test_refusals_are_named(trusted: str, library: Library, path: str, code: str) -> None:
    job = link_job(library, f"{trusted}{path}")
    assert run(job) == JobStatus.FAILED
    job.refresh_from_db()
    assert job.error_code == code


@pytest.mark.usefixtures("allow_loopback", "importer_settings")
def test_the_servers_file_name_is_used_when_the_link_has_none(
    trusted: str, library: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    from apps.library import tasks  # noqa: PLC0415

    monkeypatch.setattr(tasks.scan_path, "delay", lambda *_args: None)
    job = link_job(library, f"{trusted}/download?id=7")
    assert job.file_name == ""
    assert run(job) == JobStatus.DONE
    assert Path(library.path, "Plan 9 (1957).mp4").read_bytes() == FILM


@pytest.mark.usefixtures("importer_settings")
def test_links_to_our_network_are_refused(
    trusted: str, library: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    for url in ("https://127.0.0.1/x.mp4", "https://169.254.169.254/x.mp4", "https://web/x.mp4"):
        with pytest.raises(services.ImportRefused) as error:
            link_job(library, url)
        assert error.value.code == "unsafe_destination"
    # A name that resolves to loopback is refused when the importer resolves it.
    loopback = [(2, 1, 6, "", ("127.0.0.1", 443))]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: loopback)
    egress.INTERNAL.until = 0.0
    job = link_job(library, "https://files.example.com/x.mp4")
    assert run(job) == JobStatus.FAILED
    job.refresh_from_db()
    assert job.error_code == "unsafe_destination"


@pytest.mark.usefixtures("allow_loopback", "importer_settings")
def test_a_certificate_that_does_not_verify_fails_at_once(
    https: tuple[str, ssl.SSLContext], library: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    base, _context = https
    monkeypatch.setattr(direct, "_verify", None)  # the public CAs only: ours isn't one
    job = link_job(library, f"{base}/films/{NAME}")
    assert run(job) == JobStatus.FAILED
    job.refresh_from_db()
    assert (job.error_code, job.attempts) == ("tls_failed", 1)


def test_links_must_be_https(settings: Any) -> None:
    with pytest.raises(ValueError, match="https"):
        direct.check_link("http://example.com/film.mp4")
    with pytest.raises(ValueError, match="https"):
        direct.check_link("ftp://example.com/film.mp4")
    with pytest.raises(ValueError, match="user name"):
        direct.check_link("https://me:pw@example.com/film.mp4")
    settings.DEBUG = True  # the dev stack's test server only
    direct.check_link("http://example.com/film.mp4")
    assert (
        direct.display("https://me@example.com:8443/a/b.mp4?sig=1#x")
        == "https://example.com:8443/a/b.mp4"
    )
    assert direct.checksum_algorithm("A" * 40) == "sha1"
    assert direct.checksum_algorithm("") is None
    with pytest.raises(ValueError, match="hexadecimal"):
        direct.checksum_algorithm("xyz")
