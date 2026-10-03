"""Media tokens (ADR-0007): the backend's signer and its stream-auth verifier.

This mirrors `streaming/tools/sign_token.py`, the reference the edge's njs verifier
agrees with byte for byte; `streaming/tests/vectors.json` pins all three. A token is
URL-safe ASCII with '.'-separated fields:

    kid.session.title.rendition.exp[.net].sig

`sig` is the unpadded base64url HMAC-SHA256, keyed with the secret named by `kid`,
of the token's ASCII bytes before the last '.'. `title` is the asset's storage key
(its directory under the media root), never a path.

Keys come from the key file the edges load (`conf.media_token_keys_file()`):
`{"current": {"kid", "secret"}, "previous": {...} | null}`. Django signs with
`current`; verification accepts both. The file is re-read when it changes, so a
rotated key file needs no restart. Errors never contain key material, and nothing
here logs a token.
"""

import base64
import binascii
import hashlib
import hmac
import ipaddress
import json
import os
import re
import threading
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from apps.playback import conf

# Verification policy, identical at the edge (token.js) and in the reference.
CLOCK_SKEW_S = 30  # tolerated clock difference between the signer and an edge
MAX_TTL_S = 7 * 24 * 3600  # an exp further ahead is a signer bug (milliseconds, say)
MAX_TOKEN_LEN = 256
MAX_TAIL_SEGMENTS = 8
SESSION_LOG_PREFIX = 16  # session characters that may be logged in place of a token
SECRET_MIN_BYTES = 32
SECRET_MAX_BYTES = 64

# Field grammars, always matched in full.
KID = re.compile(r"[A-Za-z0-9_-]{1,16}")
SESSION = re.compile(r"[0-9a-f]{32}")
TITLE = re.compile(r"[A-Za-z0-9_-]{1,64}")
RENDITION = re.compile(r"[A-Za-z0-9_-]{1,32}")
EXP = re.compile(r"[1-9][0-9]{0,11}")
NET = re.compile(r"[0-9a-f]{6}|[0-9a-f]{16}")
SIG = re.compile(r"[A-Za-z0-9_-]{43}")
SECRET = re.compile(r"[A-Za-z0-9_-]{43,86}")
SEGMENT = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}")
LEAF = re.compile(r".*\.[A-Za-z0-9]{1,8}")
EXTENSION = re.compile(r"[A-Za-z0-9]{1,8}")


class Verdict(StrEnum):
    """Why a token failed, in the edge's words. Success has no verdict."""

    MALFORMED = "malformed"
    BAD_PATH = "bad_path"
    UNKNOWN_KID = "unknown_kid"
    BAD_SIGNATURE = "bad_signature"
    EXPIRED = "expired"
    EXP_TOO_FAR = "exp_too_far"
    IP_MISMATCH = "ip_mismatch"
    SCOPE = "scope"


class TokenError(ValueError):
    """A token failed verification; `reason` is the edge's verdict."""

    def __init__(self, reason: Verdict) -> None:
        super().__init__(reason.value)
        self.reason = reason


class KeySetError(ValueError):
    """The key file is unusable. Messages never contain key material."""


@dataclass(frozen=True, slots=True)
class Key:
    kid: str
    secret: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class KeySet:
    """The signer uses `current`; verifiers accept `current` and `previous` alike."""

    current: Key
    previous: Key | None = None

    def find(self, kid: str) -> Key | None:
        for key in (self.current, self.previous):
            if key is not None and key.kid == kid:
                return key
        return None


@dataclass(frozen=True, slots=True)
class Claims:
    kid: str
    session: str
    title: str
    rendition: str
    exp: int
    net: str | None = None


