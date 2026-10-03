"""The Django catalog source over the real models: visibility, mapping to the DTOs,
query counts, and player_api.php checked against compat/ (SPEC §7.5)."""

import json
from collections.abc import Callable, Iterator
from datetime import date
from typing import Any

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from pytest_django import Settings

from apps.catalog.models import Movie, TitleStatus
from apps.catalog.services import refresh_movie_status
from apps.library.models import Library
from apps.media.models import Rendition
from apps.xtream_api.catalog import DjangoCatalogSource
from apps.xtream_api.dto import AudioStream, Text, TitleKind, VideoStream
from apps.xtream_api.source import CatalogScope, catalog_source, set_catalog_source
from apps.xtream_api.tests.conftest import Subscriber, subscribe
from apps.xtream_api.tests.contract import Contract
from apps.xtream_api.tests.world import World, build_world

pytestmark = pytest.mark.django_db
API = "/player_api.php"
MEDIA = "https://media.example.test"
EVERYTHING = CatalogScope()


@pytest.fixture(autouse=True)
def _media_origin(settings: Settings) -> None:
    settings.MEDIA_BASE_URL = MEDIA


@pytest.fixture
def source() -> Iterator[DjangoCatalogSource]:
    """The installed source (XtreamApiConfig.ready), reinstalled if a test swapped it."""
    real = DjangoCatalogSource()
    previous = set_catalog_source(real)
    yield real
    set_catalog_source(previous)


@pytest.fixture
def world(make_library: Callable[..., Library]) -> World:
    return build_world(make_library)


def test_ready_installs_the_django_source() -> None:
    assert isinstance(catalog_source(), DjangoCatalogSource)


def test_categories_are_visible_allowed_and_not_empty(
    source: DjangoCatalogSource, world: World
) -> None:
    assert not source.categories(EVERYTHING, "vod")  # nothing ready yet
    world.add_movies(1)
    world.add_series(1)
    vod = source.categories(EVERYTHING, "vod")
    assert [(c.xc_id, c.name, c.sort) for c in vod] == [
        (world.action.xc_id, Text("Action", "ar-action"), 1),
        (world.drama.xc_id, Text("Drama", "ar-drama"), 2),
    ]
    series = source.categories(EVERYTHING, "series")
    assert [c.xc_id for c in series] == [world.docs.xc_id, world.crime.xc_id]
    only_drama = CatalogScope(categories=frozenset({str(world.drama.pk)}))
    assert [c.xc_id for c in source.categories(only_drama, "vod")] == [world.drama.xc_id]
    assert not source.categories(only_drama, "series")
    assert not source.categories(CatalogScope(allow_movies=False), "vod")
    assert not source.categories(EVERYTHING, "live")


def test_movies_map_to_the_dtos(source: DjangoCatalogSource, world: World) -> None:
    (movie,) = world.add_movies(1)
    world.add_movies(1, status=TitleStatus.PROCESSING)
    world.add_movies(1, status=TitleStatus.HIDDEN)
    (summary,) = source.movies(EVERYTHING)
    assert summary.xc_id == movie.xc_id
    assert summary.title == Text(f"{movie.title}", f"{movie.title_ar}")
    assert summary.added == movie.created_at
    # Link order: drama was linked first (the primary), the hidden category is cut.
    assert summary.category_ids == (world.drama.xc_id, world.action.xc_id)
    assert summary.poster == f"{MEDIA}/images/{movie.pk}/poster/w500.abc.webp"
    assert (summary.rating, summary.tmdb_id, summary.is_adult) == (8.27, movie.tmdb_id, False)

    detail = source.movie(EVERYTHING, movie.xc_id)
    assert detail is not None
    assert detail.movie == summary
    assert detail.poster_large == f"{MEDIA}/images/{movie.pk}/poster/original.abc.webp"
    assert detail.backdrops == (f"{MEDIA}/images/{movie.pk}/backdrop/w1280.abc.webp",)
    assert detail.overview == Text("An overview.", "نبذة.")
    assert len(detail.cast) == 2
    assert len(detail.directors) == 1
    assert detail.genres == (Text("Action", "أكشن"), Text("Drama", "دراما"))
    assert (detail.release_date, detail.year, detail.countries) == (
        date(1999, 3, 31),
        1999,
        ("US",),
    )
    assert (detail.imdb_id, detail.trailer_key, detail.certification) == (
        "tt0133093",
        "vKQi3bBA1y8",
        "R",
    )
    # A direct-play source: delivered as probed, with the default audio track.
    assert detail.media.video == VideoStream("hevc", 3840, 2160)
    assert detail.media.audio == AudioStream("ac3", 6)
    assert (detail.media.duration_secs, detail.media.bitrate_kbps) == (7260, 8000)


