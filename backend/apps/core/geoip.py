"""Country and network (ASN) of client addresses from DB-IP Lite (SPEC §11.1; ADR-0021).

DB-IP's free "IP to Country Lite" and "IP to ASN Lite" databases (CC BY 4.0; both apps'
About pages credit "IP Geolocation by DB-IP") are MaxMind DB files, read with
`maxminddb` (Apache-2.0). They live in the `geoip-data` volume: written by the worker
(`update`, monthly and on first start), read by web for playback's country rules and by
the anti-sharing score.

- `lookup(ip)` never raises and never touches the network: a missing database, a
  private or loopback address, or an address the database doesn't know all give an
  answer with `country=None` (and `local=True` for the LAN).
- Readers are memory-mapped and reopened when the file changes (checked at most every
  `RELOAD_CHECK_S`), so a monthly update needs no restart.
- `update()` downloads only from DB-IP's site, refuses oversized files, validates the
  result (type, build date, sample lookups) and swaps it in atomically.
"""

import gzip
import hashlib
import ipaddress
import json
import logging
import os
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, Literal

import httpx
import maxminddb
from django.conf import settings

from apps.core.services import get_setting

logger = logging.getLogger(__name__)

type Kind = Literal["country", "asn"]

KINDS: Final[tuple[Kind, ...]] = ("country", "asn")
FILES: Final[dict[Kind, str]] = {"country": "dbip-country-lite.mmdb", "asn": "dbip-asn-lite.mmdb"}
META_FILE: Final = "meta.json"
DOWNLOAD_BASE: Final = "https://download.db-ip.com/free/"
DOWNLOAD_HOST: Final = "download.db-ip.com"
#: The compressed downloads are about 8 MB each today; the caps leave room to grow.
MAX_DOWNLOAD_BYTES: Final = 96 * 1024 * 1024
MAX_DATABASE_BYTES: Final = 384 * 1024 * 1024
#: A database older than this is too stale to install (DB-IP publishes monthly).
MAX_BUILD_AGE: Final = timedelta(days=120)
RELOAD_CHECK_S: Final = 60.0
ATTRIBUTION_URL: Final = "https://db-ip.com"


@dataclass(frozen=True, slots=True)
class Geo:
    """What the databases say about one address."""

    country: str | None = None
    asn: int | None = None
    asn_org: str = ""
    #: A private, loopback or link-local address: the operator's own network.
    local: bool = False


@dataclass
class _Reader:
    path: Path
    reader: Any = None
    mtime: float = -1.0
    checked: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def get(self) -> Any:
        now = time.monotonic()
        if now - self.checked < RELOAD_CHECK_S and self.reader is not None:
            return self.reader
        with self.lock:
            self.checked = now
            try:
                mtime = self.path.stat().st_mtime
            except OSError:
                self._close()
                return None
            if mtime != self.mtime or self.reader is None:
                self._close()
                try:
                    self.reader = maxminddb.open_database(str(self.path), maxminddb.MODE_MMAP)
                    self.mtime = mtime
                except (OSError, maxminddb.InvalidDatabaseError, ValueError):
                    logger.warning("geoip: %s could not be opened", self.path.name)
                    self.reader = None
            return self.reader

    def _close(self) -> None:
        if self.reader is not None:
            try:
                self.reader.close()
            except Exception:
                logger.debug("geoip: closing a reader failed", exc_info=True)
        self.reader = None
        self.mtime = -1.0


_readers: dict[str, _Reader] = {}
_readers_lock = threading.Lock()


def directory() -> Path:
    return Path(str(getattr(settings, "GEOIP_DIR", "/geoip")))


def _reader(kind: Kind) -> Any:
    path = directory() / FILES[kind]
    key = str(path)
    with _readers_lock:
        holder = _readers.get(key)
        if holder is None:
            holder = _readers[key] = _Reader(path)
    return holder.get()


def reset_readers() -> None:
    """Forget open readers (tests, and after an update in the same process)."""
    with _readers_lock:
        for holder in _readers.values():
            holder._close()
        _readers.clear()


def enabled() -> bool:
    return bool(get_setting("geoip.enabled"))


def _address(ip: str | None) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    if not ip:
        return None
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def is_local(ip: str | None) -> bool:
    address = _address(ip)
    return address is not None and (
        address.is_private or address.is_loopback or address.is_link_local
    )


