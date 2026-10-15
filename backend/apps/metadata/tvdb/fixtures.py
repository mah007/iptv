"""Offline TheTVDB: an httpx transport that answers from JSON fixtures (fixture mode).

The tests drive the TheTVDB client and the TV fallback through it, with no key and no
network. The fixtures under `apps/metadata/fixtures/tvdb/` are hand-written, synthetic
documents shaped like TheTVDB v4 responses (`{"status": "success", "data": ...}`); each
carries a `_fixture` note that is removed before the response is served. Layout (`{rel}`
is the API path without the `/v4/` prefix):

- `{rel}.json`, e.g. `series/9100001/extended.json`,
  `series/9100001/episodes/official/eng.json`, `series/9100001/artworks.json`,
  `search/remoteid/tt0903747.json`;
- `search/series.json`: `{"queries": {normalised query: [search results]}}`; a `year`
  filter is applied the way TheTVDB applies it, unknown queries return no results.

`POST /login` answers a fixed token. Episode lists honour the `season` filter and come
as one page. Artwork URLs (`artworks.thetvdb.com/banners/...-{width}x{height}.jpg`) are
answered with generated placeholder art, as the TMDB fixtures do.
"""

import json
import re
from pathlib import Path
from typing import Any, Final

import httpx

from apps.metadata.tmdb.fixtures import _placeholder
from apps.search.normalize import normalize

__all__ = [
    "API_HOST",
    "ARTWORK_HOST",
    "DEFAULT_FIXTURES_DIR",
    "FIXTURE_TOKEN",
    "FixtureTransport",
    "api_relpath",
    "fixture_path",
]

API_HOST: Final = "api4.thetvdb.com"
ARTWORK_HOST: Final = "artworks.thetvdb.com"
DEFAULT_FIXTURES_DIR: Final = Path(__file__).resolve().parents[1] / "fixtures" / "tvdb"
#: The token fixture mode signs in with (not a secret: it opens nothing).
FIXTURE_TOKEN: Final = "fixture-mode-token"  # noqa: S105 (opens nothing: fixture mode)

_NOT_FOUND: Final = {"status": "failure", "message": "NotFoundException", "data": None}
_SAFE_REL: Final = re.compile(r"^[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*$")
_ARTWORK_PATH: Final = re.compile(
    r"^/banners/(?:[a-z0-9_-]+/)*(?P<name>[A-Za-z0-9_-]+)\.(?P<ext>jpg|png)$"
)
_IMAGE_DIMENSIONS: Final = re.compile(r"-(?P<width>\d{2,4})x(?P<height>\d{2,4})$")
_FIXTURE_NOTE_KEY: Final = "_fixture"


def api_relpath(url_path: str) -> str:
    """`/v4/series/81189/extended` -> `series/81189/extended`."""
    return url_path.removeprefix("/v4/").strip("/")


def fixture_path(root: Path, rel: str) -> Path:
    """Where the fixture for API path `rel` lives (it may not exist)."""
    if not _SAFE_REL.match(rel):
        msg = f"not a TheTVDB API path: {rel!r}"
        raise ValueError(msg)
    return root / f"{rel}.json"


class FixtureTransport(httpx.BaseTransport):
    """Serve TheTVDB API fixtures and placeholder artwork from `root`; never the network."""

    def __init__(self, root: Path = DEFAULT_FIXTURES_DIR) -> None:
        self.root = root

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == ARTWORK_HOST and request.method == "GET":
            return self._artwork(request.url.path)
        if request.url.host != API_HOST:
            return httpx.Response(404, json=_NOT_FOUND)
        rel = api_relpath(request.url.path)
        if rel == "login":
            return _login(request.method)
        if request.method != "GET":
            return httpx.Response(405, json=_NOT_FOUND)
        if request.headers.get("Authorization") != f"Bearer {FIXTURE_TOKEN}":
            return httpx.Response(401, json={"status": "failure", "message": "Unauthorized"})
        return self._api(rel, request.url.params)

    def _api(self, rel: str, params: httpx.QueryParams) -> httpx.Response:
        if rel == "search":
            return self._search(params)
        document = self._document(rel)
        if document is None:
            return httpx.Response(404, json=_NOT_FOUND)
        if "/episodes/" in rel:
            document = _season_only(document, params.get("season"))
            if (params.get("page") or "0") != "0":
                document = {**document, "data": {**document.get("data", {}), "episodes": []}}
        return httpx.Response(200, json=document)

    def _document(self, rel: str) -> dict[str, Any] | None:
        try:
            path = fixture_path(self.root, rel)
        except ValueError:
            return None
        if not path.is_file():
            return None
        document = _load(path)
        document.pop(_FIXTURE_NOTE_KEY, None)
        return document

    def _search(self, params: httpx.QueryParams) -> httpx.Response:
        empty = {"status": "success", "data": [], "links": {"next": None}}
        path = self.root / "search" / "series.json"
        if params.get("type", "series") != "series" or not path.is_file():
            return httpx.Response(200, json=empty)
        queries = _load(path).get("queries", {})
        results = queries.get(normalize(params.get("query", "")))
        if not isinstance(results, list):
            return httpx.Response(200, json=empty)
        found = [item for item in results if isinstance(item, dict)]
        year = params.get("year")
        if year:
            found = [item for item in found if str(item.get("year", "")) == year]
        return httpx.Response(200, json={**empty, "data": found})

    def _artwork(self, url_path: str) -> httpx.Response:
        match = _ARTWORK_PATH.match(url_path)
        dimensions = _IMAGE_DIMENSIONS.search(match["name"]) if match else None
        if match is None or dimensions is None:
            return httpx.Response(404, text="Not Found")
        width, height = int(dimensions["width"]), int(dimensions["height"])
        body = _placeholder(match["name"], width, height, match["ext"])
        content_type = "image/png" if match["ext"] == "png" else "image/jpeg"
        return httpx.Response(200, content=body, headers={"Content-Type": content_type})


def _login(method: str) -> httpx.Response:
    if method != "POST":
        return httpx.Response(405, json=_NOT_FOUND)
    return httpx.Response(200, json={"status": "success", "data": {"token": FIXTURE_TOKEN}})


def _season_only(document: dict[str, Any], season: str | None) -> dict[str, Any]:
    data = document.get("data")
    if not season or not isinstance(data, dict):
        return document
    episodes = [
        item
        for item in data.get("episodes") or []
        if isinstance(item, dict) and str(item.get("seasonNumber")) == season
    ]
    return {**document, "data": {**data, "episodes": episodes}}


def _load(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        msg = f"fixture {path.name} is not a JSON object"
        raise TypeError(msg)
    return data
