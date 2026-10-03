#!/usr/bin/env python3
"""End-to-end checks of the media edge (ADR-0007), driven by streaming/tests/run.sh.

Standard library only. Steps (run.sh calls them in this order):

    prepare WORK                      keys, media tree and fuzz vectors in WORK
    reference                         the Python reference against vectors.json
    local --edge URL --control URL --work WORK [--auth-ttl S]
    s3 --edge URL --control URL --work WORK --container NAME
    failclosed --edge URL --work WORK (run with stream-auth stopped)
    logs --work WORK FILE...          the edge's captured output

Every token, signature, secret and credential a step sends is recorded in
WORK/secrets.json; `logs` proves none of them reached the edge's output.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import http.client
import ipaddress
import json
import random
import re
import secrets
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import sign_token as st

VECTORS = Path(__file__).with_name("vectors.json")
MIB = 1024 * 1024
# The title directory: an asset key with the UUID shape the backend is expected to use.
TITLE = "0192f3a4-b5c6-7d8e-9f00-1122334455aa"
COMPAT_SIZE = 9 * MIB + 12345  # over directio (8m) locally, three 4 MB slices in S3 mode
ALLOWED_ORIGIN = "https://app.example.com"
# The edge trusts X-Forwarded-For from the test network (EDGE_REAL_IP_FROM).
CLIENT_V4 = "203.0.113.200"
CLIENT_V6 = "2001:db8:1:2:ffff::1"
COOKIE = "sessionid=edge-test-cookie-7f3a91"
AUTHORIZATION = "Bearer edge-test-credential-51c0de"
XTREAM_USER = "edgetestuser"
XTREAM_PASSWORD = "edge-test-password-92b7"  # noqa: S105 - a fake credential the logs must not show
LOG_KEYS = {
    "time",
    "edge",
    "request_id",
    "remote_addr",
    "method",
    "path",
    "status",
    "bytes_sent",
    "request_time",
    "range",
    "session",
    "kid",
    "title",
    "rendition",
    "verdict",
    "reason",
    "auth_status",
    "auth_cache",
    "upstream_cache_status",
    "user_agent",
}
IMMUTABLE = "public, max-age=31536000, immutable"
PLAYLIST = "max-age=60"


# ---------------------------------------------------------------------------- output


class Checks:
    """Counts and prints results; a failed check never stops the step."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.passed = 0
        self.failed: list[str] = []
        print(f"== {label}")

    def check(self, name: str, ok: bool, detail: object = "") -> bool:
        if ok:
            self.passed += 1
            print(f"  ok    {name}")
        else:
            self.failed.append(name)
            print(f"  FAIL  {name}" + (f": {detail}" if detail != "" else ""))
        return ok

    def finish(self) -> int:
        total = self.passed + len(self.failed)
        print(f"-- {self.label}: {self.passed}/{total} passed")
        return 1 if self.failed else 0


# ------------------------------------------------------------------------------ http


@dataclass
class Response:
    status: int
    headers: list[tuple[str, str]]
    body: bytes

    def all(self, name: str) -> list[str]:
        return [value for key, value in self.headers if key.lower() == name.lower()]

    def get(self, name: str) -> str | None:
        values = self.all(name)
        return values[0] if values else None


def request(base: str, method: str, path: str, headers: dict[str, str] | None = None) -> Response:
    """One request on its own connection; the path is sent exactly as given."""
    url = urlsplit(base)
    conn = http.client.HTTPConnection(url.hostname or "127.0.0.1", url.port or 80, timeout=30)
    try:
        conn.putrequest(method, path, skip_accept_encoding=True)
        for name, value in (headers or {}).items():
            conn.putheader(name, value)
        conn.endheaders()
        resp = conn.getresponse()
        body = resp.read()
        return Response(resp.status, resp.getheaders(), body)
    finally:
        conn.close()


def control_json(control: str, path: str) -> Any:
    resp = request(control, "GET", path)
    if resp.status != 200:
        raise RuntimeError(f"stub control {path} answered {resp.status}")
    return json.loads(resp.body)


def control_post(control: str, path: str) -> None:
    resp = request(control, "POST", path)
    if resp.status != 204:
        raise RuntimeError(f"stub control {path} answered {resp.status}")


# --------------------------------------------------------------------------- context