def lookup(ip: str | None) -> Geo:
    """The country and ASN of `ip`; never raises."""
    address = _address(ip)
    if address is None:
        return Geo()
    if address.is_private or address.is_loopback or address.is_link_local:
        return Geo(local=True)
    if address.is_multicast or address.is_reserved or address.is_unspecified:
        return Geo()
    try:
        if not enabled():
            return Geo()
        country: str | None = None
        record = _get(_reader("country"), str(address))
        if isinstance(record, dict):
            code = (record.get("country") or {}).get("iso_code")
            country = code.upper() if isinstance(code, str) and len(code) == 2 else None
        asn: int | None = None
        org = ""
        record = _get(_reader("asn"), str(address))
        if isinstance(record, dict):
            number = record.get("autonomous_system_number")
            asn = number if isinstance(number, int) and number > 0 else None
            org = str(record.get("autonomous_system_organization") or "")[:120]
        return Geo(country=country, asn=asn, asn_org=org)
    except Exception:
        logger.warning("geoip lookup failed", exc_info=True)
        return Geo()


def _get(reader: Any, ip: str) -> Any:
    if reader is None:
        return None
    try:
        return reader.get(ip)
    except (ValueError, maxminddb.InvalidDatabaseError):
        return None


def available(kind: Kind = "country") -> bool:
    return (directory() / FILES[kind]).is_file()


# --- Status (admin Integrations card) ---------------------------------------------------------


def status() -> dict[str, Any]:
    """What is installed: per database its build date and size, and the last update."""
    meta = _read_meta()
    databases = {}
    for kind in KINDS:
        path = directory() / FILES[kind]
        entry: dict[str, Any] = {"installed": path.is_file(), "built_at": None, "size": 0}
        if path.is_file():
            entry["size"] = path.stat().st_size
            reader = _reader(kind)
            epoch = getattr(getattr(reader, "metadata", lambda: None)(), "build_epoch", None)
            if isinstance(epoch, int):
                entry["built_at"] = datetime.fromtimestamp(epoch, tz=UTC).isoformat()
        entry["updated_at"] = (meta.get(kind) or {}).get("updated_at")
        databases[kind] = entry
    return {"enabled": enabled(), "databases": databases, "attribution": ATTRIBUTION_URL}


