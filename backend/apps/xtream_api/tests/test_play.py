"""Play URLs: authenticate, resolve, start playback, 302 to the edge (SPEC §7.4, §7.5)."""

import pytest
from django.test import Client

from apps.xtream_api.dto import TitleKind
from apps.xtream_api.playback import (
    PLAYBACK_UNAVAILABLE,
    PlayRefused,
    UnavailablePlayback,
    refusal,
    set_playback_starter,
)
from apps.xtream_api.tests.conftest import Subscriber
from apps.xtream_api.tests.fakes import EDGE_URL, FakeStarter, InMemoryCatalogSource

pytestmark = pytest.mark.django_db


def test_movie_redirects_to_the_signed_edge_url(
    tv: Client, subscriber: Subscriber, catalog: InMemoryCatalogSource, starter: FakeStarter
) -> None:
    response = tv.get(subscriber.path("movie", 1001), REMOTE_ADDR="198.51.100.4")
    assert response.status_code == 302
    assert response["Location"] == EDGE_URL
    assert "no-store" in response["Cache-Control"]
    assert subscriber.password not in response["Location"]
    assert response.content == b""
    [request] = starter.requests
    assert (request.user, request.device) == (subscriber.user, subscriber.device)
    assert (request.title.kind, request.title.xc_id) == (TitleKind.MOVIE, 1001)
    assert (request.extension, request.client_ip) == ("mp4", "198.51.100.4")


def test_episode_play_urls_resolve_episode_ids(
    tv: Client, subscriber: Subscriber, catalog: InMemoryCatalogSource, starter: FakeStarter
) -> None:
    response = tv.head(subscriber.path("series", 5003, "MKV"))
    assert response.status_code == 302
    [request] = starter.requests
    assert (request.title.kind, request.title.xc_id, request.extension) == (
        TitleKind.EPISODE,
        5003,
        "mkv",
    )


@pytest.mark.parametrize(
    ("path_kind", "xc_id"),
    [("movie", 424242), ("movie", 5001), ("series", 1001)],
)
def test_unknown_titles_are_404(
    *,
    tv: Client,
    subscriber: Subscriber,
    catalog: InMemoryCatalogSource,
    starter: FakeStarter,
    path_kind: str,
    xc_id: int,
) -> None:
    response = tv.get(subscriber.path(path_kind, xc_id))
    assert (response.status_code, response["X-Reason"]) == (404, "NOT_FOUND")
    assert starter.requests == []


def test_failed_authentication_is_a_plain_404(
    tv: Client, subscriber: Subscriber, catalog: InMemoryCatalogSource, starter: FakeStarter
) -> None:
    wrong = tv.get(f"/movie/{subscriber.username}/wrong-password-1/1001.mp4")
    unknown = tv.get("/movie/nobody-k3p9qa/wrong-password-1/1001.mp4")
    for response in (wrong, unknown):
        assert (response.status_code, response.content) == (404, b"")
        assert "X-Reason" not in response
    assert starter.requests == []


@pytest.mark.parametrize(
    ("code", "retry_after", "status"),
    [
        ("CONCURRENCY_LIMIT", None, 403),
        ("SUBSCRIPTION_EXPIRED", None, 403),
        ("DEVICE_BLOCKED", None, 403),
        ("CATEGORY_NOT_ALLOWED", None, 403),
        ("SOMETHING_NEW", None, 403),
        ("NOT_FOUND", None, 404),
        ("TITLE_PREPARING", 120, 503),
    ],
)
def test_refusals_map_to_statuses(
    *,
    tv: Client,
    subscriber: Subscriber,
    catalog: InMemoryCatalogSource,
    starter: FakeStarter,
    code: str,
    retry_after: int | None,
    status: int,
) -> None:
    starter.outcome = PlayRefused(code, retry_after)
    response = tv.get(subscriber.path("movie", 1001))
    assert (response.status_code, response["X-Reason"], response.content) == (status, code, b"")
    assert response.get("Retry-After") == (str(retry_after) if retry_after else None)


def test_playback_is_unavailable_until_the_playback_service_is_wired_in(
    tv: Client, subscriber: Subscriber, catalog: InMemoryCatalogSource
) -> None:
    previous = set_playback_starter(UnavailablePlayback())
    try:
        response = tv.get(subscriber.path("movie", 1001))
    finally:
        set_playback_starter(previous)
    assert (response.status_code, response["X-Reason"]) == (503, PLAYBACK_UNAVAILABLE)
    assert response["Retry-After"] == "60"
    assert refusal(PlayRefused("INTERNAL_ERROR")).status == 503


@pytest.mark.parametrize(
    "path",
    [
        "/movie/user/pass/1001",  # no extension
        "/movie/user/pass/0.mp4",  # ids start at 1
        "/movie/user/pass/12a.mp4",
        "/movie/user/1001.mp4",
        "/live/user/pass/3001.ts",  # M12
        "/timeshift/user/pass/60/2026-10-03:16-00/3001.ts",
    ],
)
def test_malformed_play_urls_are_404(tv: Client, path: str) -> None:
    response = tv.get(path)
    assert (response.status_code, response.content) == (404, b"")


def test_play_urls_refuse_unsafe_methods(tv: Client, subscriber: Subscriber) -> None:
    assert tv.post(subscriber.path("movie", 1001)).status_code == 405