def test_transcoded_movies_report_the_compat_rendition(
    source: DjangoCatalogSource, world: World
) -> None:
    (movie,) = world.add_movies(1, compat=True)
    detail = source.movie(EVERYTHING, movie.xc_id)
    assert detail is not None
    assert detail.media.video == VideoStream("h264", 1920, 1080)
    assert detail.media.audio == AudioStream("aac", 2)
    assert (detail.media.duration_secs, detail.media.bitrate_kbps) == (7260, 5000)


def test_movie_visibility(source: DjangoCatalogSource, world: World) -> None:
    (movie,) = world.add_movies(1)
    (processing,) = world.add_movies(1, status=TitleStatus.PROCESSING)
    assert source.movie(EVERYTHING, processing.xc_id) is None
    assert source.movie(CatalogScope(allow_movies=False), movie.xc_id) is None
    assert not source.movies(CatalogScope(allow_movies=False))
    hidden_only = CatalogScope(categories=frozenset({str(world.hidden.pk)}))
    assert not source.movies(hidden_only)
    assert source.movie(hidden_only, movie.xc_id) is None
    action_only = CatalogScope(categories=frozenset({str(world.action.pk)}))
    (summary,) = source.movies(action_only)
    assert summary.category_ids == (world.action.xc_id,)


def test_series_map_to_the_dtos(source: DjangoCatalogSource, world: World) -> None:
    (show,) = world.add_series(1)
    (summary,) = source.series_list(EVERYTHING)
    assert summary.xc_id == show.xc_id
    assert summary.category_ids == (world.crime.xc_id, world.docs.xc_id)
    assert summary.poster.endswith("/poster/w500.abc.webp")
    assert summary.poster_large.endswith("/poster/original.abc.webp")
    assert len(summary.backdrops) == 1
    assert len(summary.cast) == 2
    assert len(summary.creators) == 1
    assert (summary.episode_run_time, summary.first_air_date) == (47, date(2008, 1, 20))
    newest = show.seasons.get(number=0).episodes.get().created_at
    assert summary.last_modified == newest

    detail = source.series(EVERYTHING, show.xc_id)
    assert detail is not None
    assert detail.series == summary
    season_one = show.seasons.get(number=1).tmdb_id
    assert [(s.number, s.id) for s in detail.seasons] == [(0, -1), (1, season_one), (2, -1)]
    playable = sorted((e.season, e.number) for e in detail.episodes)
    assert playable == [(0, 1), (1, 1), (1, 2)]  # S01E03 is still being prepared
    episode = next(e for e in detail.episodes if (e.season, e.number) == (1, 1))
    assert episode.media.video == VideoStream("h264", 1920, 1080)
    assert episode.still.endswith("/still/w500.abc.webp")
    refs = source.episodes(EVERYTHING)
    assert sorted((r.series_xc_id, r.season, r.number) for r in refs) == [
        (show.xc_id, 0, 1),
        (show.xc_id, 1, 1),
        (show.xc_id, 1, 2),
    ]
    assert not source.series_list(CatalogScope(allow_series=False))
    assert not source.episodes(CatalogScope(categories=frozenset()))


