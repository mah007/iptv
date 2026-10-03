"""The TMDB client against a fake API: auth, parameters, retries, caching, secrecy."""

import email.utils
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from apps.metadata.tmdb import (
    IMAGE_LANGUAGES,
    MOVIE_APPEND,
    TV_APPEND,
    InMemoryResponseCache,
    TMDBAuthError,
    TMDBClient,
    TMDBError,
    TMDBNotFoundError,
    TMDBRateLimitedError,
    TMDBRetryableError,
    TMDBServerError,
    TMDBUnavailableError,
    cache_key,
    image_url,
)

SECRET = "SECRET-api-key-0123456789"  # noqa: S105 (a fake key)
type Reply = tuple[int, Any, dict[str, str]] | Exception


class FakeTMDB:
    """Answers each request with the next reply (the last one repeats) and records it."""

    def __init__(self, *replies: Reply) -> None:
        self.replies = list(replies) or [(200, {"id": 1}, {})]
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        status, body, headers = reply
        if isinstance(body, bytes):
            return httpx.Response(status, content=body, headers=headers)
        return httpx.Response(status, json=body, headers=headers)


class CountingLimiter:
    def __init__(self) -> None:
        self.calls = 0

    def acquire(self) -> None:
        self.calls += 1


def ok(body: Any = None) -> Reply:
    return (200, {"id": 603} if body is None else body, {})


def status(code: int, headers: dict[str, str] | None = None) -> Reply:
    return (code, {"status_code": 0, "status_message": "x"}, headers or {})


def make_client(fake: FakeTMDB, sleeps: list[float] | None = None, **options: Any) -> TMDBClient:
    sleep: Callable[[float], None] = (sleeps if sleeps is not None else []).append
    options.setdefault("api_key", SECRET)
    return TMDBClient(transport=httpx.MockTransport(fake), sleep=sleep, **options)


def test_api_key_is_a_query_parameter_and_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    fake = FakeTMDB(ok())
    with make_client(fake) as client:
        assert client.movie_details(603) == {"id": 603}
    request = fake.requests[0]
    assert request.url.params["api_key"] == SECRET
    assert "Authorization" not in request.headers
    assert "api_key=REDACTED" in caplog.text  # httpx logged the request, masked
    assert SECRET not in caplog.text


