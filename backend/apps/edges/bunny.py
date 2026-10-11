"""Bunny CDN token authentication (advanced, HMAC-SHA256), written from Bunny's
documentation (ADR-0022; docs/runbooks/cdn-bunny.md). Verified against vectors produced
from the documented algorithm only: no live pull zone was available.

    token = "HS256-" [+ "1-" when an address is signed]
            + base64url(HMAC-SHA256(key, signature_path + expires + address + signing_data))

- `signature_path` is `token_path` when set (a directory prefix, so one token covers an
  HLS presentation's playlists and segments), else the file's path;
- `address` is the viewer's address as packed bytes, an IPv6 one cut to its /64;
- `signing_data` is the other parameters sorted by name as `name=value` with raw values
  (here only `token_path`); in the URL the values are percent-encoded.

URL forms (the token travels in the path, so relative HLS URIs inherit it; Bunny strips
it before its cache key and its origin request):

    directory  https://cdn.example.com/bcdn_token=<t>&token_path=%2F<title>%2Fhls%2F&expires=<e>/<title>/hls/master.m3u8
    file       https://cdn.example.com/bcdn_token=<t>&expires=<e>/<title>/compat.mp4
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
from urllib.parse import quote


def _address(ip: str | None) -> bytes:
    if not ip:
        return b""
    address = ipaddress.ip_address(ip)
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return address.ipv4_mapped.packed
        return address.packed[:8] + b"\0" * 8
    return address.packed


def token(key: str, *, signature_path: str, expires: int, params: dict[str, str],
          ip: str | None = None) -> str:  # fmt: skip
    signing = "&".join(f"{name}={params[name]}" for name in sorted(params))
    message = signature_path.encode() + str(expires).encode() + _address(ip) + signing.encode()
    digest = hmac.new(key.encode(), message, hashlib.sha256).digest()
    encoded = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return "HS256-" + ("1-" if ip else "") + encoded


def signed_url(  # noqa: PLR0913 (keyword-only facts of one URL)
    base: str,
    path: str,
    *,
    key: str,
    expires: int,
    directory: str | None = None,
    ip: str | None = None,
) -> str:
    """A Bunny URL for `path` (starting with /): scoped to `directory` (a prefix such as
    /<title>/hls/) when given, else to the file itself."""
    params = {"token_path": directory} if directory else {}
    value = token(
        key,
        signature_path=directory or path,
        expires=expires,
        params=params,
        ip=ip,
    )
    tail = "".join(f"&{name}={quote(params[name], safe='')}" for name in sorted(params))
    return f"{base.rstrip('/')}/bcdn_token={value}{tail}&expires={expires}{path}"
