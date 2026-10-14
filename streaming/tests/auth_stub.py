#!/usr/bin/env python3
"""Stand-ins for the edge's two upstreams, for streaming/tests/run.sh.

GET /internal/stream-auth speaks the ADR-0007 contract like Django will: it reads
X-Original-URI and X-Real-IP and answers 204, or 403 with X-Reason for a kicked
session. It misbehaves on purpose, sending headers that would make a naive cache
store a 403 or skip a 204, so the tests prove the edge caches by its own policy.

GET|HEAD /origin/<key> (with --media) plays an S3-style origin for the edge's S3
mode: single byte ranges, an ETag, and the leaky extras a real bucket sends
(x-amz-* headers, object metadata, its own Cache-Control and CORS headers, XML error
bodies that name the bucket and the key). Keys with a segment named `denied` answer
403 and `broken` 500, as a private bucket and a failing one would.

Requests carrying `X-Live-Object` play the live relay (ADR-0017): they answer 200
with `relay <object>` and leaky headers (Cache-Control, Set-Cookie) the edge must
drop, and are recorded.

With --s3-creds FILE the origin is private (ADR-0022, G-44): every /origin/ request must
carry a valid SigV4 presigned query for that key (checked with the reference,
streaming/tools/s3_presign.py), else it gets S3's 403 AccessDenied.

With --tls-port (and --tls-cert, --tls-key) the stub also plays the control plane's
edges host for a standalone edge (ADR-0022), over HTTPS:
    POST /edge/v1/poll          bearer + X-Edge-Id: the key set (--keys), the origin key
                                (--s3-creds) and a CDN secret; reports are recorded
    GET  /edge/v1/stream-auth   bearer + X-Edge-Id, then the stream-auth contract, with
                                the client address in X-Edge-Client-IP

Test control (not part of any contract):
    POST /__control/kick?session=<id>     later checks for that session get 403
    POST /__control/unkick?session=<id>
    POST /__control/reset                 forget kicks and recorded requests
    GET  /__control/calls?session=<id>    {"count": n, "last": {header: value}}
    GET  /__control/origin                {"requests": [{"path", "headers"}, ...]}
    GET  /__control/relay                 {"requests": [{"path", "headers"}, ...]}
    GET  /__control/health
    GET  /__control/polls                 {"polls": [{"headers", "body"}, ...]}
    POST /__control/keys                  replace the key set the control plane hands out
"""

from __future__ import annotations

import argparse
import calendar
import email.utils
import hashlib
import hmac
import json
import mimetypes
import re
import ssl
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import s3_presign

TOKEN_IN_URI = re.compile(r"^/v/([^/?#]+)/")
RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
BUCKET = "smart-iptv-media"


class State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.kicked: set[str] = set()
        self.calls: dict[str, list[dict[str, str]]] = {}
        self.origin: list[dict[str, Any]] = []
        self.relay: list[dict[str, Any]] = []
        self.polls: list[dict[str, Any]] = []

    def reset(self) -> None:
        with self.lock:
            self.kicked.clear()
            self.calls.clear()
            self.origin.clear()
            self.relay.clear()
            self.polls.clear()


STATE = State()
MEDIA: Path | None = None
S3_CREDS: dict[str, str] | None = None
KEYS: dict[str, Any] | None = None
EDGE_ID = "edge-test-control"
EDGE_SECRET = ""
CDN_SECRET = ""


def presign_ok(path: str, query: str, host: str) -> bool:
    """Whether the query is a valid, current SigV4 presign of GET `path` on `host`."""
    if S3_CREDS is None:
        return True
    params = {k: v[0] for k, v in parse_qs(query, keep_blank_values=True).items()}
    try:
        amz_date = params["X-Amz-Date"]
        expires = int(params["X-Amz-Expires"])
        credential = params["X-Amz-Credential"]
        signature = params["X-Amz-Signature"]
    except (KeyError, ValueError):
        return False
    access, _, scope = credential.partition("/")
    if access != S3_CREDS["access_key"] or params.get("X-Amz-SignedHeaders") != "host":
        return False
    signed_at = calendar.timegm(time.strptime(amz_date, "%Y%m%dT%H%M%SZ"))
    if abs(time.time() - signed_at) > 900 or time.time() > signed_at + expires:
        return False
    region = scope.split("/")[1] if scope.count("/") >= 3 else ""
    expected = s3_presign.presign(
        host=host,
        path=unquote(path),
        access_key=access,
        secret_key=S3_CREDS["secret_key"],
        region=region,
        amz_date=amz_date,
        expires=expires,
    ).rsplit("=", 1)[1]
    return hmac.compare_digest(expected, signature)


def session_of(original_uri: str) -> str:
    match = TOKEN_IN_URI.match(original_uri)
    if not match:
        return ""
    fields = match.group(1).split(".")
    return fields[1] if len(fields) >= 6 else ""


