"""The playback starter over playback.services: refusal mapping, and play URLs through the
real catalog, entitlement checks, slots and signed edge URLs (SPEC §7.4, §7.5)."""

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import redis
from django.test import Client
from pytest_django import Settings

from apps.catalog.models import TitleStatus
from apps.library.models import Library
from apps.playback import tokens
from apps.playback.models import PlaybackSession
from apps.playback.models import TitleKind as PlaybackKind
from apps.playback.services import Denial, PlayableTitle, PlaybackDenied, Prefer
from apps.playback.tests.conftest import VECTORS
from apps.xtream_api import starter as starter_module
from apps.xtream_api.dto import TitleKind, TitleRef
from apps.xtream_api.playback import (
    PlayRefused,
    PlayRequest,
    PlayStarted,
    playback_starter,
    set_playback_starter,
)
from apps.xtream_api.starter import DjangoPlaybackStarter
from apps.xtream_api.tests.conftest import Subscriber, subscribe
from apps.xtream_api.tests.world import World, build_world

pytestmark = pytest.mark.django_db
MEDIA = "https://media.example.test"


@pytest.fixture(autouse=True)
def media_keys(settings: Settings, tmp_path: Path) -> Iterator[None]:
    """The public ADR-0007 test keys and a fixed media origin."""
    path = tmp_path / "media_token_keys.json"
    path.write_text(json.dumps(json.loads(VECTORS.read_text())["keys"]), encoding="utf-8")
    settings.MEDIA_TOKEN_KEYS_FILE = str(path)
    settings.MEDIA_BASE_URL = MEDIA
    tokens.reset_keyring()
    yield
    tokens.reset_keyring()


@pytest.fixture
def real_starter() -> Iterator[DjangoPlaybackStarter]:
    real = DjangoPlaybackStarter()
    previous = set_playback_starter(real)
    yield real
    set_playback_starter(previous)


@pytest.fixture
def world(make_library: Callable[..., Library]) -> World:
    return build_world(make_library)


def _request(subscriber: Subscriber, extension: str = "mp4") -> PlayRequest:
    return PlayRequest(
        user=subscriber.user,
        device=subscriber.device,
        title=TitleRef(TitleKind.EPISODE, 42, "x"),
        extension=extension,
        client_ip="203.0.113.7",
        user_agent="IPTVSmarters/3.1",
    )


def test_ready_installs_the_django_starter() -> None:
    assert isinstance(playback_starter(), DjangoPlaybackStarter)


def test_outcomes_map_from_the_playback_service(
    subscriber: Subscriber, monkeypatch: pytest.MonkeyPatch
) -> None:
    looked_up: list[tuple[PlaybackKind, int]] = []
    title = PlayableTitle(kind=PlaybackKind.EPISODE, id=subscriber.user.pk, renditions=())

    def lookup(kind: PlaybackKind, xc_id: int) -> PlayableTitle | None:
        looked_up.append((kind, xc_id))
        return title

    calls: list[dict[str, Any]] = []

    class Grant:
        url = f"{MEDIA}/v/token/compat.mp4"

    def granted(*args: Any, **kwargs: Any) -> Grant:
        calls.append({"args": args, **kwargs})
        return Grant()

    monkeypatch.setattr(starter_module, "start_playback", granted)
    starter = DjangoPlaybackStarter(lookup)
    assert starter.start(_request(subscriber)) == PlayStarted(Grant.url)
    assert starter.start(_request(subscriber, "m3u8")).__class__ is PlayStarted
    assert looked_up == [(PlaybackKind.EPISODE, 42)] * 2
    assert [call["prefer"] for call in calls] == [Prefer.MP4, Prefer.HLS]
    assert calls[0]["args"] == (subscriber.user, subscriber.device, title)
    assert (calls[0]["client_ip"], calls[0]["user_agent"]) == ("203.0.113.7", "IPTVSmarters/3.1")

    def denied(*args: Any, **kwargs: Any) -> Grant:
        raise PlaybackDenied(Denial.CONCURRENCY_LIMIT)

    monkeypatch.setattr(starter_module, "start_playback", denied)
    assert starter.start(_request(subscriber)) == PlayRefused("CONCURRENCY_LIMIT")

    def down(*args: Any, **kwargs: Any) -> Grant:
        raise redis.ConnectionError

    monkeypatch.setattr(starter_module, "start_playback", down)
    assert starter.start(_request(subscriber)) == PlayRefused("INTERNAL_ERROR")
    assert DjangoPlaybackStarter(lambda kind, xc_id: None).start(
        _request(subscriber)
    ) == PlayRefused("TITLE_PREPARING")


# --- Through the real catalog and playback service ------------------------------------------


def test_play_urls_redirect_to_signed_edge_urls(
    tv: Client, subscriber: Subscriber, world: World, real_starter: DjangoPlaybackStarter
) -> None:
    (movie,) = world.add_movies(1, compat=True)
    (show,) = world.add_series(1)
    episode = show.seasons.get(number=1).episodes.get(number=1)

    response = tv.get(subscriber.path("movie", movie.xc_id), HTTP_USER_AGENT="IPTVnator")
    assert response.status_code == 302, response.headers
    assert response["Location"].startswith(f"{MEDIA}/v/")
    assert subscriber.password not in response["Location"]
    assert response.content == b""
    session = PlaybackSession.objects.get(user=subscriber.user)
    assert session.user_agent == "IPTVnator"

    episode_play = tv.get(subscriber.path("series", episode.xc_id))
    assert episode_play.status_code == 302
    assert episode_play["Location"].startswith(f"{MEDIA}/v/")

    login = json.loads(tv.get("/player_api.php", subscriber.params()).content)
    # One stream per device: the episode replaced the movie.
    assert login["user_info"]["active_cons"] == "1"


def test_titles_not_ready_or_unknown(
    tv: Client, subscriber: Subscriber, world: World, real_starter: DjangoPlaybackStarter
) -> None:
    (processing,) = world.add_movies(1, status=TitleStatus.PROCESSING)
    (hidden,) = world.add_movies(1, status=TitleStatus.HIDDEN)
    (show,) = world.add_series(1)
    preparing = show.seasons.get(number=1).episodes.get(number=3)
    for path in (
        subscriber.path("movie", processing.xc_id),
        subscriber.path("series", preparing.xc_id),
    ):
        response = tv.get(path)
        assert (response.status_code, response["X-Reason"]) == (503, "TITLE_PREPARING")
        assert response["Retry-After"] == "60"
    for path in (
        subscriber.path("movie", hidden.xc_id),
        subscriber.path("movie", preparing.xc_id),  # an episode id is no movie
        subscriber.path("series", 987_654_321),
    ):
        response = tv.get(path)
        assert (response.status_code, response["X-Reason"]) == (404, "NOT_FOUND")


def test_stream_limit_and_denials_are_refusals(
    tv: Client, world: World, make_customer: Callable[..., Any], real_starter: DjangoPlaybackStarter
) -> None:
    first, second = world.add_movies(2, compat=True)
    customer = make_customer(max_streams=1)
    living_room, bedroom = subscribe(customer), subscribe(customer)
    assert tv.get(living_room.path("movie", first.xc_id)).status_code == 302
    refused = tv.get(bedroom.path("movie", second.xc_id))
    assert (refused.status_code, refused["X-Reason"]) == (403, "CONCURRENCY_LIMIT")

    no_movies = subscribe(make_customer(allow_movies=False))
    refused = tv.get(no_movies.path("movie", first.xc_id))
    assert (refused.status_code, refused["X-Reason"]) == (403, "CONTENT_TYPE_NOT_ALLOWED")