def b64url(data: bytes) -> str:
    """Unpadded base64url (RFC 4648 §5)."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def decode_secret(text: str) -> bytes:
    """Decode a key secret: canonical unpadded base64url of 32-64 bytes."""
    if not SECRET.fullmatch(text):
        raise KeySetError("secret must be 43-86 unpadded base64url characters")
    try:
        raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError):
        raise KeySetError("secret is not valid base64url") from None
    if b64url(raw) != text:
        raise KeySetError("secret is not canonical base64url")
    if not SECRET_MIN_BYTES <= len(raw) <= SECRET_MAX_BYTES:
        raise KeySetError(f"secret must decode to {SECRET_MIN_BYTES}-{SECRET_MAX_BYTES} bytes")
    return raw


def _parse_key(obj: object, where: str) -> Key:
    if not isinstance(obj, dict) or set(obj) != {"kid", "secret"}:
        raise KeySetError(f"{where} must be an object with exactly 'kid' and 'secret'")
    kid, secret = obj["kid"], obj["secret"]
    if not isinstance(kid, str) or not KID.fullmatch(kid):
        raise KeySetError(f"{where}.kid must match [A-Za-z0-9_-]{{1,16}}")
    if not isinstance(secret, str):
        raise KeySetError(f"{where}.secret must be a string")
    try:
        return Key(kid, decode_secret(secret))
    except KeySetError as exc:
        raise KeySetError(f"{where}.{exc}") from None


def parse_keyset(obj: object) -> KeySet:
    """Validate a decoded key file: {"current": {...}, "previous": {...} | null}."""
    if not isinstance(obj, dict):
        raise KeySetError("key file must hold a JSON object")
    unknown = sorted(str(name) for name in set(obj) - {"current", "previous"})
    if unknown:
        raise KeySetError(f"unknown fields: {', '.join(unknown)}")
    if "current" not in obj:
        raise KeySetError("'current' is required")
    current = _parse_key(obj["current"], "current")
    previous = None if obj.get("previous") is None else _parse_key(obj["previous"], "previous")
    if previous is not None and previous.kid == current.kid:
        raise KeySetError("current and previous must have different kids")
    return KeySet(current, previous)


def load_keyset(path: str | Path) -> KeySet:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise KeySetError(f"cannot read key file: {exc.strerror}") from None
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        # The decoder's message quotes the file, which may include a secret.
        raise KeySetError("key file is not valid JSON") from None
    return parse_keyset(obj)


def client_net(address: str) -> str:
    """The `net` field for a client address.

    IPv4: the /24 as 6 lowercase hex digits (203.0.113.7 -> cb0071).
    IPv6: the /64 as 16 lowercase hex digits (2001:db8:1:2::7 -> 20010db800010002).
    IPv4-mapped IPv6 (::ffff:203.0.113.7) counts as IPv4. Anything that is not a
    plain address, zone ids included, raises ValueError.
    """
    if "%" in address:
        raise ValueError("scoped addresses are not client addresses")
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if isinstance(ip, ipaddress.IPv4Address):
        return ip.packed[:3].hex()
    return ip.packed[:8].hex()


def mac(secret: bytes, payload: str) -> str:
    return b64url(hmac.new(secret, payload.encode("ascii"), hashlib.sha256).digest())


def _require(pattern: re.Pattern[str], value: str, name: str) -> None:
    if not pattern.fullmatch(value):
        msg = f"{name} does not match {pattern.pattern}"
        raise ValueError(msg)


# One keyword parameter per token field reads better at call sites than a builder.
def sign(  # noqa: PLR0913
    keyset: KeySet,
    *,
    session: str,
    title: str,
    rendition: str,
    exp: int,
    net: str | None = None,
    kid: str | None = None,
) -> str:
    """Mint a token with the current key (or the key named by `kid`)."""
    key = keyset.current if kid is None else keyset.find(kid)
    if key is None:
        raise KeySetError("no key with that kid")
    if isinstance(exp, bool) or not isinstance(exp, int):
        raise ValueError("exp must be an int (Unix seconds)")
    fields = [key.kid, session, title, rendition, str(exp)]
    for pattern, value, name in (
        (SESSION, session, "session"),
        (TITLE, title, "title"),
        (RENDITION, rendition, "rendition"),
        (EXP, fields[4], "exp"),
    ):
        _require(pattern, value, name)
    if net is not None:
        _require(NET, net, "net")
        fields.append(net)
    payload = ".".join(fields)
    return f"{payload}.{mac(key.secret, payload)}"


def parse(token: str) -> tuple[Claims, str, str]:
    """Split a token into (claims, signed payload, signature). Grammar only."""
    if not 0 < len(token) <= MAX_TOKEN_LEN:
        raise TokenError(Verdict.MALFORMED)
    parts = token.split(".")
    if len(parts) not in (6, 7):
        raise TokenError(Verdict.MALFORMED)
    kid, session, title, rendition, exp = parts[:5]
    net = parts[5] if len(parts) == 7 else None
    sig = parts[-1]
    if not (
        KID.fullmatch(kid)
        and SESSION.fullmatch(session)
        and TITLE.fullmatch(title)
        and RENDITION.fullmatch(rendition)
        and EXP.fullmatch(exp)
        and (net is None or NET.fullmatch(net))
        and SIG.fullmatch(sig)
    ):
        raise TokenError(Verdict.MALFORMED)
    payload = token[: len(token) - len(sig) - 1]
    return Claims(kid, session, title, rendition, int(exp), net), payload, sig


def valid_tail(tail: str) -> bool:
    """The path after the token: safe segments, ending in a file with an extension."""
    segments = tail.split("/")
    return (
        len(segments) <= MAX_TAIL_SEGMENTS
        and all(SEGMENT.fullmatch(segment) for segment in segments)
        and LEAF.fullmatch(segments[-1]) is not None
    )


def in_scope(rendition: str, tail: str) -> bool:
    """A token for rendition R reaches the file R.<ext> or anything under R/."""
    if "/" not in tail:
        stem, dot, ext = tail.partition(".")
        return stem == rendition and dot == "." and EXTENSION.fullmatch(ext) is not None
    return tail.startswith(rendition + "/")


def verify(
    keyset: KeySet,
    token: str,
    *,
    now: int,
    client_ip: str | None = None,
    tail: str | None = None,
) -> Claims:
    """Check a token in the edge's order; raise TokenError with the edge's verdict.

    `tail` is the request path after the token; when given, it must be well formed
    and inside the token's rendition. A token bound to a network fails without a
    usable `client_ip`.
    """
    claims, payload, sig = parse(token)
    if tail is not None and not valid_tail(tail):
        raise TokenError(Verdict.BAD_PATH)
    key = keyset.find(claims.kid)
    if key is None:
        raise TokenError(Verdict.UNKNOWN_KID)
    if not hmac.compare_digest(mac(key.secret, payload), sig):
        raise TokenError(Verdict.BAD_SIGNATURE)
    if now >= claims.exp + CLOCK_SKEW_S:
        raise TokenError(Verdict.EXPIRED)
    if claims.exp > now + MAX_TTL_S:
        raise TokenError(Verdict.EXP_TOO_FAR)
    if claims.net is not None:
        try:
            actual = None if client_ip is None else client_net(client_ip)
        except ValueError:
            actual = None
        if actual != claims.net:
            raise TokenError(Verdict.IP_MISMATCH)
    if tail is not None and not in_scope(claims.rendition, tail):
        raise TokenError(Verdict.SCOPE)
    return claims


# --- The deployment's key ring ----------------------------------------------------

_ring_lock = threading.Lock()
_ring: tuple[tuple[str, int, int], KeySet] | None = None


def keyring() -> KeySet:
    """The key set from the configured key file, re-read whenever the file changes.

    Raises KeySetError when the file is missing or invalid (the message names the
    problem, never a secret).
    """
    global _ring  # noqa: PLW0603 (process-local cache, replaced atomically)
    path = conf.media_token_keys_file()
    try:
        stat = os.stat(path)
    except OSError as exc:
        raise KeySetError(f"cannot read key file: {exc.strerror}") from None
    signature = (str(path), stat.st_mtime_ns, stat.st_size)
    cached = _ring
    if cached is not None and cached[0] == signature:
        return cached[1]
    with _ring_lock:
        keyset = load_keyset(path)
        _ring = (signature, keyset)
    return keyset


def reset_keyring() -> None:
    """Forget the cached key set (tests, and after replacing the file in place)."""
    global _ring  # noqa: PLW0603
    _ring = None
