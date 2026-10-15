"""TheTVDB v4 client: the TV fallback when TMDB has no confident match (SPEC §1.3, §7.2).

Our own client on httpx, like the TMDB one (no third-party TheTVDB library):

- Sign-in: `POST /login` with the project's API key and, for a subscriber-supported key,
  the subscriber's PIN, gives a bearer token that lasts a month. It is kept in this
  process only (never in Redis, a log or an exception) and replaced after a 401.
- Every request first takes a token from the injected rate limiter (the Redis token
  bucket shared by all workers, `ratelimit:tvdb`), then is retried with jittered
  exponential backoff on 429, 5xx and network errors, honouring a 429's Retry-After.
- Successful responses go to the injected cache, keyed by the path and parameters.
- Fixture mode (`TVDBClient.offline()`) serves the synthetic fixtures through
  `FixtureTransport`: no key, no network, same code path. The tests use it; the live
  service never falls back to it (no key means no TheTVDB fallback at all).

Responses are `{"status": "success", "data": ..., "links": ...}`; the methods return
`data` (and the episode list walks every page).
"""

import json
import logging
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from types import TracebackType
from typing import Any, Final, Literal, Self

import httpx
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random,
    wait_random_exponential,
)
from tenacity.stop import stop_base

from apps.core.metrics import METADATA_API_REQUESTS
from apps.metadata.tmdb.cache import ResponseCache, cache_key
from apps.metadata.tmdb.client import _retry_after
from apps.metadata.tmdb.ratelimit import RateLimiter

from .errors import (
    TVDBAuthError,
    TVDBError,
    TVDBNotFoundError,
    TVDBRateLimitedError,
    TVDBRetryableError,
    TVDBServerError,
    TVDBUnavailableError,
)
from .fixtures import DEFAULT_FIXTURES_DIR, FixtureTransport

__all__ = [
    "API_BASE_URL",
    "ARTWORK_HOST",
    "CACHE_TTL_S",
    "SeasonType",
    "TVDBClient",
]

logger = logging.getLogger(__name__)

type JSONValue = Any
type SeasonType = Literal["official", "dvd", "absolute"]

API_BASE_URL: Final = "https://api4.thetvdb.com/v4"
ARTWORK_HOST: Final = "artworks.thetvdb.com"
CACHE_TTL_S: Final = 24 * 60 * 60
USER_AGENT: Final = "SmartIPTV/1.0 (metadata)"
#: Pages of an episode list read at most (TheTVDB pages hold 500 episodes).
MAX_EPISODE_PAGES: Final = 20
_PROVIDER: Final = "tvdb"


class ArtworkHostError(httpx.RequestError):
    """An artwork request (or a redirect) pointed away from TheTVDB's artwork host."""


def _artwork_host_only(request: httpx.Request) -> None:
    if request.url.scheme != "https" or request.url.host != ARTWORK_HOST:
        msg = "artwork is only fetched from TheTVDB's artwork host"
        raise ArtworkHostError(msg, request=request)


def is_artwork_url(value: str) -> bool:
    """A TheTVDB artwork URL (`https://artworks.thetvdb.com/banners/...`)."""
    try:
        url = httpx.URL(value)
    except (httpx.InvalidURL, TypeError):
        return False
    return url.scheme == "https" and url.host == ARTWORK_HOST