def test_errors_and_retry_logs_never_contain_the_key(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    fake = FakeTMDB(status(500))
    with make_client(fake) as client, pytest.raises(TMDBServerError) as raised:
        client.search_movie("The Matrix", year=1999)
    assert SECRET not in str(raised.value)
    assert raised.value.path == "/search/movie"
    assert raised.value.status_code == 500
    assert "retry" in caplog.text
    assert SECRET not in caplog.text


def test_bearer_token_goes_in_the_authorization_header() -> None:
    fake = FakeTMDB(ok())
    with make_client(fake, api_key=None, bearer_token="token-abc") as client:  # noqa: S106
        client.configuration()
    request = fake.requests[0]
    assert request.headers["Authorization"] == "Bearer token-abc"
    assert "api_key" not in request.url.params
    assert request.headers["Accept"] == "application/json"


def test_a_credential_or_a_transport_is_required() -> None:
    with pytest.raises(ValueError, match="API key"):
        TMDBClient()


def test_details_append_what_the_enricher_needs() -> None:
    fake = FakeTMDB(ok())
    with make_client(fake) as client:
        client.movie_details(603)
        client.tv_details(1396, language="ar-SA")
        client.tv_season(1396, 2)
    movie, tv, season = (request.url for request in fake.requests)
    assert movie.path == "/3/movie/603"
    assert movie.params["append_to_response"] == ",".join(MOVIE_APPEND)
    assert "release_dates" in movie.params["append_to_response"]
    assert movie.params["include_image_language"] == ",".join(IMAGE_LANGUAGES)
    assert movie.params["language"] == "en-US"
    assert tv.path == "/3/tv/1396"
    assert tv.params["append_to_response"] == ",".join(TV_APPEND)
    assert tv.params["language"] == "ar-SA"
    assert season.path == "/3/tv/1396/season/2"
    assert "append_to_response" not in season.params


def test_search_parameters() -> None:
    fake = FakeTMDB(ok({"results": []}))
    with make_client(fake) as client:
        client.search_movie("The Matrix", year=1999)
        client.search_movie("Inception")
        client.search_tv("Breaking Bad", first_air_date_year=2008)
        client.find("tt0133093", "imdb_id")
        client.movie_genres(language="ar-SA")
    movie, no_year, tv, find, genres = (dict(request.url.params) for request in fake.requests)
    assert movie == {
        "language": "en-US",
        "query": "The Matrix",
        "year": "1999",
        "page": "1",
        "include_adult": "false",
        "api_key": SECRET,
    }
    assert "year" not in no_year
    assert tv["first_air_date_year"] == "2008"
    assert find["external_source"] == "imdb_id"
    assert fake.requests[3].url.path == "/3/find/tt0133093"
    assert genres["language"] == "ar-SA"
    assert fake.requests[4].url.path == "/3/genre/movie/list"


def test_find_rejects_unsafe_ids() -> None:
    with make_client(FakeTMDB()) as client, pytest.raises(ValueError, match="alphanumeric"):
        client.find("../movie/603", "imdb_id")


def test_server_errors_are_retried_with_jittered_backoff() -> None:
    fake = FakeTMDB(status(500), status(502), ok())
    sleeps: list[float] = []
    with make_client(fake, sleeps, max_backoff_s=4.0) as client:
        assert client.movie_details(603) == {"id": 603}
    assert len(fake.requests) == 3
    assert len(sleeps) == 2
    assert all(0 <= pause <= 4.0 for pause in sleeps)


def test_429_waits_for_retry_after_seconds() -> None:
    fake = FakeTMDB(status(429, {"Retry-After": "3"}), ok())
    sleeps: list[float] = []
    with make_client(fake, sleeps) as client:
        client.movie_details(603)
    assert len(sleeps) == 1
    assert 3.0 <= sleeps[0] <= 4.0  # Retry-After plus up to 1 s of jitter


def test_429_retry_after_may_be_an_http_date() -> None:
    when = email.utils.format_datetime(datetime.now(UTC) + timedelta(seconds=5), usegmt=True)
    fake = FakeTMDB(status(429, {"Retry-After": when}), ok())
    sleeps: list[float] = []
    with make_client(fake, sleeps) as client:
        client.movie_details(603)
    assert 3.0 <= sleeps[0] <= 6.0


def test_429_without_retry_after_uses_backoff() -> None:
    fake = FakeTMDB(status(429), ok())
    sleeps: list[float] = []
    with make_client(fake, sleeps, max_backoff_s=2.0) as client:
        client.movie_details(603)
    assert len(sleeps) == 1
    assert 0 <= sleeps[0] <= 2.0


def test_a_long_retry_after_is_handed_back_not_waited_for() -> None:
    fake = FakeTMDB(status(429, {"Retry-After": "600"}))
    sleeps: list[float] = []
    with make_client(fake, sleeps) as client, pytest.raises(TMDBRateLimitedError) as raised:
        client.movie_details(603)
    assert raised.value.retry_after == 600.0
    assert len(fake.requests) == 1
    assert sleeps == []


def test_retries_stop_after_max_attempts() -> None:
    fake = FakeTMDB(status(503))
    sleeps: list[float] = []
    with make_client(fake, sleeps, max_attempts=4) as client, pytest.raises(TMDBServerError):
        client.tv_details(1396)
    assert len(fake.requests) == 4
    assert len(sleeps) == 3


def test_network_errors_are_retried() -> None:
    fake = FakeTMDB(httpx.ConnectError("refused"), httpx.ReadTimeout("slow"), ok())
    with make_client(fake) as client:
        assert client.movie_details(603) == {"id": 603}
    fake = FakeTMDB(httpx.ConnectTimeout("slow"))
    with make_client(fake, max_attempts=2) as client, pytest.raises(TMDBUnavailableError):
        client.movie_details(603)
    assert len(fake.requests) == 2


@pytest.mark.parametrize(
    ("code", "error"),
    [(404, TMDBNotFoundError), (401, TMDBAuthError), (403, TMDBAuthError), (400, TMDBError)],
)
def test_client_errors_are_not_retried(code: int, error: type[TMDBError]) -> None:
    fake = FakeTMDB(status(code))
    with make_client(fake) as client, pytest.raises(error) as raised:
        client.movie_details(603)
    assert not isinstance(raised.value, TMDBRetryableError)
    assert raised.value.status_code == code
    assert len(fake.requests) == 1


@pytest.mark.parametrize("body", [b"<html>not json</html>", b"[1, 2]"])
def test_responses_must_be_json_objects(body: bytes) -> None:
    with make_client(FakeTMDB((200, body, {}))) as client, pytest.raises(TMDBError):
        client.configuration()


def test_responses_are_cached_by_url_without_the_credentials() -> None:
    cache = InMemoryResponseCache()
    fake = FakeTMDB(ok())
    with make_client(fake, cache=cache) as client:
        client.movie_details(603)
        client.movie_details(603)
        client.movie_details(603, language="ar-SA")
        client.movie_details(603, append=())
    assert len(fake.requests) == 3  # the repeat was a cache hit
    other_key = FakeTMDB(ok())
    with make_client(other_key, api_key="another-key", cache=cache) as client:
        client.movie_details(603)
    assert other_key.requests == []
    with make_client(other_key, api_key="another-key", cache=cache) as client:
        client.get("/movie/603", {"append_to_response": ",".join(MOVIE_APPEND)}, use_cache=False)
    assert len(other_key.requests) == 1


def test_failures_are_not_cached() -> None:
    cache = InMemoryResponseCache()
    fake = FakeTMDB(status(404), ok())
    with make_client(fake, cache=cache) as client:
        with pytest.raises(TMDBNotFoundError):
            client.movie_details(603)
        assert client.movie_details(603) == {"id": 603}
    assert len(fake.requests) == 2
    assert len(cache) == 1


def test_the_rate_limiter_gates_every_attempt_but_not_cache_hits() -> None:
    limiter = CountingLimiter()
    fake = FakeTMDB(status(500), ok())
    with make_client(fake, rate_limiter=limiter, cache=InMemoryResponseCache()) as client:
        client.movie_details(603)
        client.movie_details(603)
    assert limiter.calls == 2


def test_cache_key_ignores_credentials_and_parameter_order() -> None:
    first = cache_key("/movie/603", {"language": "en-US", "api_key": "one", "page": "1"})
    second = cache_key("/movie/603", {"page": "1", "language": "en-US", "api_key": "two"})
    assert first == second
    assert first != cache_key("/movie/603", {"page": "2", "language": "en-US"})
    assert "one" not in first


def test_in_memory_cache_expires_and_evicts() -> None:
    now = [0.0]
    cache = InMemoryResponseCache(max_entries=2, clock=lambda: now[0])
    cache.set("a", b"1", ttl_s=10)
    cache.set("b", b"2", ttl_s=10)
    assert cache.get("a") == b"1"  # "a" is now the most recently used
    cache.set("c", b"3", ttl_s=10)
    assert cache.get("b") is None
    assert cache.get("a") == b"1"
    now[0] = 11.0
    assert cache.get("a") is None
    assert cache.get("missing") is None


def test_image_url() -> None:
    assert image_url("/abc.jpg", "w500") == "https://image.tmdb.org/t/p/w500/abc.jpg"
    assert image_url("abc.png") == "https://image.tmdb.org/t/p/original/abc.png"
