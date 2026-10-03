"""Offline TMDB: an httpx transport that answers from JSON fixtures (fixture mode).

Without a TMDB key the client still works end to end, so the sample media
(`make sample-media`) can be matched, enriched and given artwork offline. The
fixtures under `apps/metadata/fixtures/tmdb/` are hand-authored, synthetic
documents shaped like TMDB v3 responses; each carries a `_fixture` note that
is removed before the response is served. Layout (`{rel}` is the API path
without the `/3/` prefix):

- `{rel}.json`, e.g. `movie/603.json`, `tv/1396/season/1.json`,
  `configuration.json`, `find/tt0133093.json`, `genre/movie/list.json`;
- `{rel}.{language}.json` for a non-default language (`genre/tv/list.ar-SA.json`),
  falling back to `{rel}.json`;
- `search/movie.json` and `search/tv.json`: `{"queries": {normalised query: response}}`.
  A `year`/`primary_release_year` (movies) or `first_air_date_year` (series)
  filter is applied the way TMDB applies it; unknown queries return no results.

Unknown paths get TMDB's 404 document. Image URLs (`image.tmdb.org/t/p/{size}/…`)
are answered with generated placeholder art: fixture image paths end in
`-{width}x{height}.jpg|png`, which gives the original size, and the colours
come from the name, so every run produces the same bytes.
"""

import hashlib
import io
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

import httpx

from apps.search.normalize import normalize

__all__ = [
    "API_HOST",
    "DEFAULT_FIXTURES_DIR",
    "DEFAULT_LANGUAGE",
    "IMAGE_HOST",
    "FixtureTransport",
    "api_relpath",
    "fixture_path",
    "search_fixture_path",
]

API_HOST: Final = "api.themoviedb.org"
IMAGE_HOST: Final = "image.tmdb.org"
DEFAULT_LANGUAGE: Final = "en-US"
DEFAULT_FIXTURES_DIR: Final = Path(__file__).resolve().parents[1] / "fixtures" / "tmdb"

#: Each search kind's date field and the parameters TMDB filters it with.
_SEARCH_YEAR: Final[Mapping[str, tuple[str, tuple[str, ...]]]] = {
    "movie": ("release_date", ("year", "primary_release_year")),
    "tv": ("first_air_date", ("first_air_date_year", "year")),
}
_NOT_FOUND: Final = {
    "success": False,
    "status_code": 34,
    "status_message": "The resource you requested could not be found.",
}
_SAFE_REL: Final = re.compile(r"^[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*$")
_LANGUAGE: Final = re.compile(r"^[a-z]{2}(?:-[A-Z]{2})?$")
_IMAGE_PATH: Final = re.compile(
    r"^/t/p/(?P<size>original|[wh]\d{2,4})/(?P<name>[A-Za-z0-9_-]+)\.(?P<ext>jpg|png)$"
)
_IMAGE_DIMENSIONS: Final = re.compile(r"-(?P<width>\d{2,4})x(?P<height>\d{2,4})$")
_FIXTURE_NOTE_KEY: Final = "_fixture"


def api_relpath(url_path: str) -> str:
    """`/3/tv/1396/season/1` -> `tv/1396/season/1`."""
    return url_path.removeprefix("/3/").strip("/")


def fixture_path(root: Path, rel: str, language: str | None = None) -> Path:
    """Where the fixture for API path `rel` in `language` lives (it may not exist)."""
    if not _SAFE_REL.match(rel):
        msg = f"not a TMDB API path: {rel!r}"
        raise ValueError(msg)
    if language and language != DEFAULT_LANGUAGE:
        if not _LANGUAGE.match(language):
            msg = f"not a language code: {language!r}"
            raise ValueError(msg)
        return root / f"{rel}.{language}.json"
    return root / f"{rel}.json"


def search_fixture_path(root: Path, kind: str) -> Path:
    return root / "search" / f"{kind}.json"


