"""Record live TheTVDB responses as fixtures (needs a TheTVDB key; ADR-0023).

`RecordingTransport` wraps a real transport and saves every successful API response
where `FixtureTransport` will look for it, marked as recorded rather than synthetic.
`record_series` records the searches and series the tests need. Recorded files are
TheTVDB data under TheTVDB's licence: keep them out of public repositories. Run it with
`manage.py record_tvdb_fixtures` (the key comes from Settings → Integrations).

Synthetic entries are kept: searches are merged into `search/series.json`, other
responses replace their file.
"""

import json
import os
import tempfile
import threading
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import httpx

from apps.search.normalize import normalize

from .client import SeasonType, TVDBClient
from .fixtures import API_HOST, DEFAULT_FIXTURES_DIR, api_relpath, fixture_path

__all__ = ["ATTRIBUTION", "RecordingTransport", "record_series", "save_response"]

ATTRIBUTION: Final = "Metadata provided by TheTVDB (https://thetvdb.com)."


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
        rel = api_relpath(request.url.path)
        if (
            request.url.host == API_HOST
            and request.method == "GET"
            and rel != "login"
            and response.status_code == httpx.codes.OK
        ):
            response.read()
            document = json.loads(response.content)
            if isinstance(document, dict):
                with self._lock:
                    self.saved.append(save_response(self.root, request.url, document))
        return response

    def close(self) -> None:
        self._inner.close()


def save_response(root: Path, url: httpx.URL, document: dict[str, Any]) -> Path:
    """Write `document`, the response to `url`, as the fixture `FixtureTransport` serves."""
    rel = api_relpath(url.path)
    note = {
        "synthetic": False,
        "source": "TheTVDB API v4",
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "attribution": ATTRIBUTION,
    }
    if rel == "search":
        path = root / "search" / "series.json"
        existing: dict[str, Any] = (
            json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        )
        queries = existing.get("queries", {})
        queries[normalize(url.params.get("query", ""))] = document.get("data") or []
        payload: dict[str, Any] = {"_fixture": existing.get("_fixture", note), "queries": queries}
    else:
        path = fixture_path(root, rel)
        payload = {"_fixture": note, **document}
    _write_json(path, payload)
    return path


def record_series(  # noqa: PLR0913
    *,
    api_key: str,
    pin: str | None = None,
    series_ids: Sequence[int] = (),
    queries: Sequence[str] = (),
    season_types: Sequence[SeasonType] = ("official",),
    languages: Sequence[str] = ("eng", "ara"),
    root: Path = DEFAULT_FIXTURES_DIR,
    inner: httpx.BaseTransport | None = None,
) -> list[Path]:
    """Record searches and series (extended record, artworks and episode lists); return
    the files written. `inner` is the transport that reaches TheTVDB."""
    transport = RecordingTransport(inner, root=root)
    with TVDBClient(api_key=api_key, pin=pin, transport=transport) as client:
        for query in queries:
            client.search_series(query)
        for series_id in series_ids:
            client.series_extended(series_id)
            client.series_artworks(series_id)
            for season_type in season_types:
                for language in languages:
                    client.series_episodes(series_id, season_type=season_type, language=language)
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
