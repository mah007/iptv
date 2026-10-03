"""Record live TMDB responses as fixtures (needs a TMDB key or read-access token).

`RecordingTransport` wraps a real transport and saves every successful API
response where `FixtureTransport` will look for it, marked as recorded rather
than synthetic. `record_sample_fixtures` re-records the sample titles that
`make sample-media` names. Recorded files hold TMDB data and fall under the
TMDB API terms: keep them out of public repositories. Run it with:

    uv run python manage.py shell -c "from apps.metadata.tmdb.recording import \
record_sample_fixtures as r; r(bearer_token='…')"

Synthetic entries (such as the made-up series "Show") are kept: searches are
merged into `search/{kind}.json`, other responses replace their file.
"""

import json
import os
import tempfile
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal

import httpx

from apps.search.normalize import normalize

from .client import TMDBClient
from .fixtures import (
    API_HOST,
    DEFAULT_FIXTURES_DIR,
    api_relpath,
    fixture_path,
    search_fixture_path,
)

__all__ = [
    "SAMPLE_TITLES",
    "RecordingTransport",
    "SampleTitle",
    "record_sample_fixtures",
    "save_response",
]

ATTRIBUTION: Final = "This product uses the TMDB API but is not endorsed or certified by TMDB."


@dataclass(frozen=True, slots=True)
class SampleTitle:
    kind: Literal["movie", "tv"]
    query: str
    year: int | None
    tmdb_id: int
    seasons: tuple[int, ...] = ()


#: The titles `scripts/sample_media.sh` generates files for (the series "Show" is synthetic).
SAMPLE_TITLES: Final = (
    SampleTitle("movie", "The Matrix", 1999, 603),
    SampleTitle("movie", "Inception", 2010, 27205),
    SampleTitle("movie", "Blade Runner 2049", 2017, 335984),
    SampleTitle("movie", "وجدة", 2012, 129112),
    SampleTitle("tv", "Breaking Bad", None, 1396, seasons=(1, 2)),
)


class RecordingTransport(httpx.BaseTransport):
    """Send requests through `inner` and save each 200 API response under `root`."""

    def __init__(
        self, inner: httpx.BaseTransport | None = None, root: Path = DEFAULT_FIXTURES_DIR
    ) -> None:
        self._inner = inner or httpx.HTTPTransport()
        self.root = root
        self.saved: list[Path] = []
        self._lock = threading.Lock()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self._inner.handle_request(request)
        if request.url.host == API_HOST and response.status_code == httpx.codes.OK:
            response.read()
            document = json.loads(response.content)
            if isinstance(document, dict):
                with self._lock:
                    self.saved.append(save_response(self.root, request.url, document))
        return response

    def close(self) -> None:
        self._inner.close()


def save_response(root: Path, url: httpx.URL, document: dict[str, Any]) -> Path:
    """Write `document`, the response to `url`, as the fixture `FixtureTransport` serves for it."""
    rel = api_relpath(url.path)
    note = {
        "synthetic": False,
        "source": "TMDB API",
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "attribution": ATTRIBUTION,
    }
    if rel.startswith("search/"):
        path = search_fixture_path(root, rel.removeprefix("search/"))
        existing: dict[str, Any] = (
            json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        )
        queries = existing.get("queries", {})
        queries[normalize(url.params.get("query", ""))] = document
        payload = {"_fixture": existing.get("_fixture", note), "queries": queries}
    else:
        path = fixture_path(root, rel, url.params.get("language"))
        payload = {"_fixture": note, **document}
    _write_json(path, payload)
    return path


def record_sample_fixtures(  # noqa: PLR0913
    *,
    api_key: str | None = None,
    bearer_token: str | None = None,
    root: Path = DEFAULT_FIXTURES_DIR,
    titles: Sequence[SampleTitle] = SAMPLE_TITLES,
    languages: Sequence[str] = ("en-US", "ar-SA"),
    inner: httpx.BaseTransport | None = None,
) -> list[Path]:
    """Re-record configuration, genre lists and the sample titles; return the files written.

    `inner` is the transport that reaches TMDB (the default HTTP transport).
    """
    transport = RecordingTransport(inner, root=root)
    with TMDBClient(api_key=api_key, bearer_token=bearer_token, transport=transport) as client:
        client.configuration()
        for language in languages:
            client.movie_genres(language=language)
            client.tv_genres(language=language)
        for title in titles:
            if title.kind == "movie":
                client.search_movie(title.query, year=title.year)
                client.movie_details(title.tmdb_id)
            else:
                client.search_tv(title.query, first_air_date_year=title.year)
                client.tv_details(title.tmdb_id)
                for season in title.seasons:
                    client.tv_season(title.tmdb_id, season)
    return list(dict.fromkeys(transport.saved))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        Path(temporary).replace(path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