def test_series_without_playable_episodes_are_not_listed(
    source: DjangoCatalogSource, world: World
) -> None:
    (show,) = world.add_series(1)
    Rendition.objects.filter(media_file__episodes__season__series=show).delete()
    assert not source.series_list(EVERYTHING)
    assert source.series(EVERYTHING, show.xc_id) is None
    assert not source.categories(EVERYTHING, "series")


def test_title_resolves_ready_and_upcoming_titles_only(
    source: DjangoCatalogSource, world: World
) -> None:
    (movie,) = world.add_movies(1)
    (processing,) = world.add_movies(1, status=TitleStatus.PROCESSING)
    (hidden,) = world.add_movies(1, status=TitleStatus.HIDDEN)
    (show,) = world.add_series(1)
    episode = show.seasons.get(number=1).episodes.get(number=3)
    assert source.title(TitleKind.MOVIE, movie.xc_id) is not None
    assert source.title(TitleKind.MOVIE, processing.xc_id) is not None
    assert source.title(TitleKind.MOVIE, hidden.xc_id) is None
    ref = source.title(TitleKind.EPISODE, episode.xc_id)
    assert ref is not None
    assert (ref.kind, ref.xc_id, ref.id) == (TitleKind.EPISODE, episode.xc_id, str(episode.pk))
    assert source.title(TitleKind.MOVIE, episode.xc_id) is None  # ids never cross kinds
    assert source.title(TitleKind.EPISODE, 999_999_999) is None


# --- Query counts: fixed whatever the catalog size --------------------------------------------


def _queries(call: Callable[[], Any]) -> int:
    with CaptureQueriesContext(connection) as captured:
        call()
    return len(captured.captured_queries)


def _all_calls(source: DjangoCatalogSource, world: World) -> dict[str, Callable[[], Any]]:
    movie = Movie.objects.filter(status=TitleStatus.READY).first()
    show = world.crime.series.first()
    assert movie is not None
    assert show is not None
    return {
        "categories": lambda: source.categories(EVERYTHING, "vod"),
        "movies": lambda: source.movies(EVERYTHING),
        "movie": lambda: source.movie(EVERYTHING, movie.xc_id),
        "series_list": lambda: source.series_list(EVERYTHING),
        "series": lambda: source.series(EVERYTHING, show.xc_id),
        "episodes": lambda: source.episodes(EVERYTHING),
    }


EXPECTED_QUERIES = {
    "categories": 1,
    "movies": 3,  # category links, movies, images
    "movie": 7,  # movie, images, credits, genres, files, renditions, links
    "series_list": 5,  # links, series, images, credits, genres
    "series": 11,  # series + 5 prefetches, links, episodes, files, renditions, stills
    "episodes": 1,
}


def test_query_counts_do_not_grow_with_the_catalog(
    source: DjangoCatalogSource, world: World
) -> None:
    world.add_movies(2)
    world.add_series(1)
    small = {name: _queries(call) for name, call in _all_calls(source, world).items()}
    world.add_movies(6, compat=True)
    world.add_series(4)
    large = {name: _queries(call) for name, call in _all_calls(source, world).items()}
    assert small == large
    assert large == EXPECTED_QUERIES
    assert len(source.movies(EVERYTHING)) == 8
    assert len(source.series_list(EVERYTHING)) == 5


# --- player_api.php over the real catalog, checked against compat/ -----------------------------


def _get(tv: Client, subscriber: Subscriber, **params: str) -> bytes:
    response = tv.get(API, subscriber.params(**params))
    assert response.status_code == 200, response.content
    return bytes(response.content)


