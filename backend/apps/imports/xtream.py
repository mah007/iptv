"""A guarded Xtream Codes VOD source for the media importer.

The account name and password are always passed as encrypted source secrets.  They are
used only by the importer and are never returned by the API, put in a job, or logged.
The source browser exposes stable opaque paths (category/<id>, movie/<id>/<name>) so a
job can be retried without persisting a stream URL containing those credentials.
"""

import re
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import quote, urlsplit

import httpx

from apps.imports import files
from apps.live import egress

TIMEOUT: Final = httpx.Timeout(30.0, read=120.0)
USER_AGENT: Final = "smart-iptv-importer"
_STREAM_ID = re.compile(r"^[1-9][0-9]{0,19}$")
_CATEGORY_ID = re.compile(r"^[0-9]{1,20}$")
_EXTENSION = re.compile(r"^[a-z0-9]{1,10}$")

# Tests route requests through a transport and never contact a real subscription.
_transport: httpx.BaseTransport | None = None


class XtreamError(Exception):
    def __init__(self, code: str, message: str, *, transient: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.transient = transient


@dataclass(frozen=True, slots=True)
class Movie:
    stream_id: str
    name: str
    extension: str
    category_id: str

    @property
    def file_name(self) -> str:
        return safe_name(self.name, self.extension, self.stream_id)

    @property
    def path(self) -> str:
        return f"movie/{self.stream_id}/{self.file_name}"


def schemes(base_url: str) -> frozenset[str]:
    """Allow HTTP only when the configured provider itself is HTTP; never downgrade HTTPS."""
    if urlsplit(base_url).scheme == "http":
        return frozenset({"http", "https"})
    return frozenset({"https"})


def _client(base_url: str) -> httpx.Client:
    accepted = schemes(base_url)

    def check_scheme(request: httpx.Request) -> None:
        if request.url.scheme not in accepted:
            raise egress.UnsafeDestination("insecure_redirect")

    guarded = egress.guarded_client(
        follow_redirects=True,
        timeout=TIMEOUT,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        transport=_transport,
    )
    guarded.event_hooks["request"].insert(0, check_scheme)
    return guarded


def _failure(response: httpx.Response) -> XtreamError:
    if response.status_code in {401, 403}:
        return XtreamError("auth_failed", "The IPTV server refused the account.")
    if response.status_code in {404, 410}:
        return XtreamError("not_found", "The Xtream API was not found at this address.")
    return XtreamError(
        "unreachable",
        f"The IPTV server answered HTTP {response.status_code}.",
        transient=response.status_code == 429 or response.status_code >= 500,
    )


def request(base_url: str, username: str, password: str, action: str = "", **params: str) -> Any:
    query = {"username": username, "password": password, **params}
    if action:
        query["action"] = action
    try:
        with _client(base_url) as client:
            response = client.get(f"{base_url.rstrip('/')}/player_api.php", params=query)
    except egress.UnsafeDestination as exc:
        raise XtreamError("unsafe_destination", f"Not allowed: {exc.reason}.") from None
    except httpx.TimeoutException:
        raise XtreamError("timeout", "The IPTV server took too long.", transient=True) from None
    except httpx.HTTPError:
        # httpx exception strings contain the request URL, whose query has credentials.
        raise XtreamError(
            "unreachable", "The IPTV server could not be reached.", transient=True
        ) from None
    if not response.is_success:
        raise _failure(response)
    try:
        return response.json()
    except ValueError:
        raise XtreamError("unexpected", "The IPTV server returned an invalid response.") from None


def authenticate(base_url: str, username: str, password: str) -> dict[str, Any]:
    payload = request(base_url, username, password)
    if not isinstance(payload, dict):
        raise XtreamError("unexpected", "The IPTV server returned an invalid account response.")
    info = payload.get("user_info")
    if not isinstance(info, dict) or str(info.get("auth", "0")) != "1":
        raise XtreamError("auth_failed", "The IPTV server refused the account.")
    return info


def categories(base_url: str, username: str, password: str) -> list[dict[str, str]]:
    payload = request(base_url, username, password, "get_vod_categories")
    if not isinstance(payload, list):
        raise XtreamError("unexpected", "The IPTV server returned an invalid category list.")
    result: list[dict[str, str]] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        category_id = str(row.get("category_id") or "")
        name = str(row.get("category_name") or "").strip()
        if _CATEGORY_ID.fullmatch(category_id) and name:
            result.append({"id": category_id, "name": name})
    return result


def _movie(row: Any) -> Movie | None:
    if not isinstance(row, dict):
        return None
    stream_id = str(row.get("stream_id") or "")
    if not _STREAM_ID.fullmatch(stream_id):
        return None
    name = str(row.get("name") or f"Movie {stream_id}").strip()
    extension = str(row.get("container_extension") or "mp4").strip().lower().lstrip(".")
    if not _EXTENSION.fullmatch(extension):
        extension = ""
    category_id = str(row.get("category_id") or "")
    return Movie(stream_id, name, extension, category_id)


def movies(base_url: str, username: str, password: str, *, category_id: str = "") -> list[Movie]:
    params = {"category_id": category_id} if category_id else {}
    payload = request(base_url, username, password, "get_vod_streams", **params)
    if not isinstance(payload, list):
        raise XtreamError("unexpected", "The IPTV server returned an invalid movie list.")
    found = [movie for row in payload if (movie := _movie(row)) is not None]
    if category_id:
        found = [
            movie for movie in found if not movie.category_id or movie.category_id == category_id
        ]
    return found


class Listing:
    """The provider's whole VOD list, fetched at most once per import. A few compatible
    panels omit movie_data from get_vod_info, and a listing can hold tens of thousands
    of movies, so every selected movie shares one copy."""

    def __init__(self, base_url: str, username: str, password: str) -> None:
        self._account = (base_url, username, password)
        self._movies: dict[str, Movie] | None = None

    def get(self, stream_id: str) -> Movie | None:
        if self._movies is None:
            self._movies = {item.stream_id: item for item in movies(*self._account)}
        return self._movies.get(stream_id)


def movie(
    base_url: str, username: str, password: str, stream_id: str, *, listing: Listing | None = None
) -> Movie:
    if not _STREAM_ID.fullmatch(stream_id):
        raise XtreamError("not_found", "The selected movie no longer exists.")
    payload = request(base_url, username, password, "get_vod_info", vod_id=stream_id)
    if isinstance(payload, dict):
        found = _movie(payload.get("movie_data"))
        if found is not None and found.stream_id == stream_id:
            return found
    found = (listing or Listing(base_url, username, password)).get(stream_id)
    if found is None:
        raise XtreamError("not_found", "The selected movie no longer exists.")
    return found


def safe_name(name: str, extension: str, stream_id: str) -> str:
    clean = " ".join(name.replace("/", " ").replace("\\", " ").split()).strip(" .")
    clean = "".join(char for char in clean if ord(char) >= 32 and ord(char) != 127).strip(" .")
    clean = clean or f"Movie {stream_id}"
    suffix = f".{extension}" if extension else ""
    # Cut by bytes, not characters: file systems limit names in bytes, and an Arabic
    # letter takes two. A torn character at the cut is dropped.
    budget = files.MAX_NAME_BYTES - len(suffix.encode())
    clean = clean.encode()[:budget].decode(errors="ignore").rstrip(" .") or f"Movie {stream_id}"
    return f"{clean}{suffix}"


def parse_path(path: str) -> tuple[str, str]:
    parts = path.split("/", 2)
    valid_id = _CATEGORY_ID if parts and parts[0] == "category" else _STREAM_ID
    if len(parts) >= 2 and parts[0] in {"category", "movie"} and valid_id.fullmatch(parts[1]):
        return parts[0], parts[1]
    raise XtreamError("invalid_path", "Choose a category or movie from this source.")


def stream_url(base_url: str, username: str, password: str, item: Movie) -> str:
    user = quote(username, safe="")
    secret = quote(password, safe="")
    return f"{base_url.rstrip('/')}/movie/{user}/{secret}/{item.stream_id}.{item.extension}"


def importable(item: Movie) -> bool:
    return files.file_kind(item.file_name) is not None