def s3_error(code: str, key: str) -> bytes:
    """The error body S3-compatible stores send: it names the bucket and the key."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f"<Error><Code>{code}</Code><Message>{code}</Message><Key>{key}</Key>"
        f"<BucketName>{BUCKET}</BucketName><Resource>/{BUCKET}/{key}</Resource>"
        "<RequestId>stub-4442587FB7D0A2F9</RequestId></Error>"
    ).encode()


class Handler(BaseHTTPRequestHandler):
    server_version = "auth-stub"
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        """Quiet: the request line holds a token."""

    def _reply(
        self,
        status: int,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
        *,
        head: bool = False,
    ) -> None:
        self.send_response(status)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        if status not in (204, 304):
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body and status not in (204, 304) and not head:
            self.wfile.write(body)

    def _json(self, data: object) -> None:
        self._reply(200, json.dumps(data).encode(), {"Content-Type": "application/json"})

    def do_HEAD(self) -> None:
        url = urlsplit(self.path)
        if url.path.startswith("/origin/"):
            self._origin(url.path, head=True)
        else:
            self._reply(404, head=True)

    def _edge_authorised(self) -> bool:
        presented = self.headers.get("Authorization", "")
        return (
            bool(EDGE_SECRET)
            and hmac.compare_digest(presented.encode(), f"Bearer {EDGE_SECRET}".encode())
            and self.headers.get("X-Edge-Id") == EDGE_ID
        )

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        if self.headers.get("X-Live-Object"):
            self._relay()
        elif url.path == "/internal/stream-auth":
            self._stream_auth()
        elif url.path == "/edge/v1/stream-auth":
            if self._edge_authorised():
                self._stream_auth(client_header="X-Edge-Client-IP")
            else:
                self._reply(403, b"", {"X-Reason": "edge_unauthorized"})
        elif url.path == "/__control/polls":
            with STATE.lock:
                polls = list(STATE.polls)
            self._json({"polls": polls})
        elif url.path.startswith("/origin/"):
            self._origin(url.path, head=False)
        elif url.path == "/__control/calls":
            session = query.get("session", [""])[0]
            with STATE.lock:
                calls = list(STATE.calls.get(session, []))
            self._json({"count": len(calls), "last": calls[-1] if calls else None})
        elif url.path == "/__control/origin":
            with STATE.lock:
                requests = list(STATE.origin)
            self._json({"requests": requests})
        elif url.path == "/__control/relay":
            with STATE.lock:
                relayed = list(STATE.relay)
            self._json({"requests": relayed})
        elif url.path == "/__control/health":
            self._reply(200, b"ok\n")
        else:
            self._reply(404)

    def _poll(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(min(length, 65536)) if length else b""
        if not self._edge_authorised():
            self._reply(401, b"", {"WWW-Authenticate": "Bearer"})
            return
        with STATE.lock:
            STATE.polls.append(
                {
                    "headers": {name.lower(): value for name, value in self.headers.items()},
                    "body": json.loads(body or b"{}"),
                }
            )
            keys = KEYS
        answer = {
            "edge_id": EDGE_ID,
            "keys": keys,
            "origin": S3_CREDS,
            "origin_url": "",
            "cdn_origin_secret": CDN_SECRET or None,
        }
        self._reply(
            200,
            json.dumps(answer).encode(),
            {"Content-Type": "application/json", "Cache-Control": "no-store"},
        )

    def do_POST(self) -> None:
        global KEYS  # noqa: PLW0603 - the control plane's current key set
        url = urlsplit(self.path)
        if url.path == "/edge/v1/poll":
            self._poll()
            return
        if url.path == "/__control/keys":
            length = int(self.headers.get("Content-Length", "0") or 0)
            new_keys = json.loads(self.rfile.read(length))
            with STATE.lock:
                KEYS = new_keys
            self._reply(204)
            return
        session = parse_qs(url.query).get("session", [""])[0]
        with STATE.lock:
            if url.path == "/__control/kick":
                STATE.kicked.add(session)
            elif url.path == "/__control/unkick":
                STATE.kicked.discard(session)
            elif url.path != "/__control/reset":
                self._reply(404)
                return
        if url.path == "/__control/reset":
            STATE.reset()
        self._reply(204)

    def _stream_auth(self, client_header: str = "X-Real-IP") -> None:
        original = self.headers.get("X-Original-URI", "")
        session = session_of(original)
        record = {name.lower(): value for name, value in self.headers.items()}
        with STATE.lock:
            STATE.calls.setdefault(session, []).append(record)
            kicked = session in STATE.kicked
        if not session or not self.headers.get(client_header):
            self._reply(403, b"", {"X-Reason": "bad_request"})
        elif kicked:
            # Cacheable-looking headers on a denial: the edge must still not cache it.
            self._reply(
                403,
                b'{"code":"SESSION_KICKED"}',
                {
                    "X-Reason": "kicked",
                    "Cache-Control": "public, max-age=3600",
                    "X-Accel-Expires": "3600",
                    "Content-Type": "application/json",
                },
            )
        else:
            # Uncacheable-looking headers on an allow: the edge must still cache it.
            self._reply(
                204,
                headers={
                    "Cache-Control": "no-store, private",
                    "Set-Cookie": "sessionid=stub; HttpOnly",
                    "Vary": "*",
                },
            )

    def _relay(self) -> None:
        live_object = self.headers.get("X-Live-Object", "")
        record = {
            "path": self.path,
            "headers": {name.lower(): value for name, value in self.headers.items()},
        }
        with STATE.lock:
            STATE.relay.append(record)
        self._reply(
            200,
            f"relay {live_object}".encode(),
            {
                "Content-Type": "video/mp2t",
                "Cache-Control": "public, max-age=3600",
                "Set-Cookie": "relay=stub",
            },
        )

    def _origin(self, path: str, *, head: bool) -> None:
        key = unquote(path[len("/origin/") :])
        signed = presign_ok(path, urlsplit(self.path).query, self.headers.get("Host", ""))
        record = {
            "path": path,
            "method": self.command,
            "headers": {name.lower(): value for name, value in self.headers.items()},
            "signed": signed and S3_CREDS is not None,
            "query": bool(urlsplit(self.path).query),
        }
        with STATE.lock:
            STATE.origin.append(record)
        xml = {"Content-Type": "application/xml", "x-amz-request-id": "stub-4442587FB7D0A2F9"}
        if not signed:
            self._reply(403, s3_error("AccessDenied", key), xml, head=head)
            return
        segments = key.split("/")
        if "denied" in segments:
            self._reply(403, s3_error("AccessDenied", key), xml, head=head)
            return
        if "broken" in segments:
            self._reply(500, s3_error("InternalError", key), xml, head=head)
            return
        file = None if MEDIA is None else (MEDIA / key).resolve()
        if file is None or MEDIA is None or not file.is_relative_to(MEDIA) or not file.is_file():
            self._reply(404, s3_error("NoSuchKey", key), xml, head=head)
            return
        data = file.read_bytes()
        size = len(data)
        headers = {
            "Content-Type": mimetypes.guess_type(file.name)[0] or "application/octet-stream",
            "ETag": '"' + hashlib.md5(data, usedforsecurity=False).hexdigest() + '"',
            "Last-Modified": email.utils.formatdate(file.stat().st_mtime, usegmt=True),
            "Accept-Ranges": "bytes",
            "Cache-Control": "private, no-store",
            "Access-Control-Allow-Origin": "*",
            "Set-Cookie": "AWSALB=stub-origin-cookie",
            "x-amz-request-id": "stub-4442587FB7D0A2F9",
            "x-amz-meta-source-path": "/srv/library/movies/Some Film (2024)/film.mkv",
        }
        match = RANGE.match(self.headers.get("Range", ""))
        if match is None:
            self._reply(200, data, headers, head=head)
            return
        first, last = match.groups()
        if first:
            start = int(first)
            end = min(int(last), size - 1) if last else size - 1
        elif last:
            start, end = max(size - int(last), 0), size - 1
        else:
            start, end = size, size - 1
        if start >= size or start > end:
            headers = {**xml, "Content-Range": f"bytes */{size}"}
            self._reply(416, s3_error("InvalidRange", key), headers, head=head)
            return
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        self._reply(206, data[start : end + 1], headers, head=head)


def main() -> int:
    # Set once at start-up, before the server threads run.
    global MEDIA, S3_CREDS, KEYS, EDGE_SECRET, CDN_SECRET  # noqa: PLW0603
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--media", type=Path, help="directory served under /origin/")
    parser.add_argument(
        "--s3-creds", type=Path, help="the origin's key (JSON): reads must be signed"
    )
    parser.add_argument("--keys", type=Path, help="the key set the control plane hands out")
    parser.add_argument("--edge-secret", type=Path, help="the standalone edge's bearer secret")
    parser.add_argument("--cdn-secret", type=Path, help="the CDN origin secret handed out (a file)")
    parser.add_argument("--tls-port", type=int, default=0)
    parser.add_argument("--tls-cert", type=Path)
    parser.add_argument("--tls-key", type=Path)
    args = parser.parse_args()
    MEDIA = None if args.media is None else args.media.resolve()
    if args.s3_creds is not None:
        S3_CREDS = json.loads(args.s3_creds.read_text())
    if args.keys is not None:
        KEYS = json.loads(args.keys.read_text())
    if args.edge_secret is not None:
        EDGE_SECRET = args.edge_secret.read_text().strip()
    if args.cdn_secret is not None:
        CDN_SECRET = args.cdn_secret.read_text().strip()
    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    server.daemon_threads = True
    if args.tls_port:
        secure = ThreadingHTTPServer(("0.0.0.0", args.tls_port), Handler)
        secure.daemon_threads = True
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(args.tls_cert, args.tls_key)
        secure.socket = context.wrap_socket(secure.socket, server_side=True)
        threading.Thread(target=secure.serve_forever, daemon=True).start()
        sys.stdout.write(f"auth stub listening on :{args.tls_port} (TLS)\n")
    sys.stdout.write(f"auth stub listening on :{args.port}\n")
    sys.stdout.flush()
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
