"""The TheTVDB v4 client against a fake API and the offline fixtures (ADR-0023): sign-in
and the bearer token, retries, caching, the rate limiter, paging, secrecy, artwork."""

import json
import logging
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from prometheus_client import REGISTRY

from apps.metadata.tmdb import InMemoryResponseCache, TMDBRateLimitedError, TMDBRetryableError
from apps.metadata.tvdb import (
    TVDBAuthError,
    TVDBClient,
    TVDBError,
    TVDBNotFoundError,
    TVDBRateLimitedError,
    TVDBServerError,
    TVDBUnavailableError,
    is_artwork_url,
)
from apps.metadata.tvdb.client import ArtworkHostError
from apps.metadata.tvdb.fixtures import FIXTURE_TOKEN

API_KEY = "fake-" + "tvdb-" + "key-0123456789abcdef"
PIN = "PIN" + "1234"
TOKEN = "eyJ" + "fake.bearer.token"
type Reply = tuple[int, Any, dict[str, str]] | Exception


class FakeTVDB:
    """Signs in with TOKEN; answers each other request with the next reply (the last one
    repeats) and records every request."""

    def __init__(self, *replies: Reply, login: Reply | None = None) -> None:
        self.replies = list(replies) or [ok({"id": 1})]
        self.login_reply = login or (200, {"status": "success", "data": {"token": TOKEN}}, {})
        self.requests: list[httpx.Request] = []
        self.logins = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("/login"):
            self.logins += 1
            reply = self.login_reply
        else:
            reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        code, body, headers = reply
        return httpx.Response(code, json=body, headers=headers)

    @property
    def api_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if not r.url.path.endswith("/login")]


class CountingLimiter:
    def __init__(self) -> None:
        self.calls = 0

    def acquire(self) -> None:
        self.calls += 1


def ok(data: Any, links: dict[str, Any] | None = None) -> Reply:
    body: dict[str, Any] = {"status": "success", "data": data}
    if links is not None:
        body["links"] = links
    return (200, body, {})


def status(code: int, headers: dict[str, str] | None = None) -> Reply:
    return (code, {"status": "failure", "message": "x"}, headers or {})


def make_client(fake: FakeTVDB, sleeps: list[float] | None = None, **options: Any) -> TVDBClient:
    sleep: Callable[[float], None] = (sleeps if sleeps is not None else []).append
    options.setdefault("api_key", API_KEY)
    return TVDBClient(transport=httpx.MockTransport(fake), sleep=sleep, **options)


def test_signs_in_once_and_sends_the_bearer_token() -> None:
    fake = FakeTVDB(ok({"id": 9100001}))
    with make_client(fake, pin=PIN) as client:
        assert client.series_extended(9100001) == {"id": 9100001}
        client.series_extended(9100001)
    assert fake.logins == 1
    login = fake.requests[0]
    assert login.method == "POST"
    assert login.url == "https://api4.thetvdb.com/v4/login"
    assert json.loads(login.read()) == {"apikey": API_KEY, "pin": PIN}
    for request in fake.api_requests:
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        assert request.url.path == "/v4/series/9100001/extended"
        assert request.url.params["meta"] == "translations"


def test_a_key_without_a_pin_signs_in_with_the_key_alone() -> None:
    fake = FakeTVDB(ok([]))
    with make_client(fake) as client:
        client.search_series("x")
    assert json.loads(fake.requests[0].read()) == {"apikey": API_KEY}


def test_an_expired_token_signs_in_again_once() -> None:
    fake = FakeTVDB(status(401), ok({"id": 7}))
    with make_client(fake) as client:
        assert client.series_extended(7) == {"id": 7}
    assert fake.logins == 2


def test_a_refused_token_after_signing_in_again_is_an_auth_error() -> None:
    fake = FakeTVDB(status(401))
    with make_client(fake) as client, pytest.raises(TVDBAuthError):
        client.series_extended(7)
    assert fake.logins == 2


def test_a_refused_key_is_an_auth_error_that_never_names_the_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    fake = FakeTVDB(login=status(401))
    with make_client(fake, pin=PIN) as client, pytest.raises(TVDBAuthError) as raised:
        client.search_series("x")
    assert API_KEY not in str(raised.value)
    assert PIN not in str(raised.value)
    assert API_KEY not in caplog.text
    assert TOKEN not in caplog.text


