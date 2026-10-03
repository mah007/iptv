#!/usr/bin/env python3
"""Reference signer and verifier for Smart IPTV media tokens (ADR-0007).

Standard library only (Python 3.10+). The backend's M7 signer and its stream-auth
check mirror this module, and the edge's verifier (streaming/nginx/njs/token.js)
agrees with it byte for byte: streaming/tests/vectors.json pins all of them.

A token is URL-safe ASCII with '.'-separated fields:

    kid.session.title.rendition.exp[.net].sig

`sig` is the unpadded base64url HMAC-SHA256, keyed with the secret named by `kid`,
of the token's ASCII bytes before the last '.'. ADR-0007 defines every field.

Command line (each subcommand has --help):

    sign_token.py genkeys --kid k1 > keys.json
    sign_token.py rotate --kid k2 keys.json > keys.new.json
    sign_token.py sign --keys keys.json --session <32 hex> --title <asset> \
        --rendition compat --ttl 7200 [--client-ip 203.0.113.7]
    sign_token.py verify --keys keys.json --tail compat.mp4 <token>
    sign_token.py check-keys keys.json
    sign_token.py net 203.0.113.7
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Verification policy, identical at the edge (token.js).
CLOCK_SKEW_S = 30  # tolerated clock difference between the signer and an edge
MAX_TTL_S = 7 * 24 * 3600  # an exp further ahead is a signer bug (milliseconds, say)
MAX_TOKEN_LEN = 256
MAX_TAIL_SEGMENTS = 8
SESSION_LOG_PREFIX = 16  # session characters the edge logs in place of the token
SECRET_MIN_BYTES = 32
SECRET_MAX_BYTES = 64

# Field grammars, always matched in full. No field can hold '.', '/' or anything
# that needs escaping in a URL path, a log line or a Redis key.
KID = re.compile(r"[A-Za-z0-9_-]{1,16}")
SESSION = re.compile(r"[0-9a-f]{32}")
TITLE = re.compile(r"[A-Za-z0-9_-]{1,64}")
RENDITION = re.compile(r"[A-Za-z0-9_-]{1,32}")
EXP = re.compile(r"[1-9][0-9]{0,11}")
NET = re.compile(r"[0-9a-f]{6}|[0-9a-f]{16}")
SIG = re.compile(r"[A-Za-z0-9_-]{43}")
SECRET = re.compile(r"[A-Za-z0-9_-]{43,86}")
# Path after the token: 1-8 segments that cannot be '.', '..' or hidden, and the
# last one names a file with an extension (so a directory is never addressed).
SEGMENT = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}")
LEAF = re.compile(r".*\.[A-Za-z0-9]{1,8}")
EXTENSION = re.compile(r"[A-Za-z0-9]{1,8}")

# Verdicts, identical at the edge. Success has no code.
MALFORMED = "malformed"
BAD_PATH = "bad_path"
UNKNOWN_KID = "unknown_kid"
BAD_SIGNATURE = "bad_signature"
EXPIRED = "expired"
EXP_TOO_FAR = "exp_too_far"
IP_MISMATCH = "ip_mismatch"
SCOPE = "scope"


class TokenError(ValueError):
    """A token failed verification; `reason` is the stable verdict the edge logs."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class KeySetError(ValueError):
    """The key file is unusable. Messages never contain key material."""


@dataclass(frozen=True)
class Key:
    kid: str
    secret: bytes = field(repr=False)


@dataclass(frozen=True)
class KeySet:
    """The signer uses `current`; edges accept `current` and `previous` alike."""

    current: Key
    previous: Key | None = None

    def find(self, kid: str) -> Key | None:
        for key in (self.current, self.previous):
            if key is not None and key.kid == kid:
                return key
        return None


@dataclass(frozen=True)
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


def keyset_json(keyset: KeySet) -> str:
    def entry(key: Key) -> dict[str, str]:
        return {"kid": key.kid, "secret": b64url(key.secret)}

    data: dict[str, object] = {"current": entry(keyset.current)}
    if keyset.previous is not None:
        data["previous"] = entry(keyset.previous)
    return json.dumps(data, indent=2) + "\n"


def generate_key(kid: str) -> Key:
    if not KID.fullmatch(kid):
        raise ValueError("kid must match [A-Za-z0-9_-]{1,16}")
    return Key(kid, secrets.token_bytes(SECRET_MIN_BYTES))


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
        raise ValueError(f"{name} does not match {pattern.pattern}")


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
        raise TokenError(MALFORMED)
    parts = token.split(".")
    if len(parts) not in (6, 7):
        raise TokenError(MALFORMED)
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
        raise TokenError(MALFORMED)
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
        raise TokenError(BAD_PATH)
    key = keyset.find(claims.kid)
    if key is None:
        raise TokenError(UNKNOWN_KID)
    if not hmac.compare_digest(mac(key.secret, payload), sig):
        raise TokenError(BAD_SIGNATURE)
    if now >= claims.exp + CLOCK_SKEW_S:
        raise TokenError(EXPIRED)
    if claims.exp > now + MAX_TTL_S:
        raise TokenError(EXP_TOO_FAR)
    if claims.net is not None:
        try:
            actual = None if client_ip is None else client_net(client_ip)
        except ValueError:
            actual = None
        if actual != claims.net:
            raise TokenError(IP_MISMATCH)
    if tail is not None and not in_scope(claims.rendition, tail):
        raise TokenError(SCOPE)
    return claims


