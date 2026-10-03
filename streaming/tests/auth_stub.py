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

Test control (not part of any contract):
    POST /__control/kick?session=<id>     later checks for that session get 403
    POST /__control/unkick?session=<id>
    POST /__control/reset                 forget kicks and recorded requests
    GET  /__control/calls?session=<id>    {"count": n, "last": {header: value}}
    GET  /__control/origin                {"requests": [{"path", "headers"}, ...]}
    GET  /__control/health
"""

from __future__ import annotations

import argparse
import email.utils
import hashlib
import json
import mimetypes
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

TOKEN_IN_URI = re.compile(r"^/v/([^/?#]+)/")
RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
BUCKET = "smart-iptv-media"


class State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.kicked: set[str] = set()
        self.calls: dict[str, list[dict[str, str]]] = {}
        self.origin: list[dict[str, Any]] = []

    def reset(self) -> None:
        with self.lock:
            self.kicked.clear()
            self.calls.clear()
            self.origin.clear()


STATE = State()
MEDIA: Path | None = None


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

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        if url.path == "/internal/stream-auth":
            self._stream_auth()
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
        elif url.path == "/__control/health":
            self._reply(200, b"ok\n")
        else:
            self._reply(404)

    def do_POST(self) -> None:
        url = urlsplit(self.path)
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

    def _stream_auth(self) -> None:
        original = self.headers.get("X-Original-URI", "")
        session = session_of(original)
        record = {name.lower(): value for name, value in self.headers.items()}
        with STATE.lock:
            STATE.calls.setdefault(session, []).append(record)
            kicked = session in STATE.kicked
        if not session or not self.headers.get("X-Real-IP"):
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

    def _origin(self, path: str, *, head: bool) -> None:
        key = unquote(path[len("/origin/") :])
        record = {
            "path": path,
            "method": self.command,
            "headers": {name.lower(): value for name, value in self.headers.items()},
        }
        with STATE.lock:
            STATE.origin.append(record)
        xml = {"Content-Type": "application/xml", "x-amz-request-id": "stub-4442587FB7D0A2F9"}
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
    global MEDIA  # noqa: PLW0603 - set once at start-up, before the server threads run
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--media", type=Path, help="directory served under /origin/")
    args = parser.parse_args()
    MEDIA = None if args.media is None else args.media.resolve()
    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    server.daemon_threads = True
    sys.stdout.write(f"auth stub listening on :{args.port}\n")
    sys.stdout.flush()
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
