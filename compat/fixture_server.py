#!/usr/bin/env python3
"""Serve the golden fixtures as an Xtream host. A test double, not a server.

It rehearses validate.py --live and the IPTVnator end-to-end run before the real
API exists (M9), and lets you point an IPTV app at the golden shapes:

    python3 compat/fixture_server.py --port 18080 --media /path/to/sample.mp4
    XC_USER=mah-7k3p9q XC_PASS=example-password \\
        uvx --with 'jsonschema==4.*' python compat/validate.py --live http://127.0.0.1:18080

Credentials are XC_USER/XC_PASS when set, else the login fixture's pair. Play URLs
redirect (302) to --media, served with byte ranges; without it they return 404.
Python standard library only; binds 127.0.0.1 unless --host says otherwise.
"""

from __future__ import annotations

import argparse
import copy
import gzip
import json
import os
import re
import sys
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlsplit

FIXTURES = Path(__file__).resolve().parent / "fixtures"
_PLAY = re.compile(r"/(movie|series|live)/([^/]+)/([^/]+)/([0-9]+)\.([a-z0-9]+)")
_CREDENTIALS = re.compile(
    r"(/(?:movie|series|live|timeshift)/)[^/]+/[^/]+/|((?:username|password)=)[^&\s]*"
)


class Catalog:
    """The fixtures, rewritten for one public URL and credential pair."""

    def __init__(self, fixtures: Path, public_url: str, credentials: tuple[str, str]) -> None:
        self.data = {
            path.stem: json.loads(path.read_text("utf-8")) for path in fixtures.glob("*.json")
        }
        self.m3u = (fixtures / "m3u_plus.m3u").read_text("utf-8")
        self.xmltv = (fixtures / "xmltv.xml").read_bytes()
        self.credentials = credentials
        parts = urlsplit(public_url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise SystemExit(f"--public-url must be an http(s) URL, got {public_url!r}")
        self.public_url = f"{parts.scheme}://{parts.netloc}"
        port = str(parts.port or (443 if parts.scheme == "https" else 80))
        server = self.data["login"]["server_info"]
        server.update(url=parts.hostname, server_protocol=parts.scheme)
        server["https_port" if parts.scheme == "https" else "port"] = port
        login = self.data["login"]["user_info"]
        self.fixture_credentials = (login["username"], login["password"])

    def login(self) -> Any:
        payload = copy.deepcopy(self.data["login"])
        payload["user_info"]["username"], payload["user_info"]["password"] = self.credentials
        now = datetime.now(UTC).replace(microsecond=0)
        payload["server_info"]["timestamp_now"] = int(now.timestamp())
        payload["server_info"]["time_now"] = now.strftime("%Y-%m-%d %H:%M:%S")
        return payload

    def listing(self, action: str, category_id: str | None) -> Any:
        items = copy.deepcopy(self.data[action])
        if category_id is not None:
            wanted = int(category_id) if category_id.isdigit() else -1
            items = [item for item in items if wanted in item["category_ids"]]
        for number, item in enumerate(items, start=1):
            item["num"] = number
        return items

    def vod_info(self, vod_id: str) -> Any:
        golden = self.data["get_vod_info"]
        if vod_id == str(golden["movie_data"]["stream_id"]):
            return golden
        stream = next(
            (s for s in self.data["get_vod_streams"] if str(s["stream_id"]) == vod_id), None
        )
        if stream is None:
            return None
        # Another listed movie: the golden shape with that movie's list values.
        info = dict(golden["info"])
        for key in (
            "plot",
            "description",
            "cast",
            "actors",
            "director",
            "genre",
            "country",
            "age",
            "release_date",
            "releasedate",
            "year",
            "imdb_id",
            "youtube_trailer",
        ):
            info[key] = ""
        info.update(
            name=stream["name"],
            o_name=stream["name"],
            cover_big=stream["stream_icon"],
            movie_image=stream["stream_icon"],
            backdrop_path=[],
            rating=stream["rating"],
            rating_5based=stream["rating_5based"],
            tmdb_id=stream["tmdb_id"],
        )
        movie_data = {key: stream[key] for key in golden["movie_data"]}
        return {"info": info, "movie_data": movie_data, **info}

    def series_info(self, series_id: str) -> Any:
        first = self.data["get_series"][0]
        return self.data["get_series_info"] if series_id == str(first["series_id"]) else None

    def epg(self, action: str, stream_id: str, limit: str | None) -> Any:
        listings = copy.deepcopy(self.data[action]["epg_listings"])
        channel = next(
            (s for s in self.data["get_live_streams"] if str(s["stream_id"]) == stream_id), None
        )
        if (
            channel is None
            or not listings
            or listings[0]["channel_id"] != channel["epg_channel_id"]
        ):
            listings = []
        if limit and limit.isdigit():
            listings = listings[: int(limit)]
        return {"epg_listings": listings}

    def playlist(self, output: str) -> str:
        text = self.m3u
        fixture_user, fixture_password = (
            quote(value, safe="") for value in self.fixture_credentials
        )
        user, password = (quote(value, safe="") for value in self.credentials)
        text = text.replace(f"/{fixture_user}/{fixture_password}/", f"/{user}/{password}/")
        text = text.replace(
            f"username={fixture_user}&password={fixture_password}",
            f"username={user}&password={password}",
        )
        text = text.replace("https://tv.example.com", self.public_url)
        if output == "m3u8":
            text = re.sub(r"(/live/[^\n]+/[0-9]+)\.ts\n", r"\1.m3u8\n", text)
        return text

    def known_stream(self, kind: str, xc_id: str) -> bool:
        if kind == "movie":
            return any(str(s["stream_id"]) == xc_id for s in self.data["get_vod_streams"])
        if kind == "series":
            episodes = self.data["get_series_info"]["episodes"].values()
            return any(episode["id"] == xc_id for season in episodes for episode in season)
        return False


class Handler(BaseHTTPRequestHandler):
    server_version = "fixture-server/1"
    catalog: Catalog
    media: Path | None

    # -- plumbing

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write(_CREDENTIALS.sub(_redacted, format % args) + "\n")

    def _send(
        self, status: int, body: bytes, content_type: str, *, compress: bool = True, **headers: str
    ) -> None:
        accepts_gzip = "gzip" in self.headers.get("Accept-Encoding", "")
        if compress and accepts_gzip and len(body) > 256:
            body = gzip.compress(body)
            headers["Content-Encoding"] = "gzip"
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in headers.items():
            self.send_header(name.replace("_", "-"), value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        self._send(status, body, "application/json")

    def _fields(self) -> dict[str, str]:
        fields = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
        if self.command == "POST":
            length = int(self.headers.get("Content-Length", "0") or 0)
            body = self.rfile.read(length).decode("utf-8", "replace")
            fields.update({k: v[0] for k, v in parse_qs(body).items()})
        return fields

    def _authorized(self, fields: dict[str, str]) -> bool:
        return (fields.get("username"), fields.get("password")) == self.catalog.credentials

    # -- routes

    def do_GET(self) -> None:
        self._route()

    def do_POST(self) -> None:
        self._route()

    def do_HEAD(self) -> None:
        self._route()

    def _route(self) -> None:
        path = urlsplit(self.path).path
        if path == "/player_api.php":
            self._player_api(self._fields())
        elif path == "/get.php":
            self._get_php(self._fields())
        elif path == "/xmltv.php":
            fields = self._fields()
            if not self._authorized(fields):
                self._send(HTTPStatus.FORBIDDEN, b"", "text/plain")
            else:
                self._send(200, self.catalog.xmltv, "application/xml; charset=utf-8")
        elif path == "/media/sample.mp4":
            self._media()
        elif match := _PLAY.fullmatch(path):
            self._play(match)
        else:
            self._send(HTTPStatus.NOT_FOUND, b"not found\n", "text/plain")

    def _player_api(self, fields: dict[str, str]) -> None:
        catalog = self.catalog
        if not self._authorized(fields):
            self._json(catalog.data["auth_failure"])
            return
        action = fields.get("action", "")
        if action in ("get_vod_categories", "get_series_categories", "get_live_categories"):
            self._json(catalog.data[action])
        elif action in ("get_vod_streams", "get_series", "get_live_streams"):
            self._json(catalog.listing(action, fields.get("category_id")))
        elif action == "get_vod_info":
            self._found(catalog.vod_info(fields.get("vod_id", "")))
        elif action == "get_series_info":
            self._found(catalog.series_info(fields.get("series_id", "")))
        elif action in ("get_short_epg", "get_simple_data_table"):
            self._json(catalog.epg(action, fields.get("stream_id", ""), fields.get("limit")))
        else:  # no action, get_account_info or an unknown action
            self._json(catalog.login())

    def _found(self, payload: Any) -> None:
        # What the real server returns for an unknown id is an open M9 question (README).
        self._json(payload if payload is not None else {}, 200 if payload is not None else 404)

    def _get_php(self, fields: dict[str, str]) -> None:
        if not self._authorized(fields):
            self._send(HTTPStatus.FORBIDDEN, b"", "text/plain")
            return
        output = fields.get("output", "ts")
        if fields.get("type") != "m3u_plus" or output not in ("ts", "m3u8", "mp4"):
            self._send(HTTPStatus.BAD_REQUEST, b"type=m3u_plus&output=ts|m3u8|mp4\n", "text/plain")
            return
        body = self.catalog.playlist("m3u8" if output == "m3u8" else "ts").encode("utf-8")
        self._send(
            200,
            body,
            "audio/x-mpegurl; charset=utf-8",
            Content_Disposition='attachment; filename="playlist.m3u"',
        )

    def _play(self, match: re.Match[str]) -> None:
        kind, user, password, xc_id, _ext = match.groups()
        if (unquote(user), unquote(password)) != self.catalog.credentials:
            self._send(HTTPStatus.FORBIDDEN, b"", "text/plain")
        elif self.media is None or not self.catalog.known_stream(kind, xc_id):
            self._send(HTTPStatus.NOT_FOUND, b"", "text/plain")
        else:
            self._send(
                HTTPStatus.FOUND,
                b"",
                "text/plain",
                Location=f"{self.catalog.public_url}/media/sample.mp4",
            )

    def _media(self) -> None:
        if self.media is None:
            self._send(HTTPStatus.NOT_FOUND, b"", "text/plain")
            return
        size = self.media.stat().st_size
        match = re.fullmatch(r"bytes=([0-9]*)-([0-9]*)", self.headers.get("Range", ""))
        status, start, end = HTTPStatus.OK, 0, size - 1
        if match is not None and any(match.groups()):
            first, last = match.groups()
            if first:
                start, end = int(first), min(int(last) if last else size - 1, size - 1)
            else:
                start = max(0, size - int(last))
            if start >= size or start > end:
                self._send(
                    HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE,
                    b"",
                    "video/mp4",
                    compress=False,
                    Content_Range=f"bytes */{size}",
                )
                return
            status = HTTPStatus.PARTIAL_CONTENT
        # Media is never gzipped, whatever Accept-Encoding says.
        self.send_response(status)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        if self.command == "HEAD":
            return
        remaining = end - start + 1
        with self.media.open("rb") as media:
            media.seek(start)
            try:
                while remaining > 0:
                    chunk = media.read(min(remaining, 1 << 16))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                # Players cancel range requests all the time; that is not an error.
                self.close_connection = True


def _redacted(match: re.Match[str]) -> str:
    return f"{match.group(1)}***/***/" if match.group(1) else f"{match.group(2)}***"


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the golden fixtures as an Xtream host.")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (127.0.0.1)")
    parser.add_argument("--port", type=int, default=18080, help="port (18080)")
    parser.add_argument("--public-url", help="the URL clients use, if not http://HOST:PORT")
    parser.add_argument("--media", type=Path, help="an MP4 that every play URL redirects to")
    parser.add_argument("--fixtures", type=Path, default=FIXTURES, help="fixture directory")
    args = parser.parse_args()
    login = json.loads((args.fixtures / "login.json").read_text("utf-8"))["user_info"]
    credentials = (
        os.environ.get("XC_USER") or login["username"],
        os.environ.get("XC_PASS") or login["password"],
    )
    public_url = args.public_url or f"http://{args.host}:{args.port}"
    Handler.catalog = Catalog(args.fixtures, public_url, credentials)
    Handler.media = args.media
    if args.media is not None and not args.media.is_file():
        raise SystemExit(f"--media {args.media} is not a file")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    sys.stderr.write(f"Serving the golden fixtures on {public_url} (Ctrl-C stops)\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
