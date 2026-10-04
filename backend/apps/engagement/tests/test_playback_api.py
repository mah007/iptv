"""Web playback through the customer API (SPEC §3, §7.4, §10 Playback; ADR-0013)."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings
from rest_framework.test import APIClient

from apps.accounts import customer_auth
from apps.accounts import services as accounts
from apps.accounts.models import Device, DeviceKind, User
from apps.catalog.tests.builders import Builder, episode, refresh_entitlement
from apps.conftest import CustomerFactory
from apps.engagement.models import WatchProgress
from apps.media.models import (
    AudioTrack,
    Rendition,
    RenditionKind,
    RenditionStatus,
    SubtitleFormat,
    SubtitleStatus,
    SubtitleTrack,
)
from apps.playback import tokens
from apps.playback.models import EndReason, PlaybackSession

VECTORS = Path(__file__).resolve().parents[2] / "playback" / "tests" / "data" / "token_vectors.json"

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("clean_stores", "media_keys")]

PORTAL = {"host": settings.APP_HOST}
START = "/api/v1/playback/start"


@pytest.fixture
def media_keys(settings: Any, tmp_path: Path) -> Iterator[None]:
    """The public ADR-0007 test keys (the playback tests' vectors)."""
    keys = json.loads(VECTORS.read_text(encoding="utf-8"))["keys"]
    path = tmp_path / "media_token_keys.json"
    path.write_text(json.dumps(keys), encoding="utf-8")
    settings.MEDIA_TOKEN_KEYS_FILE = str(path)
    settings.MEDIA_BASE_URL = "https://media.example.test"
    tokens.reset_keyring()
    yield
    tokens.reset_keyring()


@pytest.fixture
def customer(make_customer: CustomerFactory) -> User:
    return make_customer(max_streams=2)


@pytest.fixture
def client(customer: User) -> APIClient:
    client = APIClient()
    client.force_login(customer)
    client.defaults["HTTP_HOST"] = PORTAL["host"]
    client.defaults["HTTP_USER_AGENT"] = "Mozilla/5.0 (X11; Linux x86_64) Firefox/140.0"
    return client


def start(client: APIClient, **body: Any) -> Any:
    return client.post(START, body, format="json")


def test_start_a_movie_in_the_browser(build: Builder, customer: User, client: APIClient) -> None:
    movie = build.movie("Film")
    response = start(client, title_type="movie", title_id=str(movie.pk), prefer="mp4")
    assert response.status_code == 200, response.json()
    assert response["Cache-Control"] == "no-store"
    body = response.json()
    assert body["title_type"] == "movie"
    assert body["delivery"] == "progressive"
    assert "/v/" in body["url"]
    assert body["url"].endswith("/compat.mp4")
    assert body["compat_url"] == body["url"]
    assert body["hls_master_url"] is None
    assert body["resume_ms"] == 0
    assert body["duration_ms"] == 5_400_000
    assert body["height"] == 1080
    session = PlaybackSession.objects.get(pk=body["session_id"])
    assert session.user == customer
    device = Device.objects.get(user=customer, kind=DeviceKind.WEB)
    assert device.name == "Firefox on Linux"
    assert client.session[customer_auth.SESSION_DEVICE_KEY] == str(device.pk)

    # The same browser keeps its device.
    assert start(client, title_type="movie", title_id=str(movie.pk)).status_code == 200
    assert Device.objects.filter(user=customer, kind=DeviceKind.WEB).count() == 1


def test_tracks_and_thumbnails_come_with_the_grant(build: Builder, client: APIClient) -> None:
    movie = build.movie("Film")
    file = movie.files.get()
    AudioTrack.objects.create(
        media_file=file, stream_index=1, language="ara", codec="aac", channels=2, default=True
    )
    SubtitleTrack.objects.create(
        media_file=file,
        stream_index=2,
        format=SubtitleFormat.SRT,
        language="eng",
        status=SubtitleStatus.READY,
        storage_key="2.eng",
    )
    Rendition.objects.create(
        media_file=file,
        kind=RenditionKind.THUMBNAILS,
        storage_key=file.pk.hex,
        status=RenditionStatus.READY,
    )
    body = start(client, title_type="movie", title_id=str(movie.pk)).json()
    base = body["url"].rsplit("/", 1)[0]
    assert body["audio"] == [{"language": "ara", "codec": "aac", "channels": 2, "default": True}]
    assert body["subtitles"][0]["url"] == f"{base}/compat/subs/2.eng.vtt"
    assert body["thumbnails_url"] == f"{base}/compat/thumbs/thumbs.vtt"


def test_resume_where_the_customer_left(build: Builder, customer: User, client: APIClient) -> None:
    movie = build.movie("Film")
    WatchProgress.objects.create(
        user=customer, movie=movie, position_ms=120_000, duration_ms=5_400_000
    )
    body = start(client, title_type="movie", title_id=str(movie.pk)).json()
    assert body["resume_ms"] == 120_000


def test_series_play_the_next_episode_or_the_one_asked_for(
    build: Builder, customer: User, client: APIClient
) -> None:
    series = build.series("Show", seasons={1: 3})
    first = start(client, title_type="series", title_id=str(series.pk)).json()
    assert first["episode"] == {
        "id": str(episode(series, 1, 1).pk),
        "season_number": 1,
        "number": 1,
    }
    third = episode(series, 1, 3)
    chosen = start(
        client, title_type="series", title_id=str(series.pk), episode_id=str(third.pk)
    ).json()
    assert chosen["title_type"] == "episode"
    assert chosen["title_id"] == str(third.pk)


def test_refusals_carry_stable_codes(
    build: Builder, customer: User, client: APIClient, make_customer: CustomerFactory
) -> None:
    movie = build.movie("Film")
    late = build.category("late", adult=True)
    adult = build.movie("Adult", categories=[late])
    preparing = build.movie("Soon", playable=False)
    series = build.series("Show", unplayable=[(1, 2)])

    hidden = start(client, title_type="movie", title_id=str(adult.pk))
    assert hidden.status_code == 404
    unknown = start(client, title_type="series", title_id=str(movie.pk))
    assert unknown.status_code == 404
    not_ready = start(client, title_type="movie", title_id=str(preparing.pk))
    assert not_ready.status_code == 409  # ready, but no rendition plays yet
    assert not_ready.json()["code"] == "TITLE_PREPARING"
    processing = build.movie("Processing", status="processing")
    assert start(client, title_type="movie", title_id=str(processing.pk)).status_code == 404
    episode_two = episode(series, 1, 2)
    preparing_episode = start(
        client, title_type="series", title_id=str(series.pk), episode_id=str(episode_two.pk)
    )
    assert preparing_episode.status_code == 409
    assert preparing_episode.json()["code"] == "TITLE_PREPARING"
    bad = start(client, title_type="episode", title_id=str(movie.pk))
    assert bad.status_code == 400

    accounts.suspend_customer(customer, actor=None)
    refresh_entitlement(customer)
    suspended = start(client, title_type="movie", title_id=str(movie.pk))
    assert suspended.status_code == 403
    assert suspended.json()["code"] == "ACCOUNT_SUSPENDED"


def test_the_stream_limit_applies_across_browsers(
    build: Builder, make_customer: CustomerFactory
) -> None:
    movie = build.movie("Film")
    other = build.movie("Other")
    user = make_customer(max_streams=1)
    browsers = []
    for _ in range(2):
        browser = APIClient()
        browser.force_login(user)
        browser.defaults["HTTP_HOST"] = PORTAL["host"]
        browsers.append(browser)
    assert start(browsers[0], title_type="movie", title_id=str(movie.pk)).status_code == 200
    second = start(browsers[1], title_type="movie", title_id=str(other.pk))
    assert second.status_code == 409
    assert second.json()["code"] == "CONCURRENCY_LIMIT"


def test_progress_is_throttled_and_marks_titles_watched(
    build: Builder, customer: User, client: APIClient
) -> None:
    movie = build.movie("Film")
    session = start(client, title_type="movie", title_id=str(movie.pk)).json()["session_id"]
    url = f"/api/v1/playback/{session}/progress"
    first = client.post(url, {"position_ms": 60_000}, format="json")
    assert first.json() == {"saved": True, "position_ms": 60_000, "completed": False}
    assert client.post(url, {"position_ms": 75_000}, format="json").json()["saved"] is False
    row = WatchProgress.objects.get(user=customer, movie=movie)
    assert row.position_ms == 60_000
    assert row.duration_ms == 5_400_000

    stop = client.post(
        f"/api/v1/playback/{session}/stop", {"position_ms": 5_000_000}, format="json"
    )
    assert stop.status_code == 204
    row.refresh_from_db()
    assert row.completed is True
    closed = PlaybackSession.objects.get(pk=session)
    assert closed.ended_at is not None
    assert closed.end_reason == EndReason.STOPPED
    # Stopping twice is harmless; another customer's session is unknown.
    assert client.post(f"/api/v1/playback/{session}/stop", {}, format="json").status_code == 204


def test_progress_of_someone_elses_session_is_refused(
    build: Builder, client: APIClient, make_customer: CustomerFactory
) -> None:
    movie = build.movie("Film")
    session = start(client, title_type="movie", title_id=str(movie.pk)).json()["session_id"]
    stranger = APIClient()
    stranger.force_login(make_customer())
    response = stranger.post(
        f"/api/v1/playback/{session}/progress",
        {"position_ms": 1},
        format="json",
        headers=PORTAL,
    )
    assert response.status_code == 404


def test_episode_progress_records_the_series(
    build: Builder, customer: User, client: APIClient
) -> None:
    series = build.series("Show")
    body = start(client, title_type="series", title_id=str(series.pk)).json()
    client.post(
        f"/api/v1/playback/{body['session_id']}/progress",
        {"position_ms": 600_000, "duration_ms": 2_700_000},
        format="json",
    )
    row = WatchProgress.objects.get(user=customer)
    assert row.series == series
    assert row.episode == episode(series, 1, 1)
    assert row.duration_ms == 2_700_000
