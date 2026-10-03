"""TMDB v3 client (SPEC §7.2 steps 4-8).

- Auth: a v3 API key (sent as the `api_key` query parameter) or a v4 read-access
  token (sent as a Bearer header; preferred, since it never appears in a URL).
- Every request first takes a token from the injected rate limiter (the Redis
  token bucket shared by all workers), then is retried with jittered
  exponential backoff on 429, 5xx and network errors. A 429's Retry-After is
  honoured; one longer than `max_retry_after_s` is not waited for and raises
  `TMDBRateLimitedError` (with `retry_after`) so a task can reschedule itself.
- Successful responses go to the injected cache for 24 h, keyed by the path and
  parameters without the credentials.
- Fixture mode (`TMDBClient.offline()`) serves the synthetic fixtures through
  `FixtureTransport`: no key, no network, same code path.

The API key never appears in a log line, an exception message or a cache key:
messages name the API path only, and a filter on the `httpx` logger (which logs
every request URL at INFO) masks the `api_key` parameter.
"""

import email.utils
import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
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

from .cache import ResponseCache, cache_key
from .errors import (
    TMDBAuthError,
    TMDBError,
    TMDBNotFoundError,
    TMDBRateLimitedError,
    TMDBRetryableError,
    TMDBServerError,
    TMDBUnavailableError,
)
from .fixtures import DEFAULT_FIXTURES_DIR, DEFAULT_LANGUAGE, FixtureTransport
from .ratelimit import RateLimiter

__all__ = [
    "API_BASE_URL",
    "CACHE_TTL_S",
    "IMAGE_BASE_URL",
    "IMAGE_LANGUAGES",
    "MOVIE_APPEND",
    "TV_APPEND",
    "JSONObject",
    "TMDBClient",
    "image_url",
]

logger = logging.getLogger(__name__)

type JSONObject = dict[str, Any]
type ExternalSource = Literal["imdb_id", "tvdb_id"]

API_BASE_URL: Final = "https://api.themoviedb.org/3"
IMAGE_BASE_URL: Final = "https://image.tmdb.org/t/p/"
CACHE_TTL_S: Final = 24 * 60 * 60
#: Appended to movie details (SPEC §7.2 step 5).
MOVIE_APPEND: Final = (
    "credits",
    "videos",
    "images",
    "release_dates",
    "keywords",
    "alternative_titles",
    "translations",
)
#: The series equivalent: `content_ratings` replaces `release_dates`, and
#: `external_ids` carries the IMDb and TVDB IDs.
TV_APPEND: Final = (
    "credits",
    "videos",
    "images",
    "content_ratings",
    "keywords",
    "alternative_titles",
    "translations",
    "external_ids",
)
#: Image languages kept by appended `images`: English, Arabic and language-less art.
IMAGE_LANGUAGES: Final = ("en", "ar", "null")
USER_AGENT: Final = "SmartIPTV/1.0 (metadata)"


def image_url(file_path: str, size: str = "original", base_url: str = IMAGE_BASE_URL) -> str:
    """`/abc.jpg`, `w500` -> `https://image.tmdb.org/t/p/w500/abc.jpg`."""
    return f"{base_url.rstrip('/')}/{size}/{file_path.lstrip('/')}"