class Work:
    """The prepared directory: keys, media and the record of sensitive strings."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.keys = st.load_keyset(root / "keys.json")
        self.media = root / "media"
        self.secrets_file = root / "secrets.json"
        try:
            self.secrets: list[str] = json.loads(self.secrets_file.read_text())
        except FileNotFoundError:
            self.secrets = []

    def remember(self, *values: str) -> None:
        self.secrets.extend(value for value in values if value)
        self.secrets_file.write_text(json.dumps(sorted(set(self.secrets)), indent=1))

    def file(self, relative: str) -> bytes:
        return (self.media / TITLE / relative).read_bytes()

    def token(
        self,
        *,
        rendition: str,
        session: str | None = None,
        ttl: int = 7200,
        exp: int | None = None,
        net: str | None = None,
        kid: str | None = None,
        title: str = TITLE,
    ) -> str:
        token = st.sign(
            self.keys,
            session=session or secrets.token_hex(16),
            title=title,
            rendition=rendition,
            exp=exp if exp is not None else int(time.time()) + ttl,
            net=net,
            kid=kid,
        )
        self.remember(token, token.rsplit(".", 1)[1])
        return token

    def foreign(self, token: str) -> str:
        """Record a hand-made token (tampered or signed elsewhere)."""
        self.remember(token, token.rsplit(".", 1)[-1])
        return token


def tamper_signature(token: str) -> str:
    """Change one signature character in the middle (never a no-op)."""
    i = len(token) - 20
    return token[:i] + ("B" if token[i] == "A" else "A") + token[i + 1 :]


def session_of(token: str) -> str:
    return token.split(".")[1]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ------------------------------------------------------------------------- prepare


def write_media(media: Path) -> None:
    root = media / TITLE
    (root / "hls" / "v720").mkdir(parents=True, exist_ok=True)
    rng = random.Random(7)
    (root / "compat.mp4").write_bytes(rng.randbytes(COMPAT_SIZE))
    (root / "hls" / "master.m3u8").write_text(
        "#EXTM3U\n#EXT-X-VERSION:7\n#EXT-X-INDEPENDENT-SEGMENTS\n"
        '#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1280x720,CODECS="avc1.64001f,mp4a.40.2"\n'
        "v720/index.m3u8\n"
    )
    (root / "hls" / "v720" / "index.m3u8").write_text(
        "#EXTM3U\n#EXT-X-VERSION:7\n#EXT-X-TARGETDURATION:6\n#EXT-X-PLAYLIST-TYPE:VOD\n"
        '#EXT-X-MAP:URI="init.mp4"\n#EXTINF:6.000,\nseg_00001.m4s\n#EXT-X-ENDLIST\n'
    )
    (root / "hls" / "v720" / "init.mp4").write_bytes(rng.randbytes(1500))
    (root / "hls" / "v720" / "seg_00001.m4s").write_bytes(rng.randbytes(300_000))


def random_address(rng: random.Random) -> str:
    kind = rng.randrange(8)
    if kind == 0:
        return str(ipaddress.IPv4Address(rng.getrandbits(32)))
    if kind in (1, 2):
        groups = [rng.choice([0, 0, rng.getrandbits(16)]) for _ in range(8)]
        address = ipaddress.IPv6Address(int("".join(f"{g:04x}" for g in groups), 16))
        text = address.compressed if kind == 1 else address.exploded
        return text.upper() if rng.random() < 0.3 else text
    if kind == 3:
        return "::ffff:" + str(ipaddress.IPv4Address(rng.getrandbits(32)))
    if kind == 4:
        head = ":".join(f"{rng.getrandbits(16):x}" for _ in range(6))
        return head + ":" + str(ipaddress.IPv4Address(rng.getrandbits(32)))
    return mangle(random_address(rng) if rng.random() < 0.5 else "203.0.113.7", rng)


def mangle(text: str, rng: random.Random) -> str:
    """One character inserted, deleted or replaced."""
    i = rng.randrange(len(text) + 1)
    char = rng.choice("0123456789abcdefABCDEFg:.% ")
    action = rng.randrange(3)
    if action == 0:
        return text[:i] + char + text[i:]
    if action == 1 and i < len(text):
        return text[:i] + text[i + 1 :]
    return text[:i] + char + text[i + 1 :]


def expected_net(address: str) -> str | None:
    try:
        return st.client_net(address)
    except ValueError:
        return None


def mutate(token: str, rng: random.Random) -> str:
    alphabet = "AZaz09_-.=/+~"
    i = rng.randrange(len(token))
    action = rng.randrange(6)
    if action == 0:
        return token[:i] + rng.choice(alphabet) + token[i + 1 :]
    if action == 1:
        return token[:i] + token[i + 1 :]
    if action == 2:
        return token[:i] + rng.choice(alphabet) + token[i:]
    if action == 3:
        parts = token.split(".")
        a, b = rng.randrange(len(parts)), rng.randrange(len(parts))
        parts[a], parts[b] = parts[b], parts[a]
        return ".".join(parts)
    if action == 4:
        return token[: rng.randrange(len(token))]
    return token + "." + token.rsplit(".", 1)[1]


def fuzz(keyset: st.KeySet, now: int, rng: random.Random, count: int) -> dict[str, Any]:
    cases = []
    renditions = ["compat", "hls", "uhd", "r_1-x"]
    for n in range(count):
        rendition = rng.choice(renditions)
        net = None
        if rng.random() < 0.4:
            net = expected_net(random_address(rng)) or "cb0071"
        token = st.sign(
            keyset,
            session=f"{rng.getrandbits(128):032x}",
            title=rng.choice([TITLE, "t", "A_b-9" * 12]),
            rendition=rendition,
            exp=now + rng.choice([-3600, -31, -30, -29, 0, 60, 7200, st.MAX_TTL_S + 1]),
            net=net,
            kid=rng.choice(["k2", "k1"]),
        )
        if rng.random() < 0.5:
            token = mutate(token, rng)
        tail = rng.choice(
            [
                f"{rendition}.mp4",
                f"{rendition}/master.m3u8",
                f"{rendition}/v720/seg_00001.m4s",
                "compat.mp4",
                f"{rendition}x/a.ts",
                f"{rendition}/../x.mp4",
                f"{rendition}/.x.m3u8",
                f"{rendition}/dir",
                f"{rendition}" + "/a" * 8 + ".ts",
            ]
        )
        client_ip = random_address(rng)
        try:
            st.verify(keyset, token, now=now, client_ip=client_ip, tail=tail)
            verdict = "ok"
        except st.TokenError as exc:
            verdict = exc.reason
        cases.append(
            {
                "name": f"fuzz {n}",
                "token": token,
                "tail": tail,
                "client_ip": client_ip,
                "verdict": verdict,
            }
        )
    nets = [
        {"address": a, "net": expected_net(a)} for a in (random_address(rng) for _ in range(count))
    ]
    return {
        "keys": json.loads(st.keyset_json(keyset)),
        "now": now,
        "cases": cases,
        "nets": nets,
    }


def cmd_prepare(args: argparse.Namespace) -> int:
    work: Path = args.work
    work.mkdir(parents=True, exist_ok=True)
    # Current k2 signs; previous k1 is still accepted (one rotation ago).
    keyset = st.KeySet(st.generate_key("k2"), st.generate_key("k1"))
    keys = work / "keys.json"
    keys.write_text(st.keyset_json(keyset))
    keys.chmod(0o644)  # read by nginx's master in the container (the test keys are throwaway)
    write_media(work / "media")
    rng = random.Random(args.seed)
    fuzz_keys = st.KeySet(st.Key("k2", bytes(range(32))), st.Key("k1", bytes(range(1, 33))))
    data = fuzz(fuzz_keys, 1767225000, rng, args.count)
    (work / "fuzz.json").write_text(json.dumps(data))
    verdicts: dict[str, int] = {}
    for case in data["cases"]:
        verdicts[case["verdict"]] = verdicts.get(case["verdict"], 0) + 1
    stored = json.loads(keys.read_text())
    Work(work).remember(
        stored["current"]["secret"],
        stored["previous"]["secret"],
        *(key.secret.hex() for key in (keyset.current, keyset.previous) if key is not None),
    )
    print(f"prepared {work}: keys k2+k1, media, {args.count} fuzz cases {sorted(verdicts.items())}")
    return 0


# ----------------------------------------------------------------------- reference


def cmd_reference(_args: argparse.Namespace) -> int:
    c = Checks("reference signer (sign_token.py) against vectors.json")
    vectors = json.loads(VECTORS.read_text())
    keyset = st.parse_keyset(vectors["keys"])
    for case in vectors["cases"]:
        try:
            claims = st.verify(
                keyset,
                case["token"],
                now=vectors["now"],
                client_ip=case["client_ip"],
                tail=case["tail"],
            )
            verdict = "ok"
            if "claims" in case:
                verdict = "ok" if case["claims"] == dataclasses.asdict(claims) else "claims differ"
        except st.TokenError as exc:
            verdict = exc.reason
        c.check(
            f"vector {case['name']!r} -> {case['verdict']}", verdict == case["verdict"], verdict
        )
    for entry in vectors["client_nets"]:
        got = expected_net(entry["address"])
        c.check(f"net of {entry['address']!r}", got == entry["net"], got)
    # Signing is deterministic: re-signing every valid vector gives the same token.
    for case in vectors["cases"]:
        if case["verdict"] == "ok":
            claims = case["claims"]
            again = st.sign(
                keyset,
                session=claims["session"],
                title=claims["title"],
                rendition=claims["rendition"],
                exp=claims["exp"],
                net=claims["net"],
                kid=claims["kid"],
            )
            c.check(f"re-signing {case['name']!r} is byte-identical", again == case["token"])
    return c.finish()


# --------------------------------------------------------------------------- local


def common_media_headers(c: Checks, label: str, resp: Response) -> None:
    c.check(f"{label}: nosniff", resp.get("X-Content-Type-Options") == "nosniff")
    c.check(f"{label}: Referrer-Policy no-referrer", resp.get("Referrer-Policy") == "no-referrer")
    c.check(f"{label}: no X-Reason on success", resp.get("X-Reason") is None, resp.get("X-Reason"))


def check_denied(c: Checks, label: str, resp: Response, status: int, reason: str | None) -> None:
    c.check(f"{label}: {status}", resp.status == status, resp.status)
    if reason is not None:
        c.check(f"{label}: X-Reason {reason}", resp.get("X-Reason") == reason, resp.get("X-Reason"))
    c.check(
        f"{label}: Cache-Control no-store (once)",
        resp.all("Cache-Control") == ["no-store"],
        resp.all("Cache-Control"),
    )
    c.check(f"{label}: no media bytes", len(resp.body) < 1024, len(resp.body))


def check_progressive(c: Checks, edge: str, work: Work, token: str, label: str) -> None:
    data = work.file("compat.mp4")
    path = f"/v/{token}/compat.mp4"
    resp = request(edge, "GET", path)
    c.check(f"{label}: GET 200", resp.status == 200, resp.status)
    c.check(f"{label}: full body matches", sha(resp.body) == sha(data), len(resp.body))
    c.check(
        f"{label}: Content-Type video/mp4",
        resp.get("Content-Type") == "video/mp4",
        resp.get("Content-Type"),
    )
    c.check(
        f"{label}: Cache-Control immutable (once)",
        resp.all("Cache-Control") == [IMMUTABLE],
        resp.all("Cache-Control"),
    )
    c.check(
        f"{label}: Accept-Ranges bytes (once)",
        resp.all("Accept-Ranges") == ["bytes"],
        resp.all("Accept-Ranges"),
    )
    common_media_headers(c, label, resp)


def check_ranges(c: Checks, edge: str, work: Work, token: str, label: str) -> None:
    data = work.file("compat.mp4")
    size = len(data)
    path = f"/v/{token}/compat.mp4"
    cases = [
        ("first bytes", "bytes=100-1099", 100, 1099),
        (
            "across 8 MiB (directio, third slice)",
            f"bytes={8 * MIB - 1000}-{8 * MIB + 999}",
            8 * MIB - 1000,
            8 * MIB + 999,
        ),
        (
            "across the first slice boundary",
            f"bytes={4 * MIB - 10}-{4 * MIB + 9}",
            4 * MIB - 10,
            4 * MIB + 9,
        ),
        ("suffix", "bytes=-500", size - 500, size - 1),
        ("open-ended", f"bytes={9 * MIB}-", 9 * MIB, size - 1),
    ]
    for name, header, first, last in cases:
        resp = request(edge, "GET", path, {"Range": header})
        ok = resp.status == 206 and resp.body == data[first : last + 1]
        c.check(
            f"{label}: Range {name} -> 206 with the right bytes", ok, (resp.status, len(resp.body))
        )
        c.check(
            f"{label}: Range {name} Content-Range",
            resp.get("Content-Range") == f"bytes {first}-{last}/{size}",
            resp.get("Content-Range"),
        )
        c.check(
            f"{label}: Range {name} Accept-Ranges bytes (once)",
            resp.all("Accept-Ranges") == ["bytes"],
            resp.all("Accept-Ranges"),
        )
        c.check(
            f"{label}: Range {name} Cache-Control immutable (once)",
            resp.all("Cache-Control") == [IMMUTABLE],
            resp.all("Cache-Control"),
        )
    resp = request(edge, "GET", path, {"Range": f"bytes={size}-"})
    c.check(f"{label}: unsatisfiable range -> 416", resp.status == 416, resp.status)
    c.check(
        f"{label}: 416 Cache-Control no-store (once)",
        resp.all("Cache-Control") == ["no-store"],
        resp.all("Cache-Control"),
    )
    resp = request(edge, "GET", path, {"Range": "bytes=0-1,5-9"})
    c.check(
        f"{label}: two ranges -> whole file (max_ranges 1)",
        resp.status == 200 and len(resp.body) == size,
        resp.status,
    )
    c.check(
        f"{label}: two ranges Accept-Ranges (once)",
        resp.all("Accept-Ranges") == ["bytes"],
        resp.all("Accept-Ranges"),
    )
    resp = request(edge, "HEAD", path)
    c.check(
        f"{label}: HEAD 200 with Content-Length and no body",
        resp.status == 200 and resp.get("Content-Length") == str(size) and resp.body == b"",
        (resp.status, resp.get("Content-Length")),
    )


def check_hls(c: Checks, edge: str, work: Work, token: str, label: str) -> None:
    files = [
        ("master playlist", "hls/master.m3u8", "application/vnd.apple.mpegurl", PLAYLIST),
        ("variant playlist", "hls/v720/index.m3u8", "application/vnd.apple.mpegurl", PLAYLIST),
        ("init section", "hls/v720/init.mp4", "video/mp4", IMMUTABLE),
        ("segment", "hls/v720/seg_00001.m4s", "video/mp4", IMMUTABLE),
    ]
    for name, tail, ctype, cache in files:
        resp = request(edge, "GET", f"/v/{token}/{tail}")
        c.check(
            f"{label}: {name} 200 with the right bytes",
            resp.status == 200 and resp.body == work.file(tail),
            resp.status,
        )
        c.check(
            f"{label}: {name} Content-Type {ctype}",
            resp.get("Content-Type") == ctype,
            resp.get("Content-Type"),
        )
        c.check(
            f"{label}: {name} Cache-Control {cache!r} (once)",
            resp.all("Cache-Control") == [cache],
            resp.all("Cache-Control"),
        )


def auth_calls(control: str, session: str) -> dict[str, Any]:
    result: dict[str, Any] = control_json(control, f"/__control/calls?session={session}")
    return result


def run_local(args: argparse.Namespace) -> int:
    c = Checks(f"local disk mode ({args.edge})")
    edge, control = args.edge, args.control
    work = Work(args.work)
    work.remember(
        COOKIE.split("=", 1)[1], AUTHORIZATION.split(" ", 1)[1], XTREAM_PASSWORD, XTREAM_USER
    )
    control_post(control, "/__control/reset")
    v4 = {"X-Forwarded-For": CLIENT_V4}

    resp = request(edge, "GET", "/healthz")
    c.check("healthz 200 ok", resp.status == 200 and resp.body == b"ok\n", resp.status)

    # Valid tokens: current kid, then the previous kid (one rotation ago).
    current = work.token(rendition="compat")
    check_progressive(c, edge, work, current, "current kid")
    check_ranges(c, edge, work, current, "current kid")
    check_progressive(c, edge, work, work.token(rendition="compat", kid="k1"), "previous kid")

    # Signature, expiry and key checks (njs, before stream-auth).
    resp = request(edge, "GET", f"/v/{work.foreign(tamper_signature(current))}/compat.mp4")
    check_denied(c, "tampered signature", resp, 403, "invalid")
    parts = current.split(".")
    parts[3] = "uhd"
    resp = request(edge, "GET", f"/v/{work.foreign('.'.join(parts))}/uhd.mp4")
    check_denied(c, "tampered rendition", resp, 403, "invalid")
    resp = request(
        edge, "GET", f"/v/{work.token(rendition='compat', exp=int(time.time()) - 3600)}/compat.mp4"
    )
    check_denied(c, "expired an hour ago", resp, 403, "expired")
    resp = request(
        edge,
        "GET",
        f"/v/{work.token(rendition='compat', exp=int(time.time()) - 5)}/compat.mp4",
        {"Range": "bytes=0-9"},
    )
    c.check(
        "expired 5 s ago, inside the 30 s clock-skew allowance: 206",
        resp.status == 206,
        resp.status,
    )
    resp = request(
        edge, "GET", f"/v/{work.token(rendition='compat', ttl=st.MAX_TTL_S + 3600)}/compat.mp4"
    )
    check_denied(c, "exp beyond the 7-day maximum", resp, 403, "invalid")
    stranger = st.KeySet(st.generate_key("k9"))
    unknown = st.sign(
        stranger,
        session=secrets.token_hex(16),
        title=TITLE,
        rendition="compat",
        exp=int(time.time()) + 600,
    )
    resp = request(edge, "GET", f"/v/{work.foreign(unknown)}/compat.mp4")
    check_denied(c, "unknown kid", resp, 403, "invalid")
    relabelled = work.token(rendition="compat", kid="k1").replace("k1.", "k2.", 1)
    resp = request(edge, "GET", f"/v/{work.foreign(relabelled)}/compat.mp4")
    check_denied(c, "previous key's token relabelled as current", resp, 403, "invalid")

    # Network binding (the real client address comes from X-Forwarded-For).
    bound4 = work.token(rendition="compat", net=st.client_net("203.0.113.7"))
    resp = request(edge, "GET", f"/v/{bound4}/compat.mp4", {**v4, "Range": "bytes=0-9"})
    c.check("IPv4 /24 binding, same /24: 206", resp.status == 206, resp.status)
    resp = request(edge, "GET", f"/v/{bound4}/compat.mp4", {"X-Forwarded-For": "198.51.100.7"})
    check_denied(c, "IPv4 /24 binding, other /24", resp, 403, "ip_mismatch")
    resp = request(edge, "GET", f"/v/{bound4}/compat.mp4")
    check_denied(c, "IPv4 /24 binding, edge sees the proxy's address", resp, 403, "ip_mismatch")
    bound6 = work.token(rendition="compat", net=st.client_net("2001:db8:1:2::7"))
    resp = request(
        edge, "GET", f"/v/{bound6}/compat.mp4", {"X-Forwarded-For": CLIENT_V6, "Range": "bytes=0-9"}
    )
    c.check("IPv6 /64 binding, same /64: 206", resp.status == 206, resp.status)
    resp = request(edge, "GET", f"/v/{bound6}/compat.mp4", {"X-Forwarded-For": "2001:db8:1:3::7"})
    check_denied(c, "IPv6 /64 binding, other /64", resp, 403, "ip_mismatch")
    resp = request(edge, "GET", f"/v/{bound6}/compat.mp4", v4)
    check_denied(c, "IPv6 binding, IPv4 client", resp, 403, "ip_mismatch")

    # stream-auth: the request it gets, and the 204 cached per token.
    token = work.token(rendition="compat")
    session = session_of(token)
    path = f"/v/{token}/compat.mp4?player=test"
    sent = {
        **v4,
        "Range": "bytes=0-9",
        "Cookie": COOKIE,
        "Authorization": AUTHORIZATION,
        "X-Request-ID": "edge-test-req-0001",
    }
    statuses = [request(edge, "GET", path, sent).status for _ in range(3)]
    calls = auth_calls(control, session)
    c.check("three requests, one token: all 206", statuses == [206, 206, 206], statuses)
    c.check("stream-auth asked once (204 cached per token)", calls["count"] == 1, calls["count"])
    last = calls["last"] or {}
    c.check(
        "stream-auth got X-Original-URI = the request URI",
        last.get("x-original-uri") == path,
        last.get("x-original-uri"),
    )
    c.check(
        "stream-auth got X-Real-IP = the client",
        last.get("x-real-ip") == CLIENT_V4,
        last.get("x-real-ip"),
    )
    c.check(
        "stream-auth got X-Request-ID",
        last.get("x-request-id") == "edge-test-req-0001",
        last.get("x-request-id"),
    )
    c.check("stream-auth got X-Edge-Id", bool(last.get("x-edge-id")), last.get("x-edge-id"))
    c.check(
        "stream-auth got Host from EDGE_AUTH_HOST", last.get("host") == "auth", last.get("host")
    )
    leaked = sorted({"cookie", "authorization", "x-forwarded-for", "range"} & set(last))
    c.check("stream-auth got none of the client's headers", not leaked, leaked)
    other = work.token(rendition="hls", session=session)
    request(edge, "GET", f"/v/{other}/hls/master.m3u8", v4)
    c.check(
        "a second token for the session is checked on its own",
        auth_calls(control, session)["count"] == 2,
    )
    resp = request(
        edge,
        "GET",
        f"/v/{token}/compat.mp4",
        {"X-Request-ID": "edge-test-req-0002", "Range": "bytes=0-0"},
    )
    c.check(
        "X-Request-ID echoed",
        resp.get("X-Request-ID") == "edge-test-req-0002",
        resp.get("X-Request-ID"),
    )
    resp = request(
        edge, "GET", f"/v/{token}/compat.mp4", {"X-Request-ID": "bad id", "Range": "bytes=0-0"}
    )
    c.check(
        "malformed X-Request-ID replaced",
        re.fullmatch(r"[0-9a-f]{32}", resp.get("X-Request-ID") or "") is not None,
        resp.get("X-Request-ID"),
    )

    # Kicks: a 403 from stream-auth is passed on and never cached.
    kicked = work.token(rendition="compat")
    ksession = session_of(kicked)
    control_post(control, f"/__control/kick?session={ksession}")
    resp = request(edge, "GET", f"/v/{kicked}/compat.mp4", v4)
    check_denied(c, "kicked session", resp, 403, "kicked")
    request(edge, "GET", f"/v/{kicked}/compat.mp4", v4)
    c.check("a 403 from stream-auth is not cached", auth_calls(control, ksession)["count"] == 2)
    control_post(control, f"/__control/unkick?session={ksession}")
    resp = request(edge, "GET", f"/v/{kicked}/compat.mp4", {**v4, "Range": "bytes=0-9"})
    c.check("un-kicked: plays at once (nothing cached the 403)", resp.status == 206, resp.status)
    # A kick after the 204 was cached takes effect when the cached answer expires.
    control_post(control, f"/__control/kick?session={ksession}")
    resp = request(edge, "GET", f"/v/{kicked}/compat.mp4", {**v4, "Range": "bytes=0-9"})
    c.check(
        "kicked while its 204 is cached: still plays within the TTL",
        resp.status == 206,
        resp.status,
    )
    time.sleep(args.auth_ttl + 1.5)
    resp = request(edge, "GET", f"/v/{kicked}/compat.mp4", {**v4, "Range": "bytes=0-9"})
    check_denied(c, f"kicked, after the {args.auth_ttl} s auth cache TTL", resp, 403, "kicked")

    # HLS: playlists for a minute, everything else immutable; CORS for the portal.
    hls = work.token(rendition="hls")
    check_hls(c, edge, work, hls, "HLS")
    resp = request(edge, "GET", f"/v/{hls}/hls/master.m3u8", {"Origin": ALLOWED_ORIGIN})
    c.check(
        "CORS: allowed origin echoed",
        resp.get("Access-Control-Allow-Origin") == ALLOWED_ORIGIN,
        resp.get("Access-Control-Allow-Origin"),
    )
    c.check("CORS: Vary Origin", "Origin" in (resp.get("Vary") or ""), resp.get("Vary"))
    c.check(
        "CORS: Content-Range exposed",
        "Content-Range" in (resp.get("Access-Control-Expose-Headers") or ""),
    )
    resp = request(edge, "GET", f"/v/{hls}/hls/master.m3u8", {"Origin": "https://evil.example.net"})
    c.check(
        "CORS: other origin gets no Allow-Origin",
        resp.get("Access-Control-Allow-Origin") is None,
        resp.get("Access-Control-Allow-Origin"),
    )
    preflight = {
        "Origin": ALLOWED_ORIGIN,
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "range",
    }
    resp = request(edge, "OPTIONS", f"/v/{hls}/hls/v720/seg_00001.m4s", preflight)
    c.check(
        "CORS preflight: 204 with methods, Range and max-age",
        resp.status == 204
        and resp.get("Access-Control-Allow-Origin") == ALLOWED_ORIGIN
        and resp.get("Access-Control-Allow-Methods") == "GET, HEAD, OPTIONS"
        and resp.get("Access-Control-Allow-Headers") == "Range"
        and resp.get("Access-Control-Max-Age") == "600",
        (resp.status, resp.headers),
    )
    resp = request(
        edge,
        "OPTIONS",
        f"/v/{hls}/hls/master.m3u8",
        {**preflight, "Origin": "https://evil.example.net"},
    )
    c.check(
        "CORS preflight, other origin: no Allow-Origin",
        resp.get("Access-Control-Allow-Origin") is None,
    )

    # Scope and paths: a token reaches its own rendition and nothing else.
    resp = request(edge, "GET", f"/v/{current}/hls/master.m3u8")
    check_denied(c, "compat token, HLS file", resp, 403, "invalid")
    resp = request(edge, "GET", f"/v/{hls}/compat.mp4")
    check_denied(c, "HLS token, progressive file", resp, 403, "invalid")
    for name, tail in [
        ("hidden file", "hls/.master.m3u8"),
        ("directory", "hls/v720"),
        ("trailing slash", "hls/v720/"),
        ("nothing after the token", ""),
    ]:
        resp = request(edge, "GET", f"/v/{hls}/{tail}")
        check_denied(c, f"bad path ({name})", resp, 403, None)
    resp = request(edge, "GET", f"/v/{hls}/hls/../compat.mp4")
    check_denied(c, "dot-dot inside the token path", resp, 403, None)
    resp = request(edge, "GET", f"/v/{hls}/hls/%2e%2e/compat.mp4")
    check_denied(c, "percent-encoded dot-dot inside the token path", resp, 403, None)
    resp = request(edge, "GET", f"/v/{hls}/../../etc/passwd")
    c.check(
        "dot-dot out of /v/: 404, no file",
        resp.status == 404 and b"root:" not in resp.body,
        resp.status,
    )
    resp = request(edge, "GET", f"/v/{hls}/hls/missing.m3u8")
    check_denied(c, "missing file in scope", resp, 404, None)
    resp = request(edge, "POST", f"/v/{hls}/hls/master.m3u8")
    c.check("POST: 405", resp.status == 405, resp.status)
    resp = request(edge, "GET", f"/movie/{XTREAM_USER}/{XTREAM_PASSWORD}/123.mp4")
    c.check("Xtream path on the media host: 404", resp.status == 404, resp.status)
    resp = request(edge, "GET", f"/{XTREAM_USER}/{XTREAM_PASSWORD}/123")
    c.check("other path: 404", resp.status == 404, resp.status)
    return c.finish()


# ------------------------------------------------------------------------------ s3


def origin_requests(control: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = control_json(control, "/__control/origin")["requests"]
    return result


def run_s3(args: argparse.Namespace) -> int:
    c = Checks(f"S3 origin mode ({args.edge})")
    edge, control = args.edge, args.control
    work = Work(args.work)
    control_post(control, "/__control/reset")
    data = work.file("compat.mp4")
    compat_key = f"/origin/{TITLE}/compat.mp4"

    resp = request(edge, "GET", "/healthz")
    c.check("healthz 200 ok", resp.status == 200 and resp.body == b"ok\n", resp.status)

    first = work.token(rendition="compat")
    sent = {
        "Cookie": COOKIE,
        "Authorization": AUTHORIZATION,
        "X-Forwarded-For": CLIENT_V4,
        "Origin": ALLOWED_ORIGIN,
    }
    resp = request(edge, "GET", f"/v/{first}/compat.mp4", sent)
    c.check(
        "first session: 200 with the whole file",
        resp.status == 200 and sha(resp.body) == sha(data),
        (resp.status, len(resp.body)),
    )
    c.check(
        "Cache-Control is the edge's (immutable, once)",
        resp.all("Cache-Control") == [IMMUTABLE],
        resp.all("Cache-Control"),
    )
    c.check(
        "Accept-Ranges bytes (once)",
        resp.all("Accept-Ranges") == ["bytes"],
        resp.all("Accept-Ranges"),
    )
    leaked = sorted(
        {k for k, _ in resp.headers if k.lower().startswith("x-amz") or k.lower() == "set-cookie"}
    )
    c.check("no x-amz-* or Set-Cookie from the origin", not leaked, leaked)
    c.check(
        "CORS is the edge's, not the origin's *",
        resp.get("Access-Control-Allow-Origin") == ALLOWED_ORIGIN,
        resp.all("Access-Control-Allow-Origin"),
    )
    c.check(
        "Server is not the origin's",
        "auth-stub" not in (resp.get("Server") or ""),
        resp.get("Server"),
    )

    fetched = [r for r in origin_requests(control) if r["path"] == compat_key]
    ranges = sorted(r["headers"].get("range", "") for r in fetched)
    want = [f"bytes={i * 4 * MIB}-{(i + 1) * 4 * MIB - 1}" for i in range(3)]
    c.check("origin read the file in three 4 MB slices", ranges == sorted(want), ranges)
    tokens_at_origin = [
        r
        for r in origin_requests(control)
        if any(first in str(v) for v in (r["path"], r["headers"]))
    ]
    c.check("the origin never sees the token", not tokens_at_origin)
    forwarded = sorted(
        {h for r in fetched for h in r["headers"]}
        & {"cookie", "authorization", "x-forwarded-for", "origin", "x-real-ip"}
    )
    c.check("the origin gets none of the client's headers", not forwarded, forwarded)

    second = work.token(rendition="compat")
    before = len(origin_requests(control))
    resp = request(
        edge, "GET", f"/v/{second}/compat.mp4", {"Range": f"bytes={4 * MIB - 10}-{4 * MIB + 9}"}
    )
    c.check(
        "second session, range across a slice: 206 with the right bytes",
        resp.status == 206 and resp.body == data[4 * MIB - 10 : 4 * MIB + 10],
        resp.status,
    )
    c.check(
        "206 Accept-Ranges bytes (once)",
        resp.all("Accept-Ranges") == ["bytes"],
        resp.all("Accept-Ranges"),
    )
    resp = request(edge, "GET", f"/v/{second}/compat.mp4")
    c.check(
        "second session: whole file from the cache",
        resp.status == 200 and sha(resp.body) == sha(data),
        resp.status,
    )
    c.check(
        "cache key has no token: no new origin reads",
        len(origin_requests(control)) == before,
        len(origin_requests(control)) - before,
    )
    resp = request(edge, "GET", f"/v/{second}/compat.mp4", {"Range": f"bytes={COMPAT_SIZE}-"})
    check_denied(c, "unsatisfiable range", resp, 416, None)

    hls = work.token(rendition="hls")
    check_hls(c, edge, work, hls, "S3 HLS")

    # Origin errors never reach the client: their bodies name the bucket and the key.
    for name, tail, status in [
        ("missing object (origin 404)", "hls/missing.m3u8", 404),
        ("unreadable object (origin 403)", "hls/denied/seg.m4s", 404),
        ("origin failure (origin 500)", "hls/broken/seg.m4s", 503),
    ]:
        resp = request(edge, "GET", f"/v/{hls}/{tail}")
        check_denied(c, name, resp, status, None)
        body = resp.body.decode(errors="replace")
        c.check(
            f"{name}: no origin body",
            "smart-iptv-media" not in body and "<Key>" not in body and TITLE not in body,
            body[:120],
        )
        c.check(
            f"{name}: no origin headers",
            not any(k.lower().startswith("x-amz") for k, _ in resp.headers),
        )

    # Edge denials keep their status in S3 mode (error_page 403 must not turn them into 404).
    resp = request(edge, "GET", f"/v/{work.foreign(tamper_signature(hls))}/hls/master.m3u8")
    check_denied(c, "tampered signature", resp, 403, "invalid")
    kicked = work.token(rendition="hls")
    control_post(control, f"/__control/kick?session={session_of(kicked)}")
    resp = request(edge, "GET", f"/v/{kicked}/hls/master.m3u8")
    check_denied(c, "kicked session", resp, 403, "kicked")

    config = subprocess.run(
        ["docker", "exec", args.container, "nginx", "-T"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    c.check(
        "default auth cache TTL: proxy_cache_valid 204 60s", "proxy_cache_valid 204 60s;" in config
    )
    c.check("slice 4m", "slice 4m;" in config)
    c.check(
        "cache key = object + slice range", "proxy_cache_key $edge_object$slice_range;" in config
    )
    c.check("proxy_cache_valid 200 206 30d", "proxy_cache_valid 200 206 30d;" in config)
    return c.finish()


# ---------------------------------------------------------------------- failclosed


def run_failclosed(args: argparse.Namespace) -> int:
    c = Checks(f"fail closed with stream-auth down ({args.edge})")
    work = Work(args.work)
    token = work.token(rendition="compat")
    resp = request(args.edge, "GET", f"/v/{token}/compat.mp4", {"X-Forwarded-For": CLIENT_V4})
    check_denied(c, "stream-auth unreachable", resp, 503, "unavailable")
    c.check("Retry-After sent", resp.get("Retry-After") == "5", resp.get("Retry-After"))
    resp = request(args.edge, "GET", f"/v/{work.foreign(tamper_signature(token))}/compat.mp4")
    check_denied(c, "bad token still 403 (njs answers first)", resp, 403, "invalid")
    return c.finish()


# ---------------------------------------------------------------------------- logs


def run_logs(args: argparse.Namespace) -> int:
    c = Checks("edge output (access log, error log, entrypoint)")
    work = Work(args.work)
    sensitive = sorted(set(work.secrets), key=len)
    for file in args.files:
        text = Path(file).read_text(encoding="utf-8", errors="replace")
        name = Path(file).name
        found = [s[:12] + "..." for s in sensitive if s in text]
        c.check(
            f"{name}: none of {len(sensitive)} tokens, signatures, secrets or credentials",
            not found,
            found,
        )
        sessions = set(re.findall(r"(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])", text))
        request_ids = set(re.findall(r'"request_id":"([0-9a-f]{32})"', text))
        c.check(
            f"{name}: no full session id (only 16-character prefixes)",
            not (sessions - request_ids),
            sorted(sessions - request_ids)[:3],
        )
        c.check(
            f"{name}: no request-scoped error lines (they quote the request line)",
            'request: "' not in text,
        )
        entries = []
        bad_lines = []
        for line in text.splitlines():
            if line.startswith("{"):
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    bad_lines.append(line[:80])
        c.check(
            f"{name}: {len(entries)} access log lines, all valid JSON",
            bool(entries) and not bad_lines,
            bad_lines[:2],
        )
        missing = sorted({key for e in entries for key in LOG_KEYS if key not in e})
        c.check(f"{name}: every line has the documented fields", not missing, missing)
        paths = [e["path"] for e in entries if str(e.get("path", "")).startswith("/v/")]
        odd = [p for p in paths if not re.match(r"^/v/(?:[0-9a-f]{16}|-)(?:/|$)", p)]
        c.check(
            f"{name}: /v/ paths carry the session prefix, not the token",
            bool(paths) and not odd,
            odd[:3],
        )
        c.check(
            f"{name}: Xtream-shaped paths masked",
            all("***" in e["path"] for e in entries if e["path"].startswith("/movie/")),
        )
        c.check(
            f"{name}: a successful ranged request was logged",
            any(e["status"] == 206 and e["verdict"] == "ok" for e in entries),
        )
        c.check(
            f"{name}: a bad signature was logged with its verdict",
            any(e["verdict"] == "bad_signature" for e in entries),
        )
        c.check(
            f"{name}: a kick was logged with stream-auth's reason",
            any(e["reason"] == "kicked" and e["auth_status"] == "403" for e in entries),
        )
        c.check(
            f"{name}: bytes_sent and request_time are numbers",
            all(
                isinstance(e["bytes_sent"], int) and isinstance(e["request_time"], (int, float))
                for e in entries
            ),
        )
        if "s3" in name:
            c.check(
                f"{name}: media cache hits logged",
                any(e["upstream_cache_status"] == "HIT" for e in entries),
            )
    return c.finish()


# ---------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    steps = parser.add_subparsers(dest="step", required=True)

    def step(name: str, func: Callable[[argparse.Namespace], int]) -> argparse.ArgumentParser:
        sub = steps.add_parser(name)
        sub.set_defaults(func=func)
        return sub

    prepare = step("prepare", cmd_prepare)
    prepare.add_argument("work", type=Path)
    prepare.add_argument("--count", type=int, default=600, help="fuzz cases")
    prepare.add_argument("--seed", type=int, default=20261003)
    step("reference", cmd_reference)
    for name, func in (("local", run_local), ("s3", run_s3), ("failclosed", run_failclosed)):
        sub = step(name, func)
        sub.add_argument("--edge", required=True)
        sub.add_argument("--control", default="")
        sub.add_argument("--work", type=Path, required=True)
        sub.add_argument("--auth-ttl", type=float, default=3.0, help="EDGE_AUTH_CACHE_TTL, seconds")
        sub.add_argument("--container", default="prework-edge-s3")
    logs = step("logs", run_logs)
    logs.add_argument("--work", type=Path, required=True)
    logs.add_argument("files", nargs="+")
    args = parser.parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    sys.exit(main())