class FixtureTransport(httpx.BaseTransport):
    """Serve TMDB API fixtures and placeholder images from `root`; never touches the network."""

    def __init__(self, root: Path = DEFAULT_FIXTURES_DIR) -> None:
        self.root = root

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.method != "GET":
            return httpx.Response(405, json={"success": False, "status_message": "GET only"})
        if request.url.host == IMAGE_HOST:
            return self._image(request.url.path)
        if request.url.host != API_HOST:
            return httpx.Response(404, json=_NOT_FOUND)
        rel = api_relpath(request.url.path)
        if rel.startswith("search/"):
            return self._search(rel.removeprefix("search/"), request.url.params)
        document = self._document(rel, request.url.params.get("language"))
        if document is None:
            return httpx.Response(404, json=_NOT_FOUND)
        return httpx.Response(200, json=document)

    def _document(self, rel: str, language: str | None) -> dict[str, Any] | None:
        try:
            candidates = [fixture_path(self.root, rel, language), fixture_path(self.root, rel)]
        except ValueError:
            return None
        for path in candidates:
            if path.is_file():
                document = _load(path)
                document.pop(_FIXTURE_NOTE_KEY, None)
                return document
        return None

    def _search(self, kind: str, params: httpx.QueryParams) -> httpx.Response:
        page = _int(params.get("page")) or 1
        path = search_fixture_path(self.root, kind)
        if kind not in _SEARCH_YEAR or not path.is_file():
            return httpx.Response(200, json=_page(page, []))
        queries = _load(path).get("queries", {})
        response = queries.get(normalize(params.get("query", "")))
        if not isinstance(response, dict) or page != 1:
            return httpx.Response(200, json=_page(page, []))
        results = [item for item in response.get("results", []) if isinstance(item, dict)]
        if params.get("include_adult", "false") != "true":
            results = [item for item in results if not item.get("adult")]
        date_field, year_params = _SEARCH_YEAR[kind]
        year = next((params[name] for name in year_params if params.get(name)), None)
        if year:
            results = [item for item in results if str(item.get(date_field, ""))[:4] == year]
        return httpx.Response(200, json=_page(1, results))

    def _image(self, url_path: str) -> httpx.Response:
        match = _IMAGE_PATH.match(url_path)
        dimensions = _IMAGE_DIMENSIONS.search(match["name"]) if match else None
        if match is None or dimensions is None:
            return httpx.Response(404, text="Not Found")
        width, height = int(dimensions["width"]), int(dimensions["height"])
        size = match["size"]
        if size.startswith("w") and int(size[1:]) < width:
            width, height = int(size[1:]), max(1, round(height * int(size[1:]) / width))
        elif size.startswith("h") and int(size[1:]) < height:
            width, height = max(1, round(width * int(size[1:]) / height)), int(size[1:])
        body = _placeholder(match["name"], width, height, match["ext"])
        content_type = "image/png" if match["ext"] == "png" else "image/jpeg"
        return httpx.Response(200, content=body, headers={"Content-Type": content_type})


def _load(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        msg = f"fixture {path.name} is not a JSON object"
        raise TypeError(msg)
    return data


def _page(page: int, results: list[dict[str, Any]]) -> dict[str, Any]:
    return {"page": page, "results": results, "total_pages": 1, "total_results": len(results)}


def _int(value: str | None) -> int | None:
    return int(value) if value and value.isdigit() else None


def _placeholder(name: str, width: int, height: int, extension: str) -> bytes:
    """A vertical two-colour gradient picked from `name` (deterministic)."""
    from PIL import Image  # noqa: PLC0415 (only fixture mode renders images)

    digest = hashlib.sha256(name.encode()).digest()
    top, bottom = digest[0:3], digest[3:6]
    gradient = Image.linear_gradient("L").resize((width, height))
    image = Image.composite(
        Image.new("RGB", (width, height), (bottom[0], bottom[1], bottom[2])),
        Image.new("RGB", (width, height), (top[0], top[1], top[2])),
        gradient,
    )
    buffer = io.BytesIO()
    if extension == "png":
        image.save(buffer, format="PNG", optimize=False)
    else:
        image.save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()