class TMDBClient:
    """Synchronous TMDB v3 client; thread-safe, one per worker process is enough."""

    def __init__(  # noqa: PLR0913 (every collaborator is injected)
        self,
        *,
        api_key: str | None = None,
        bearer_token: str | None = None,
        language: str = DEFAULT_LANGUAGE,
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
        if not api_key and not bearer_token and transport is None:
            msg = "TMDB needs an API key or a read-access token; use TMDBClient.offline() without"
            raise ValueError(msg)
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        self._auth_params: dict[str, str] = {}
        if bearer_token:
            headers["Authorization"] = f"Bearer {bearer_token}"
        elif api_key:
            self._auth_params["api_key"] = api_key
            _install_log_redaction()
        self.language = language
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
        self._http = httpx.Client(
            base_url=base_url,
            headers=headers,
            transport=transport,
            timeout=timeout_s,
            follow_redirects=False,
        )

    @classmethod
    def offline(cls, fixtures_dir: Path | None = None, *, language: str = DEFAULT_LANGUAGE) -> Self:
        """Fixture mode: serve the synthetic fixtures; no key, no network."""
        transport = FixtureTransport(fixtures_dir or DEFAULT_FIXTURES_DIR)
        return cls(transport=transport, language=language)

    @property
    def fixture_mode(self) -> bool:
        return self._fixture_mode

    # --- endpoints ------------------------------------------------------------------

    def configuration(self) -> JSONObject:
        """Image base URLs and sizes (`images.secure_base_url`, `poster_sizes`, …)."""
        return self.get("/configuration")

    def search_movie(
        self,
        query: str,
        *,
        year: int | None = None,
        page: int = 1,
        include_adult: bool = False,
        language: str | None = None,
    ) -> JSONObject:
        params = {"query": query, "year": year, "page": page, "include_adult": include_adult}
        return self.get("/search/movie", params, language=language)

    def search_tv(
        self,
        query: str,
        *,
        first_air_date_year: int | None = None,
        page: int = 1,
        include_adult: bool = False,
        language: str | None = None,
    ) -> JSONObject:
        params = {
            "query": query,
            "first_air_date_year": first_air_date_year,
            "page": page,
            "include_adult": include_adult,
        }
        return self.get("/search/tv", params, language=language)

    def movie_details(
        self, movie_id: int, *, language: str | None = None, append: Sequence[str] = MOVIE_APPEND
    ) -> JSONObject:
        return self.get(f"/movie/{int(movie_id)}", _append_params(append), language=language)

    def tv_details(
        self, tv_id: int, *, language: str | None = None, append: Sequence[str] = TV_APPEND
    ) -> JSONObject:
        return self.get(f"/tv/{int(tv_id)}", _append_params(append), language=language)

    def tv_season(
        self, tv_id: int, season_number: int, *, language: str | None = None
    ) -> JSONObject:
        """A season with all its episodes (names, overviews, air dates, stills, runtimes)."""
        path = f"/tv/{int(tv_id)}/season/{int(season_number)}"
        return self.get(path, language=language)

    def find(self, external_id: str, source: ExternalSource) -> JSONObject:
        """Resolve an IMDb (`tt0133093`) or TVDB (`81189`) ID: `movie_results`, `tv_results`."""
        if not external_id.isalnum():
            msg = "external IDs are alphanumeric"
            raise ValueError(msg)
        return self.get(f"/find/{external_id}", {"external_source": source})

    def movie_genres(self, *, language: str | None = None) -> JSONObject:
        return self.get("/genre/movie/list", language=language)

    def tv_genres(self, *, language: str | None = None) -> JSONObject:
        return self.get("/genre/tv/list", language=language)

    # --- plumbing -------------------------------------------------------------------

    def get(
        self,
        path: str,
        params: Mapping[str, object] | None = None,
        *,
        language: str | None = None,
        use_cache: bool = True,
    ) -> JSONObject:
        """GET an API path (`/movie/603`) and return the JSON object, from the cache if fresh."""
        path = "/" + path.lstrip("/")
        query = _query({"language": language or self.language, **(params or {})})
        key = cache_key(path, query)
        cache = self._cache if use_cache else None
        if cache is not None:
            cached = cache.get(key)
            if cached is not None:
                return _json_object(cached, path)
        retrying = Retrying(
            stop=stop_after_attempt(self._max_attempts) | self._stop_on_long_retry_after,
            wait=self._wait,
            retry=retry_if_exception_type(TMDBRetryableError),
            sleep=self._sleep,
            before_sleep=_log_retry,
            reraise=True,
        )
        body = retrying(self._send, path, query)
        document = _json_object(body, path)
        if cache is not None:
            cache.set(key, body, self._cache_ttl_s)
        return document

    def open_image_client(self, *, timeout_s: float = 30.0) -> httpx.Client:
        """An HTTP client for image.tmdb.org downloads; it uses the fixtures in fixture mode."""
        return httpx.Client(
            transport=self._transport,
            timeout=timeout_s,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
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

    def _send(self, path: str, query: Mapping[str, str]) -> bytes:
        if self._limiter is not None:
            self._limiter.acquire()
        try:
            response = self._http.get(path, params={**query, **self._auth_params})
        except httpx.TimeoutException as exc:
            msg = f"TMDB timed out on {path}"
            raise TMDBUnavailableError(msg, path=path) from exc
        except httpx.TransportError as exc:
            msg = f"TMDB unreachable on {path} ({type(exc).__name__})"
            raise TMDBUnavailableError(msg, path=path) from exc
        status = response.status_code
        if status == httpx.codes.OK:
            return response.content
        msg = f"TMDB answered {status} on {path}"
        if status == httpx.codes.TOO_MANY_REQUESTS:
            retry_after = _retry_after(response.headers.get("Retry-After"))
            raise TMDBRateLimitedError(msg, path=path, retry_after=retry_after)
        if status >= httpx.codes.INTERNAL_SERVER_ERROR:
            raise TMDBServerError(msg, path=path, status_code=status)
        if status == httpx.codes.NOT_FOUND:
            raise TMDBNotFoundError(msg, path=path, status_code=status)
        if status in {httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN}:
            raise TMDBAuthError(msg, path=path, status_code=status)
        raise TMDBError(msg, path=path, status_code=status)

    def _wait(self, state: RetryCallState) -> float:
        error = state.outcome.exception() if state.outcome else None
        if isinstance(error, TMDBRateLimitedError) and error.retry_after is not None:
            return error.retry_after + self._jitter(state)
        return self._backoff(state)


class _StopOnLongRetryAfter(stop_base):
    """Stop retrying when TMDB asks for a longer pause than a worker should block for."""

    def __init__(self, limit_s: float) -> None:
        self._limit_s = limit_s

    def __call__(self, retry_state: RetryCallState) -> bool:
        error = retry_state.outcome.exception() if retry_state.outcome else None
        return (
            isinstance(error, TMDBRateLimitedError)
            and error.retry_after is not None
            and error.retry_after > self._limit_s
        )


def _append_params(append: Sequence[str]) -> dict[str, object]:
    params: dict[str, object] = {}
    if append:
        params["append_to_response"] = ",".join(append)
    if "images" in append:
        params["include_image_language"] = ",".join(IMAGE_LANGUAGES)
    return params


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


def _json_object(body: bytes, path: str) -> JSONObject:
    try:
        document = json.loads(body)
    except ValueError as exc:
        msg = f"TMDB sent invalid JSON on {path}"
        raise TMDBError(msg, path=path) from exc
    if not isinstance(document, dict):
        msg = f"TMDB sent a non-object JSON document on {path}"
        raise TMDBError(msg, path=path)
    return document


def _retry_after(value: str | None) -> float | None:
    """Retry-After in seconds: delta-seconds or an HTTP date; None when absent or unreadable."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


def _log_retry(state: RetryCallState) -> None:
    error = state.outcome.exception() if state.outcome else None
    wait = state.next_action.sleep if state.next_action else 0.0
    logger.warning(
        "%s; retry %d in %.1fs", error or "TMDB request failed", state.attempt_number, wait
    )


class _RedactApiKey(logging.Filter):
    """Mask `api_key` in the request URLs httpx logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(_redact(arg) for arg in record.args)
        return True


def _redact(value: object) -> object:
    if isinstance(value, httpx.URL) and "api_key" in value.params:
        return value.copy_set_param("api_key", "REDACTED")
    return value


def _install_log_redaction() -> None:
    for name in ("httpx", "httpcore"):
        target = logging.getLogger(name)
        if not any(isinstance(f, _RedactApiKey) for f in target.filters):
            target.addFilter(_RedactApiKey())