def _out(text: str) -> None:
    sys.stdout.write(text + "\n")


def _err(text: str) -> None:
    sys.stderr.write(text + "\n")


def _cmd_genkeys(args: argparse.Namespace) -> int:
    sys.stdout.write(keyset_json(KeySet(generate_key(args.kid))))
    return 0


def rotate(keyset: KeySet, kid: str) -> KeySet:
    """A new random current key; the old current key becomes previous."""
    if kid in {key.kid for key in (keyset.current, keyset.previous) if key is not None}:
        raise KeySetError("the new kid must differ from the current and previous kids")
    return KeySet(generate_key(kid), keyset.current)


def _cmd_rotate(args: argparse.Namespace) -> int:
    sys.stdout.write(keyset_json(rotate(load_keyset(args.file), args.kid)))
    return 0


def _cmd_sign(args: argparse.Namespace) -> int:
    keyset = load_keyset(args.keys)
    exp = args.exp if args.exp is not None else int(time.time()) + args.ttl
    net = None if args.client_ip is None else client_net(args.client_ip)
    _out(
        sign(
            keyset,
            session=args.session,
            title=args.title,
            rendition=args.rendition,
            exp=exp,
            net=net,
            kid=args.kid,
        )
    )
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    keyset = load_keyset(args.keys)
    from_stdin = args.value == "-"
    token = sys.stdin.readline().strip() if from_stdin else args.value
    now = args.now if args.now is not None else int(time.time())
    try:
        claims = verify(keyset, token, now=now, client_ip=args.client_ip, tail=args.tail)
    except TokenError as exc:
        _err(f"invalid: {exc.reason}")
        return 1
    _out(json.dumps(asdict(claims), sort_keys=True))
    return 0


def _cmd_check_keys(args: argparse.Namespace) -> int:
    keyset = load_keyset(args.file)
    previous = keyset.previous.kid if keyset.previous else "none"
    _out(f"ok: current={keyset.current.kid} previous={previous}")
    return 0


def _cmd_net(args: argparse.Namespace) -> int:
    _out(client_net(args.address))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)

    genkeys = commands.add_parser("genkeys", help="print a new key file with one random key")
    genkeys.add_argument("--kid", default="k1")
    genkeys.set_defaults(func=_cmd_genkeys)

    rotate_cmd = commands.add_parser(
        "rotate", help="print the key file with a new current key (old current -> previous)"
    )
    rotate_cmd.add_argument("--kid", required=True, help="kid of the new current key")
    rotate_cmd.add_argument("file")
    rotate_cmd.set_defaults(func=_cmd_rotate)

    sign_cmd = commands.add_parser("sign", help="mint a token")
    sign_cmd.add_argument("--keys", required=True, help="key file (JSON)")
    sign_cmd.add_argument("--kid", help="sign with this kid instead of the current key")
    sign_cmd.add_argument("--session", required=True)
    sign_cmd.add_argument("--title", required=True)
    sign_cmd.add_argument("--rendition", required=True)
    lifetime = sign_cmd.add_mutually_exclusive_group(required=True)
    lifetime.add_argument("--exp", type=int, help="expiry, Unix seconds")
    lifetime.add_argument("--ttl", type=int, help="lifetime from now, seconds")
    sign_cmd.add_argument("--client-ip", help="bind the token to this client's network")
    sign_cmd.set_defaults(func=_cmd_sign)

    verify_cmd = commands.add_parser("verify", help="check a token like the edge does")
    verify_cmd.add_argument("--keys", required=True, help="key file (JSON)")
    verify_cmd.add_argument("--tail", help="request path after the token, e.g. compat.mp4")
    verify_cmd.add_argument("--client-ip")
    verify_cmd.add_argument("--now", type=int, help="Unix seconds (default: now)")
    verify_cmd.add_argument("value", metavar="TOKEN", help="the token, or - to read it from stdin")
    verify_cmd.set_defaults(func=_cmd_verify)

    check = commands.add_parser("check-keys", help="validate a key file")
    check.add_argument("file")
    check.set_defaults(func=_cmd_check_keys)

    net = commands.add_parser("net", help="print the network binding for an address")
    net.add_argument("address")
    net.set_defaults(func=_cmd_net)

    args = parser.parse_args(argv)
    try:
        result: int = args.func(args)
    except (KeySetError, ValueError) as exc:
        _err(f"error: {exc}")
        return 1
    return result


if __name__ == "__main__":
    sys.exit(main())
