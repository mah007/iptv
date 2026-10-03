#!/usr/bin/env python3
"""Build streaming/tests/vectors.json, the media-token vectors of ADR-0007.

The vectors pin the token format for every implementation: the reference signer
(streaming/tools/sign_token.py), the edge (streaming/nginx/njs/token.js) and the
backend's M7 signer and stream-auth check. The keys are public test data.

Usage: make_vectors.py [--check]   (--check fails if vectors.json is stale)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import sign_token as st

VECTORS = Path(__file__).with_name("vectors.json")

NOW = 1767225000  # 2025-12-31T23:50:00Z
EXP = 1767225600  # 2026-01-01T00:00:00Z
CURRENT = st.Key("k1", bytes(range(32)))
PREVIOUS = st.Key("k0", bytes(range(32, 64)))
KEYS = st.KeySet(CURRENT, PREVIOUS)
SESSION = "0192f3a4b5c67d8e9f00112233445566"
SESSION_HLS = "0192f3a4b5c67d8e9f00112233445577"
TITLE = "0192f3a4b5c67d8e9f001122334455aa"

# Addresses whose `net` binding both implementations must agree on (None = rejected).
CLIENT_NETS: list[tuple[str, str | None]] = [
    ("203.0.113.7", "cb0071"),
    ("0.0.0.0", "000000"),
    ("255.255.255.255", "ffffff"),
    ("10.1.2.3", "0a0102"),
    ("2001:db8:1:2::7", "20010db800010002"),
    ("2001:DB8:1:2::7", "20010db800010002"),
    ("2001:0db8:0001:0002:0000:0000:0000:0007", "20010db800010002"),
    ("2001:db8:1:2:3:4:5:6", "20010db800010002"),
    ("::", "0000000000000000"),
    ("::1", "0000000000000000"),
    ("1::", "0001000000000000"),
    ("1:2:3:4:5:6:7::", "0001000200030004"),
    ("::2:3:4:5:6:7:8", "0000000200030004"),
    ("fe80::1", "fe80000000000000"),
    ("::ffff:203.0.113.9", "cb0071"),
    ("::FFFF:cb00:7109", "cb0071"),
    ("0:0:0:0:0:ffff:203.0.113.9", "cb0071"),
    ("::203.0.113.9", "0000000000000000"),
    ("64:ff9b::203.0.113.9", "0064ff9b00000000"),
    ("1:2:3:4:5:6:1.2.3.4", "0001000200030004"),
    ("", None),
    ("203.0.113", None),
    ("203.0.113.7.1", None),
    ("203.0.113.256", None),
    ("203.0.113.07", None),
    ("203.0.113.7 ", None),
    (" 203.0.113.7", None),
    ("::ffff:203.0.113.07", None),
    ("::ffff:203.0.113", None),
    ("1:2:3:4:5:6:7:8:9", None),
    ("1:2:3:4:5:6:7:8::", None),
    ("1:2:3:4:5:6:7", None),
    ("1::2::3", None),
    (":1:2:3:4:5:6:7", None),
    ("1:2:3:4:5:6:7:", None),
    (":::", None),
    ("12345::", None),
    ("g::1", None),
    ("fe80::1%eth0", None),
    ("1:2:3:4:5:6:7:1.2.3.4", None),
    ("::1.2.3.4:5", None),
    ("localhost", None),
]


def _signed(**fields: Any) -> str:
    return st.sign(KEYS, **fields)


def _with_last_char(token: str, char: str) -> str:
    return token[:-1] + char


def _same_bytes_other_char(token: str) -> str:
    """Re-encode the signature's last character so it decodes to the same bytes.

    32 bytes take 43 base64url characters; the last one carries 4 data bits and 2
    zero bits, so flipping its lowest bit leaves the decoded bytes unchanged.
    """
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    return _with_last_char(token, alphabet[alphabet.index(token[-1]) ^ 1])


def _flip(text: str, index: int) -> str:
    return text[:index] + ("B" if text[index] == "A" else "A") + text[index + 1 :]


def _case(name: str, token: str, tail: str, client_ip: str, verdict: str) -> dict[str, Any]:
    case: dict[str, Any] = {
        "name": name,
        "token": token,
        "tail": tail,
        "client_ip": client_ip,
        "verdict": verdict,
    }
    if verdict == "ok":
        claims, _, _ = st.parse(token)
        case["claims"] = {
            "kid": claims.kid,
            "session": claims.session,
            "title": claims.title,
            "rendition": claims.rendition,
            "exp": claims.exp,
            "net": claims.net,
        }
    return case


def build() -> dict[str, Any]:
    compat = _signed(session=SESSION, title=TITLE, rendition="compat", exp=EXP)
    bound_v4 = _signed(
        session=SESSION_HLS, title=TITLE, rendition="hls", exp=EXP, net=st.client_net("203.0.113.7")
    )
    bound_v6 = _signed(
        session=SESSION_HLS,
        title=TITLE,
        rendition="hls",
        exp=EXP,
        net=st.client_net("2001:db8:1:2::7"),
        kid="k0",
    )
    bound_compat = _signed(session=SESSION, title=TITLE, rendition="compat", exp=EXP, net="cb0071")
    in_skew = _signed(session=SESSION, title=TITLE, rendition="compat", exp=NOW - 29)
    at_skew = _signed(session=SESSION, title=TITLE, rendition="compat", exp=NOW - 30)
    too_far = _signed(session=SESSION, title=TITLE, rendition="compat", exp=NOW + st.MAX_TTL_S + 1)
    longest = _signed(
        session=SESSION,
        title="t" * 64,
        rendition="r" * 32,
        exp=EXP,
        net="20010db800010002",
    )
    unknown_payload = f"k9.{SESSION}.{TITLE}.compat.{EXP}"
    unknown_kid = f"{unknown_payload}.{st.mac(bytes(32), unknown_payload)}"
    compat_parts = compat.split(".")
    v4_parts = bound_v4.split(".")

    def swap(parts: list[str], index: int, value: str) -> str:
        changed = list(parts)
        changed[index] = value
        return ".".join(changed)

    cases = [
        _case("progressive, unbound", compat, "compat.mp4", "198.51.100.23", "ok"),
        _case(
            "hls segment, bound to an IPv4 /24",
            bound_v4,
            "hls/v720/seg_00001.m4s",
            "203.0.113.200",
            "ok",
        ),
        _case(
            "previous kid, bound to an IPv6 /64",
            bound_v6,
            "hls/master.m3u8",
            "2001:db8:1:2:aaaa::1",
            "ok",
        ),
        _case(
            "IPv4-mapped client counts as IPv4",
            bound_compat,
            "compat.mp4",
            "::ffff:203.0.113.9",
            "ok",
        ),
        _case("expiry within the clock skew", in_skew, "compat.mp4", "198.51.100.23", "ok"),
        _case("longest fields", longest, "r" * 32 + "/x.m3u8", "2001:db8:1:2::1", "ok"),
        _case(
            "tampered signature",
            _flip(compat, len(compat) - 10),
            "compat.mp4",
            "198.51.100.23",
            "bad_signature",
        ),
        _case(
            "signature in a non-canonical encoding of the same bytes",
            _same_bytes_other_char(compat),
            "compat.mp4",
            "198.51.100.23",
            "bad_signature",
        ),
        _case(
            "tampered title",
            swap(compat_parts, 2, TITLE[:-1] + "b"),
            "compat.mp4",
            "198.51.100.23",
            "bad_signature",
        ),
        _case(
            "tampered rendition",
            swap(compat_parts, 3, "uhd"),
            "uhd.mp4",
            "198.51.100.23",
            "bad_signature",
        ),
        _case(
            "tampered exp",
            swap(compat_parts, 4, str(EXP + 3600)),
            "compat.mp4",
            "198.51.100.23",
            "bad_signature",
        ),
        _case(
            "network binding removed",
            ".".join(v4_parts[:5] + v4_parts[6:]),
            "hls/master.m3u8",
            "198.51.100.23",
            "bad_signature",
        ),
        _case(
            "kid swapped to the previous key",
            swap(compat_parts, 0, "k0"),
            "compat.mp4",
            "198.51.100.23",
            "bad_signature",
        ),
        _case("unknown kid", unknown_kid, "compat.mp4", "198.51.100.23", "unknown_kid"),
        _case(
            "expired at the clock-skew boundary", at_skew, "compat.mp4", "198.51.100.23", "expired"
        ),
        _case(
            "exp beyond the maximum lifetime", too_far, "compat.mp4", "198.51.100.23", "exp_too_far"
        ),
        _case(
            "IPv4 client from another /24",
            bound_v4,
            "hls/master.m3u8",
            "203.0.114.7",
            "ip_mismatch",
        ),
        _case(
            "IPv6 client from another /64",
            bound_v6,
            "hls/master.m3u8",
            "2001:db8:1:3::7",
            "ip_mismatch",
        ),
        _case(
            "IPv4 client for an IPv6 binding",
            bound_v6,
            "hls/master.m3u8",
            "203.0.113.7",
            "ip_mismatch",
        ),
        _case("unparseable client address", bound_v4, "hls/master.m3u8", "unknown", "ip_mismatch"),
        _case("file outside the rendition", compat, "hls/master.m3u8", "198.51.100.23", "scope"),
        _case("rendition name as a prefix only", compat, "compatx.mp4", "198.51.100.23", "scope"),
        _case(
            "directory name as a prefix only", bound_v4, "hlsx/master.m3u8", "203.0.113.7", "scope"
        ),
        _case(
            "rendition file with two extensions", compat, "compat.v2.mp4", "198.51.100.23", "scope"
        ),
        _case("dot-dot segment", compat, "hls/../compat.mp4", "198.51.100.23", "bad_path"),
        _case("hidden file", bound_v4, "hls/.index.m3u8", "203.0.113.7", "bad_path"),
        _case("directory, not a file", bound_v4, "hls/v720", "203.0.113.7", "bad_path"),
        _case("empty segment", bound_v4, "hls//master.m3u8", "203.0.113.7", "bad_path"),
        _case("nine segments", bound_v4, "hls/" + "a/" * 7 + "x.m4s", "203.0.113.7", "bad_path"),
        _case(
            "five fields",
            ".".join(compat_parts[:4] + compat_parts[5:]),
            "compat.mp4",
            "198.51.100.23",
            "malformed",
        ),
        _case(
            "eight fields",
            ".".join([*v4_parts[:6], "x", v4_parts[6]]),
            "hls/master.m3u8",
            "203.0.113.7",
            "malformed",
        ),
        _case(
            "uppercase session",
            swap(compat_parts, 1, SESSION.upper()),
            "compat.mp4",
            "198.51.100.23",
            "malformed",
        ),
        _case(
            "exp with a leading zero",
            swap(compat_parts, 4, "0" + str(EXP)),
            "compat.mp4",
            "198.51.100.23",
            "malformed",
        ),
        _case("padded signature", compat + "=", "compat.mp4", "198.51.100.23", "malformed"),
        _case("empty title", swap(compat_parts, 2, ""), "compat.mp4", "198.51.100.23", "malformed"),
        _case(
            "five-digit network",
            swap(v4_parts, 5, "cb007"),
            "hls/master.m3u8",
            "203.0.113.7",
            "malformed",
        ),
        _case(
            "seventeen-character kid",
            swap(compat_parts, 0, "k" * 17),
            "compat.mp4",
            "198.51.100.23",
            "malformed",
        ),
        _case(
            "65-character title",
            swap(compat_parts, 2, "t" * 65),
            "compat.mp4",
            "198.51.100.23",
            "malformed",
        ),
        _case(
            "longer than 256 characters",
            compat + "." + "x" * 260,
            "compat.mp4",
            "198.51.100.23",
            "malformed",
        ),
    ]
    return {
        "_comment": (
            "ADR-0007 media-token vectors, generated by make_vectors.py. The keys are "
            "public test data: never deploy them."
        ),
        "keys": json.loads(st.keyset_json(KEYS)),
        "now": NOW,
        "clock_skew_s": st.CLOCK_SKEW_S,
        "max_ttl_s": st.MAX_TTL_S,
        "cases": cases,
        "client_nets": [{"address": a, "net": n} for a, n in CLIENT_NETS],
    }


def render() -> str:
    return json.dumps(build(), indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="fail if vectors.json is stale")
    args = parser.parse_args()
    text = render()
    if args.check:
        if VECTORS.read_text(encoding="utf-8") != text:
            sys.stderr.write("vectors.json is stale: run make_vectors.py\n")
            return 1
        return 0
    VECTORS.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