def test_the_token_never_reaches_a_message_or_a_log(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    sleeps: list[float] = []
    fake = FakeTVDB(status(500))
    with (
        make_client(fake, sleeps, max_attempts=2) as client,
        pytest.raises(TVDBServerError) as raised,
    ):
        client.series_extended(1)
    assert TOKEN not in str(raised.value)
    assert TOKEN not in caplog.text
    assert "series/1/extended" in str(raised.value)


def test_rate_limits_honour_retry_after_and_are_tmdb_retryable() -> None:
    sleeps: list[float] = []
    fake = FakeTVDB(status(429, {"Retry-After": "3"}), ok({"id": 2}))
    with make_client(fake, sleeps) as client:
        assert client.series_extended(2) == {"id": 2}
    assert len(sleeps) == 1
    assert 3 <= sleeps[0] <= 4
    # The metadata tasks retry TMDB's retryable errors; TheTVDB's are the same kind.
    assert issubclass(TVDBRateLimitedError, TMDBRateLimitedError)
    assert issubclass(TVDBServerError, TMDBRetryableError)


def test_a_long_retry_after_is_not_waited_for() -> None:
    sleeps: list[float] = []
    fake = FakeTVDB(status(429, {"Retry-After": "600"}))
    with make_client(fake, sleeps) as client, pytest.raises(TVDBRateLimitedError) as raised:
        client.series_extended(2)
    assert raised.value.retry_after == 600
    assert sleeps == []


def test_server_errors_and_network_failures_are_retried_then_raised() -> None:
    sleeps: list[float] = []
    fake = FakeTVDB(status(502), httpx.ConnectError("down"), ok({"id": 3}))
    with make_client(fake, sleeps) as client:
        assert client.series_extended(3) == {"id": 3}
    assert len(sleeps) == 2
    fake = FakeTVDB(httpx.ReadTimeout("slow"))
    with make_client(fake, [], max_attempts=3) as client, pytest.raises(TVDBUnavailableError):
        client.series_extended(3)
    assert len(fake.api_requests) == 3


def test_not_found_and_other_statuses_are_not_retried() -> None:
    fake = FakeTVDB(status(404))
    with make_client(fake, []) as client, pytest.raises(TVDBNotFoundError):
        client.series_extended(4)
    assert len(fake.api_requests) == 1
    fake = FakeTVDB(status(400))
    with make_client(fake, []) as client, pytest.raises(TVDBError):
        client.series_extended(4)
    assert len(fake.api_requests) == 1


def test_responses_are_cached_and_the_limiter_counts_requests() -> None:
    limiter = CountingLimiter()
    cache = InMemoryResponseCache()
    fake = FakeTVDB(ok({"id": 5}))
    with make_client(fake, rate_limiter=limiter, cache=cache) as client:
        assert client.series_extended(5) == {"id": 5}
        assert client.series_extended(5) == {"id": 5}
    assert len(fake.api_requests) == 1
    assert len(cache) == 1
    assert limiter.calls == 2  # the sign-in and the one request; the cache hit is free


def test_episodes_walk_every_page() -> None:
    first = ok(
        {"series": {}, "episodes": [{"id": 1, "seasonNumber": 1, "number": 1}]}, {"next": "x"}
    )
    second = ok({"series": {}, "episodes": [{"id": 2, "seasonNumber": 1, "number": 2}]}, {})
    fake = FakeTVDB(first, second)
    with make_client(fake) as client:
        episodes = client.series_episodes(9, season_type="dvd", language="ara", season=1)
    assert [e["id"] for e in episodes] == [1, 2]
    pages = [r.url.params["page"] for r in fake.api_requests]
    assert pages == ["0", "1"]
    assert fake.api_requests[0].url.path == "/v4/series/9/episodes/dvd/ara"
    assert fake.api_requests[0].url.params["season"] == "1"


def test_languages_and_remote_ids_are_checked() -> None:
    with make_client(FakeTVDB()) as client:
        with pytest.raises(ValueError, match="three lowercase"):
            client.series_episodes(1, language="en-US")
        with pytest.raises(ValueError, match="alphanumeric"):
            client.search_remote_id("../x")


def test_requests_are_counted_per_provider() -> None:
    def count(code: str) -> float:
        value = REGISTRY.get_sample_value(
            "iptv_metadata_api_requests_total", {"provider": "tvdb", "status": code}
        )
        return value or 0.0

    before = count("200"), count("404")
    fake = FakeTVDB(status(404))
    with make_client(fake) as client, pytest.raises(TVDBNotFoundError):
        client.series_extended(6)
    assert count("200") == before[0] + 1  # the sign-in
    assert count("404") == before[1] + 1


def test_a_client_needs_a_key_or_a_transport() -> None:
    with pytest.raises(ValueError, match="API key"):
        TVDBClient()


# --- Fixture mode ---------------------------------------------------------------------------------


def test_fixture_mode_searches_and_filters_by_year() -> None:
    with TVDBClient.offline() as client:
        assert client.fixture_mode
        found = client.search_series("The Lantern Keepers")
        assert [item["tvdb_id"] for item in found] == ["9100001", "9100002"]
        assert client.search_series("The Lantern Keepers", year=2020) == []
        assert client.search_series("حراس الفانوس")[0]["tvdb_id"] == "9100001"
        assert client.search_series("nothing like it") == []


def test_fixture_mode_serves_series_episodes_and_artworks() -> None:
    with TVDBClient.offline() as client:
        details = client.series_extended(9100001)
        assert details["name"] == "The Lantern Keepers"
        assert "_fixture" not in details
        season_one = client.series_episodes(9100001, season=1)
        assert [e["number"] for e in season_one] == [1, 2, 3]
        assert len(client.series_episodes(9100001)) == 6
        dvd = client.series_episodes(9100001, season_type="dvd", season=1)
        assert [e["id"] for e in dvd] == [9500102, 9500101, 9500103]
        assert len(client.series_artworks(9100001)) == 4
        with pytest.raises(TVDBNotFoundError):
            client.series_extended(1)
        with pytest.raises(TVDBNotFoundError):
            client.series_episodes(9100001, season_type="dvd", language="ara")


def test_artwork_comes_only_from_the_artwork_host() -> None:
    url = "https://artworks.thetvdb.com/banners/v4/series/9100001/posters/lantern-680x1000.jpg"
    assert is_artwork_url(url)
    assert not is_artwork_url("http://artworks.thetvdb.com/banners/x.jpg")
    assert not is_artwork_url("https://image.tmdb.org/t/p/w185/x.jpg")
    assert not is_artwork_url("not a url")
    with TVDBClient.offline() as client, client.open_artwork_client() as http:
        response = http.get(url)
        assert response.status_code == 200
        assert response.headers["Content-Type"] == "image/jpeg"
        with pytest.raises(ArtworkHostError):
            http.get("https://example.com/x.jpg")


def test_the_fixture_api_refuses_requests_without_its_token() -> None:
    from apps.metadata.tvdb import FixtureTransport  # noqa: PLC0415

    with httpx.Client(transport=FixtureTransport()) as http:
        refused = http.get("https://api4.thetvdb.com/v4/series/9100001/extended")
        assert refused.status_code == 401
        allowed = http.get(
            "https://api4.thetvdb.com/v4/series/9100001/extended",
            headers={"Authorization": f"Bearer {FIXTURE_TOKEN}"},
        )
        assert allowed.status_code == 200
        assert http.get("https://api4.thetvdb.com/v4/login").status_code == 405


# --- Recording ------------------------------------------------------------------------------------


def test_recording_saves_responses_where_fixture_mode_reads_them(tmp_path: Any) -> None:
    from apps.metadata.tvdb import FixtureTransport  # noqa: PLC0415
    from apps.metadata.tvdb.recording import record_series  # noqa: PLC0415

    saved = record_series(
        api_key=API_KEY,
        series_ids=[9100001],
        queries=["The Lantern Keepers"],
        root=tmp_path,
        inner=FixtureTransport(),  # stands in for TheTVDB
    )
    names = {path.relative_to(tmp_path).as_posix() for path in saved}
    assert names == {
        "search/series.json",
        "series/9100001/extended.json",
        "series/9100001/artworks.json",
        "series/9100001/episodes/official/eng.json",
        "series/9100001/episodes/official/ara.json",
    }
    extended = json.loads((tmp_path / "series/9100001/extended.json").read_text())
    assert extended["_fixture"]["synthetic"] is False
    assert "TheTVDB" in extended["_fixture"]["attribution"]
    # The recording replays offline.
    with TVDBClient.offline(tmp_path) as client:
        assert client.search_series("The Lantern Keepers")[0]["tvdb_id"] == "9100001"
        assert client.series_extended(9100001)["name"] == "The Lantern Keepers"


@pytest.mark.django_db
def test_the_record_command_needs_a_key_and_a_target(tmp_path: Any) -> None:
    from django.core.management import CommandError, call_command  # noqa: PLC0415

    from apps.core.services import set_setting  # noqa: PLC0415

    with pytest.raises(CommandError, match="API key"):
        call_command("record_tvdb_fixtures", "--series", "1", "--root", str(tmp_path))
    set_setting("metadata.tvdb_api_key", "abcd" + "1234" * 5, actor=None)
    with pytest.raises(CommandError, match="--series or --query"):
        call_command("record_tvdb_fixtures", "--root", str(tmp_path))