def _read_meta() -> dict[str, Any]:
    try:
        data = json.loads((directory() / META_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# --- Update (worker) --------------------------------------------------------------------------


class UpdateError(Exception):
    """A download or a validation failed; the installed database stays."""


@dataclass(frozen=True, slots=True)
class UpdateResult:
    installed: tuple[str, ...] = ()
    current: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


def _months(now: datetime) -> list[str]:
    """This month's release, then last month's (early in a month it may not be out)."""
    first = now.replace(day=1)
    previous = first - timedelta(days=1)
    return [first.strftime("%Y-%m"), previous.strftime("%Y-%m")]


def _installed_month(kind: Kind) -> str | None:
    reader = _reader(kind)
    if reader is None:
        return None
    epoch = getattr(reader.metadata(), "build_epoch", None)
    return (
        datetime.fromtimestamp(epoch, tz=UTC).strftime("%Y-%m") if isinstance(epoch, int) else None
    )


def url_for(kind: Kind, month: str) -> str:
    return f"{DOWNLOAD_BASE}dbip-{kind}-lite-{month}.mmdb.gz"


def _download(client: httpx.Client, url: str, target: Path) -> None:
    with client.stream("GET", url) as response:
        if response.status_code == 404:
            raise FileNotFoundError(url)
        if response.status_code != 200:
            msg = f"HTTP {response.status_code}"
            raise UpdateError(msg)
        size = 0
        with target.open("wb") as handle:
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_DOWNLOAD_BYTES:
                    msg = "the download is larger than expected"
                    raise UpdateError(msg)
                handle.write(chunk)


def _gunzip(source: Path, target: Path) -> None:
    size = 0
    try:
        with gzip.open(source, "rb") as compressed, target.open("wb") as plain:
            while chunk := compressed.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_DATABASE_BYTES:
                    msg = "the database is larger than expected"
                    raise UpdateError(msg)
                plain.write(chunk)
    except (OSError, EOFError, gzip.BadGzipFile) as exc:
        msg = "the download is not a gzip file"
        raise UpdateError(msg) from exc


_SAMPLES: Final[dict[Kind, str]] = {"country": "8.8.8.8", "asn": "8.8.8.8"}


def validate(kind: Kind, path: Path, *, now: datetime | None = None) -> int:
    """Check a downloaded database before it replaces the installed one; its build time."""
    try:
        reader = maxminddb.open_database(str(path), maxminddb.MODE_FILE)
    except (OSError, maxminddb.InvalidDatabaseError, ValueError) as exc:
        msg = "not a MaxMind DB file"
        raise UpdateError(msg) from exc
    try:
        metadata = reader.metadata()
        database_type = str(metadata.database_type).lower()
        if kind not in database_type:
            msg = f"unexpected database type {metadata.database_type!r}"
            raise UpdateError(msg)
        built = datetime.fromtimestamp(int(metadata.build_epoch), tz=UTC)
        moment = now or datetime.now(tz=UTC)
        if built > moment + timedelta(days=2) or moment - built > MAX_BUILD_AGE:
            msg = "the database's build date is implausible"
            raise UpdateError(msg)
        record = reader.get(_SAMPLES[kind])
        if kind == "country":
            ok = isinstance(record, dict) and isinstance(
                (record.get("country") or {}).get("iso_code"), str
            )
        else:
            ok = isinstance(record, dict) and isinstance(
                record.get("autonomous_system_number"), int
            )
        if not ok:
            msg = "sample lookups found nothing"
            raise UpdateError(msg)
        return int(metadata.build_epoch)
    finally:
        reader.close()


def _redirect_guard(request: httpx.Request) -> None:
    if request.url.host != DOWNLOAD_HOST or request.url.scheme != "https":
        msg = "the download was redirected away from DB-IP"
        raise UpdateError(msg)


def update(
    *,
    force: bool = False,
    transport: httpx.BaseTransport | None = None,
    now: datetime | None = None,
) -> UpdateResult:
    """Install this month's (else last month's) databases when they are newer."""
    folder = directory()
    if not folder.is_dir():
        msg = f"the GeoIP folder {folder} does not exist"
        raise UpdateError(msg)
    moment = now or datetime.now(tz=UTC)
    installed: list[str] = []
    current: list[str] = []
    failed: list[str] = []
    errors: list[str] = []
    meta = _read_meta()
    with httpx.Client(
        timeout=httpx.Timeout(60.0, connect=10.0),
        follow_redirects=True,
        max_redirects=3,
        transport=transport,
        event_hooks={"request": [_redirect_guard]},
        headers={"User-Agent": "smart-iptv-geoip"},
    ) as client:
        for kind in KINDS:
            have = _installed_month(kind)
            done = False
            for month in _months(moment):
                if not force and have is not None and have >= month:
                    current.append(kind)
                    done = True
                    break
                try:
                    epoch, digest = _fetch(client, kind, month, folder, now=moment)
                except FileNotFoundError:
                    continue
                except (UpdateError, httpx.HTTPError, OSError) as exc:
                    failed.append(kind)
                    errors.append(f"{kind}: {exc.__class__.__name__}: {str(exc)[:200]}")
                    done = True
                    break
                installed.append(kind)
                meta[kind] = {
                    "source": url_for(kind, month),
                    "sha256": digest,
                    "built_at": datetime.fromtimestamp(epoch, tz=UTC).isoformat(),
                    "updated_at": moment.isoformat(),
                }
                done = True
                break
            if not done:
                failed.append(kind)
                errors.append(f"{kind}: no release for {', '.join(_months(moment))}")
    if installed:
        _write_meta(folder, meta)
        reset_readers()
    return UpdateResult(tuple(installed), tuple(current), tuple(failed), tuple(errors))


def _fetch(
    client: httpx.Client, kind: Kind, month: str, folder: Path, *, now: datetime
) -> tuple[int, str]:
    work = Path(tempfile.mkdtemp(prefix=".geoip-", dir=folder))
    try:
        compressed = work / "download.gz"
        plain = work / FILES[kind]
        _download(client, url_for(kind, month), compressed)
        _gunzip(compressed, plain)
        epoch = validate(kind, plain, now=now)
        digest = hashlib.sha256(plain.read_bytes()).hexdigest()
        os.chmod(plain, 0o644)
        os.replace(plain, folder / FILES[kind])
        logger.info("geoip.installed", extra={"kind": kind, "month": month})
        return epoch, digest
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _write_meta(folder: Path, meta: dict[str, Any]) -> None:
    temporary = folder / f".{META_FILE}.tmp"
    temporary.write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")
    os.chmod(temporary, 0o644)
    temporary.replace(folder / META_FILE)