class TVDBClient:
    """Synchronous TheTVDB v4 client; thread-safe, one per worker process is enough."""

    def __init__(  # noqa: PLR0913 (every collaborator is injected)
        self,
        *,
        api_key: str | None = None,
        pin: str | None = None,
        rate_limiter: RateLimiter | None = None,
        cache: ResponseCache | None = None,
        cache_ttl_s: int = CACHE_TTL_S,
        transport: httpx.BaseTransport | None = None,
        base_url: str = API_BASE_URL,
        timeout_s: float = 10.0,
        max_attempts: int = 5,
        max_backoff_s: float = 20.0,
        max_retry_after_s: float = 60.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key and transport is None:
            msg = "TheTVDB needs an API key; use TVDBClient.offline() for the fixtures"
            raise ValueError(msg)
        self._api_key = api_key or "fixture"
        self._pin = pin or ""
        self._limiter = rate_limiter
        self._cache = cache
        self._cache_ttl_s = cache_ttl_s
        self._transport = transport
        self._fixture_mode = isinstance(transport, FixtureTransport)
        self._max_attempts = max_attempts
        self._stop_on_long_retry_after = _StopOnLongRetryAfter(max_retry_after_s)
        self._backoff = wait_random_exponential(multiplier=0.5, max=max_backoff_s)
        self._jitter = wait_random(0, 1)
        self._sleep = sleep
        self._token: str | None = None
        self._token_lock = threading.Lock()
        self._http = httpx.Client(
            base_url=base_url.rstrip("/") + "/",
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            transport=transport,
            timeout=timeout_s,
            follow_redirects=False,
        )

    @classmethod
    def offline(cls, fixtures_dir: Path | None = None) -> Self:
        """Fixture mode: serve the synthetic fixtures; no key, no network."""
        return cls(transport=FixtureTransport(fixtures_dir or DEFAULT_FIXTURES_DIR))

    @property
    def fixture_mode(self) -> bool:
        return self._fixture_mode

    # --- endpoints ------------------------------------------------------------------

    def search_series(self, query: str, *, year: int | None = None, limit: int = 10) -> list[Any]:
        """Series search results (`tvdb_id`, `name`, `year`, `aliases`, `translations`,
        `overviews`, `image_url`, `remote_ids`, …)."""
        params: dict[str, object] = {"query": query, "type": "series", "limit": limit}
        if year:
            params["year"] = year
        data = self.get("search", params)
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    def series_extended(self, series_id: int) -> dict[str, Any]:
        """The series with seasons, genres, characters, artworks, remote ids and the name
        and overview translations (`meta=translations`)."""
        return _object(self.get(f"series/{int(series_id)}/extended", {"meta": "translations"}))

    def series_episodes(
        self,
        series_id: int,
        *,
        season_type: SeasonType = "official",
        language: str = "eng",
        season: int | None = None,
    ) -> list[dict[str, Any]]:
        """Every episode in `season_type` order (`seasonNumber`, `number`, `absoluteNumber`,
        `aired`, `name`, `overview`, `image`, `runtime`), names in `language` when TheTVDB
        has them; one season when `season` is given."""
        episodes: list[dict[str, Any]] = []
        path = f"series/{int(series_id)}/episodes/{season_type}/{_language(language)}"
        for page in range(MAX_EPISODE_PAGES):
            params: dict[str, object] = {"page": page}
            if season is not None:
                params["season"] = season
            body = self._document(path, params)
            data = _object(body.get("data"))
            batch = [e for e in data.get("episodes") or [] if isinstance(e, dict)]
            episodes.extend(batch)
            links = body.get("links")
            if not batch or not (isinstance(links, dict) and links.get("next")):
                break
        return episodes

    def series_artworks(self, series_id: int) -> list[dict[str, Any]]:
        """The series' artworks (`image`, `type`, `language`, `score`, `width`, `height`)."""
        data = _object(self.get(f"series/{int(series_id)}/artworks"))
        return [item for item in data.get("artworks") or [] if isinstance(item, dict)]

    def search_remote_id(self, remote_id: str) -> list[Any]:
        """Records that carry an IMDb or other remote id (`tt0903747`)."""
        if not remote_id.isalnum():
            msg = "remote ids are alphanumeric"
            raise ValueError(msg)
        data = self.get(f"search/remoteid/{remote_id}")
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    # --- plumbing -------------------------------------------------------------------

    def get(
        self, path: str, params: Mapping[str, object] | None = None, *, use_cache: bool = True
    ) -> JSONValue:
        """GET an API path (`series/81189/extended`) and return its `data`."""
        return self._document(path, params, use_cache=use_cache).get("data")

    def _document(
        self, path: str, params: Mapping[str, object] | None = None, *, use_cache: bool = True
    ) -> dict[str, Any]:
        path = path.strip("/")
        query = _query(params or {})
        key = cache_key("/" + path, query)
        cache = self._cache if use_cache else None
        if cache is not None:
            cached = cache.get(key)
            if cached is not None:
                return _envelope(cached, path)
        retrying = Retrying(
            stop=stop_after_attempt(self._max_attempts) | self._stop_on_long_retry_after,
            wait=self._wait,
            retry=retry_if_exception_type(TVDBRetryableError),
            sleep=self._sleep,
            before_sleep=_log_retry,
            reraise=True,
        )
        body = retrying(self._send, path, query)
        document = _envelope(body, path)
        if cache is not None:
            cache.set(key, body, self._cache_ttl_s)
        return document

    def login(self) -> None:
        """Sign in now (the Integrations "Test" and the first request do it)."""
        with self._token_lock:
            self._token = self._sign_in()

    def open_artwork_client(self, *, timeout_s: float = 30.0) -> httpx.Client:
        """An HTTP client for artwork downloads; it uses the fixtures in fixture mode.

        Requests, redirects included, may only go to TheTVDB's artwork host over HTTPS
        (G-09)."""
        return httpx.Client(
            transport=self._transport,
            timeout=timeout_s,
            follow_redirects=True,
            max_redirects=3,
            headers={"User-Agent": USER_AGENT},
            event_hooks={"request": [_artwork_host_only]},
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _bearer(self) -> str:
        with self._token_lock:
            if self._token is None:
                self._token = self._sign_in()
            return self._token

    def _forget_token(self, used: str) -> None:
        with self._token_lock:
            if self._token == used:
                self._token = None

    def _sign_in(self) -> str:
        body: dict[str, str] = {"apikey": self._api_key}
        if self._pin:
            body["pin"] = self._pin
        if self._limiter is not None:
            self._limiter.acquire()
        try:
            response = self._http.post("login", json=body)
        except httpx.TimeoutException as exc:
            METADATA_API_REQUESTS.labels(_PROVIDER, "timeout").inc()
            raise TVDBUnavailableError("TheTVDB timed out on login", path="login") from exc
        except httpx.TransportError as exc:
            METADATA_API_REQUESTS.labels(_PROVIDER, "unreachable").inc()
            msg = f"TheTVDB unreachable on login ({type(exc).__name__})"
            raise TVDBUnavailableError(msg, path="login") from exc
        status = response.status_code
        METADATA_API_REQUESTS.labels(_PROVIDER, str(status)).inc()
        if status in {httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN}:
            msg = "TheTVDB refused the API key (or the PIN)"
            raise TVDBAuthError(msg, path="login", status_code=status)
        if status == httpx.codes.TOO_MANY_REQUESTS:
            retry_after = _retry_after(response.headers.get("Retry-After"))
            msg = "TheTVDB answered 429 on login"
            raise TVDBRateLimitedError(msg, path="login", retry_after=retry_after)
        if status >= httpx.codes.INTERNAL_SERVER_ERROR:
            msg = f"TheTVDB answered {status} on login"
            raise TVDBServerError(msg, path="login", status_code=status)
        if status != httpx.codes.OK:
            msg = f"TheTVDB answered {status} on login"
            raise TVDBError(msg, path="login", status_code=status)
        data = _envelope(response.content, "login").get("data")
        token = data.get("token") if isinstance(data, dict) else None
        if not isinstance(token, str) or not token:
            msg = "TheTVDB sent no token on login"
            raise TVDBError(msg, path="login")
        return token

    def _send(self, path: str, query: Mapping[str, str]) -> bytes:
        token = self._bearer()
        if self._limiter is not None:
            self._limiter.acquire()
        response = self._get(path, query, token)
        if response.status_code == httpx.codes.UNAUTHORIZED:
            # The month is up, or the key changed: sign in again once.
            self._forget_token(token)
            token = self._bearer()
            response = self._get(path, query, token)
        status = response.status_code
        if status == httpx.codes.OK:
            return response.content
        msg = f"TheTVDB answered {status} on {path}"
        if status == httpx.codes.TOO_MANY_REQUESTS:
            retry_after = _retry_after(response.headers.get("Retry-After"))
            raise TVDBRateLimitedError(msg, path=path, retry_after=retry_after)
        if status >= httpx.codes.INTERNAL_SERVER_ERROR:
            raise TVDBServerError(msg, path=path, status_code=status)
        if status == httpx.codes.NOT_FOUND:
            raise TVDBNotFoundError(msg, path=path, status_code=status)
        if status in {httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN}:
            raise TVDBAuthError(msg, path=path, status_code=status)
        raise TVDBError(msg, path=path, status_code=status)

    def _get(self, path: str, query: Mapping[str, str], token: str) -> httpx.Response:
        try:
            response = self._http.get(
                path, params=query, headers={"Authorization": f"Bearer {token}"}
            )
        except httpx.TimeoutException as exc:
            METADATA_API_REQUESTS.labels(_PROVIDER, "timeout").inc()
            msg = f"TheTVDB timed out on {path}"
            raise TVDBUnavailableError(msg, path=path) from exc
        except httpx.TransportError as exc:
            METADATA_API_REQUESTS.labels(_PROVIDER, "unreachable").inc()
            msg = f"TheTVDB unreachable on {path} ({type(exc).__name__})"
            raise TVDBUnavailableError(msg, path=path) from exc
        METADATA_API_REQUESTS.labels(_PROVIDER, str(response.status_code)).inc()
        return response

    def _wait(self, state: RetryCallState) -> float:
        error = state.outcome.exception() if state.outcome else None
        if isinstance(error, TVDBRateLimitedError) and error.retry_after is not None:
            return error.retry_after + self._jitter(state)
        return self._backoff(state)


class _StopOnLongRetryAfter(stop_base):
    """Stop retrying when TheTVDB asks for a longer pause than a worker should block for."""

    def __init__(self, limit_s: float) -> None:
        self._limit_s = limit_s

    def __call__(self, retry_state: RetryCallState) -> bool:
        error = retry_state.outcome.exception() if retry_state.outcome else None
        return (
            isinstance(error, TVDBRateLimitedError)
            and error.retry_after is not None
            and error.retry_after > self._limit_s
        )


def _language(value: str) -> str:
    if not (len(value) == 3 and value.isalpha() and value.islower()):
        msg = "TheTVDB languages are three lowercase letters (eng, ara)"
        raise ValueError(msg)
    return value


def _query(params: Mapping[str, object]) -> dict[str, str]:
    query: dict[str, str] = {}
    for name, value in params.items():
        if value is None:
            continue
        if isinstance(value, bool):
            query[name] = "true" if value else "false"
        else:
            query[name] = str(value)
    return query


def _envelope(body: bytes, path: str) -> dict[str, Any]:
    try:
        document = json.loads(body)
    except ValueError as exc:
        msg = f"TheTVDB sent invalid JSON on {path}"
        raise TVDBError(msg, path=path) from exc
    if not isinstance(document, dict):
        msg = f"TheTVDB sent a non-object JSON document on {path}"
        raise TVDBError(msg, path=path)
    return document


def _object(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _log_retry(state: RetryCallState) -> None:
    error = state.outcome.exception() if state.outcome else None
    wait = state.next_action.sleep if state.next_action else 0.0
    logger.warning(
        "%s; retry %d in %.1fs", error or "TheTVDB request failed", state.attempt_number, wait
    )