def test_player_api_over_the_real_catalog_meets_the_contract(
    tv: Client, subscriber: Subscriber, world: World, contract: Contract
) -> None:
    movies = world.add_movies(3)
    world.add_movies(1, compat=True)
    shows = world.add_series(2)
    responses: dict[str, Any] = {}
    for action in (
        "get_vod_categories",
        "get_series_categories",
        "get_live_categories",
        "get_vod_streams",
        "get_series",
        "get_live_streams",
    ):
        responses[action] = contract.check(action, _get(tv, subscriber, action=action))
    responses["get_vod_info"] = contract.check(
        "get_vod_info",
        _get(tv, subscriber, action="get_vod_info", vod_id=str(movies[0].xc_id)),
    )
    series_infos = [
        contract.check(
            "get_series_info",
            _get(tv, subscriber, action="get_series_info", series_id=str(show.xc_id)),
        )
        for show in shows
    ]
    assert not contract.check_catalog(responses, series_infos)
    assert len(responses["get_vod_streams"]) == 4
    assert [c["category_name"] for c in responses["get_vod_categories"]] == ["Action", "Drama"]
    info = series_infos[0]
    assert list(info["episodes"]) == ["0", "1"]
    assert [s["season_number"] for s in info["seasons"]] == [0, 1]
    assert info["episodes"]["1"][0]["info"]["video"]["codec_name"] == "h264"

    playlist = tv.get("/get.php", subscriber.params(type="m3u_plus", output="ts"))
    assert playlist.status_code == 200
    parsed, problems = contract.check_m3u(
        playlist.content,
        live_ext="ts",
        origin=_origin(tv, subscriber),
        credentials=(subscriber.username, subscriber.password),
    )
    assert not problems
    assert not contract.check_catalog(responses, series_infos, parsed)


def _origin(tv: Client, subscriber: Subscriber) -> str:
    login = json.loads(_get(tv, subscriber))
    server = login["server_info"]
    return f"{server['server_protocol']}://{server['url']}:{server['port']}"


def test_restricted_customers_see_only_their_categories(
    tv: Client, world: World, contract: Contract, make_customer: Callable[..., Any]
) -> None:
    world.add_movies(2)
    world.add_series(1)
    customer = make_customer(category_ids=[str(world.action.pk)], allow_series=False)
    restricted = subscribe(customer)
    categories = contract.check(
        "get_vod_categories", _get(tv, restricted, action="get_vod_categories")
    )
    assert [c["category_id"] for c in categories] == [str(world.action.xc_id)]
    streams = contract.check("get_vod_streams", _get(tv, restricted, action="get_vod_streams"))
    assert {s["category_id"] for s in streams} == {str(world.action.xc_id)}
    assert json.loads(_get(tv, restricted, action="get_series")) == []


def test_arabic_customers_get_arabic_names(
    tv: Client, subscriber: Subscriber, world: World, contract: Contract
) -> None:
    (movie,) = world.add_movies(1)
    subscriber.user.locale = "ar"
    subscriber.user.save(update_fields=["locale"])
    categories = contract.check(
        "get_vod_categories", _get(tv, subscriber, action="get_vod_categories")
    )
    assert [c["category_name"] for c in categories] == ["ar-action", "ar-drama"]
    info = contract.check(
        "get_vod_info", _get(tv, subscriber, action="get_vod_info", vod_id=str(movie.xc_id))
    )
    assert (info["name"], info["plot"], info["genre"]) == (movie.title_ar, "نبذة.", "أكشن, دراما")


def test_catalog_changes_reach_apps(
    tv: Client, subscriber: Subscriber, world: World, django_capture_on_commit_callbacks: Any
) -> None:
    (movie,) = world.add_movies(1)
    assert len(json.loads(_get(tv, subscriber, action="get_vod_streams"))) == 1
    movie.files.update(removed_at=movie.created_at)
    with django_capture_on_commit_callbacks(execute=True):
        assert refresh_movie_status(movie)  # → hidden; invalidates the cache on commit
    assert json.loads(_get(tv, subscriber, action="get_vod_streams")) == []
