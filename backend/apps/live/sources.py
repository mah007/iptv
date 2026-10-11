"""Live channel source URLs: validation, encryption, display and redaction (ADR-0017).

A source URL can carry credentials anywhere (userinfo, path, query), so:
- it is stored encrypted (`apps.accounts.crypto`, Fernet) and only decrypted by the
  packager, the source test and the integrations that wrote it;
- admins see `describe()`: scheme, host and port, never the rest;
- ffmpeg output is passed through a `Redactor` built from the exact URL before it
  is logged or stored, on top of the generic `redact_text`.

Only owned or licensed feeds belong here (SPEC §1.1): the admin enters channels one
by one or syncs them from their own ErsatzTV or MediaMTX. There is no playlist import.
"""

import re
from dataclasses import dataclass
from urllib.parse import SplitResult, unquote, urlsplit

from django.core.exceptions import ValidationError

from apps.accounts import crypto
from apps.core.redaction import redact_text

ALLOWED_SCHEMES = ("http", "https", "rtmp", "rtmps", "rtsp", "rtsps", "srt")
MAX_URL_LENGTH = 2048
_FORBIDDEN = re.compile(r"[\s\x00-\x1f\x7f]")
#: Protocols ffmpeg may open while reading a source: never file, pipe, concat, data or
#: subfile, whatever an HLS source's playlist names.
PROTOCOL_WHITELIST = "http,https,tcp,tls,crypto,rtmp,rtmps,rtmpt,rtmpts,rtp,srtp,udp,srt"
SOURCE_MASK = "<source>"


def validate_source_url(value: str) -> str:
    """The URL, stripped, or ValidationError (code `invalid_source_url`)."""
    url = (value or "").strip()
    error = ValidationError(
        "Enter an http(s), rtmp(s), rtsp(s) or srt URL.", code="invalid_source_url"
    )
    if not url or len(url) > MAX_URL_LENGTH or _FORBIDDEN.search(url):
        raise error
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise error from None
    if parts.scheme.lower() not in ALLOWED_SCHEMES or not parts.hostname:
        raise error
    if port is not None and not 0 < port < 65536:
        raise error
    return url


def validate_http_url(value: str) -> str:
    """An http(s) URL (EPG feeds, integrations), or ValidationError."""
    url = validate_source_url(value)
    if urlsplit(url).scheme.lower() not in ("http", "https"):
        raise ValidationError("Enter an http or https URL.", code="invalid_url")
    return url


@dataclass(frozen=True, slots=True)
class SourceInfo:
    """What admins may see of a source URL."""

    scheme: str
    host: str
    port: int | None

    def as_dict(self) -> dict[str, object]:
        return {"scheme": self.scheme, "host": self.host, "port": self.port}


def describe(url: str) -> SourceInfo:
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        port = None
    return SourceInfo(parts.scheme.lower(), parts.hostname or "", port)


def encrypt(url: str) -> str:
    return crypto.encrypt(url)


def decrypt(token: str) -> str:
    return crypto.decrypt(token)


def describe_encrypted(token: str) -> SourceInfo | None:
    if not token:
        return None
    try:
        return describe(decrypt(token))
    except crypto.DecryptionError:
        return None


class Redactor:
    """Masks every form of one secret URL (whole, decoded, its userinfo, path and
    query) in text, then applies the generic redaction."""

    def __init__(self, url: str) -> None:
        parts: SplitResult = urlsplit(url)
        candidates = {url, unquote(url)}
        for piece in (parts.username, parts.password, parts.path, parts.query):
            if piece and len(piece) >= 4 and piece not in ("/", "/live"):
                candidates.add(piece)
                candidates.add(unquote(piece))
        self._secrets = sorted((item for item in candidates if item), key=len, reverse=True)

    def __call__(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, SOURCE_MASK)
        return redact_text(text)


def input_args(url: str, *, rw_timeout_s: int = 15) -> list[str]:
    """ffmpeg options for reading `url` (they go before `-i`)."""
    scheme = urlsplit(url).scheme.lower()
    timeout_us = str(rw_timeout_s * 1_000_000)
    args = ["-protocol_whitelist", PROTOCOL_WHITELIST]
    if scheme in ("http", "https"):
        args += [
            "-rw_timeout", timeout_us,
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_on_network_error", "1",
            "-reconnect_delay_max", "10",
        ]  # fmt: skip
    elif scheme in ("rtsp", "rtsps"):
        args += ["-rtsp_transport", "tcp", "-timeout", timeout_us]
    elif scheme in ("rtmp", "rtmps"):
        args += ["-rw_timeout", timeout_us]
    return args
