"""The pure builders against the golden fixtures and the PHP-type traps (SPEC §7.5)."""

import json
from dataclasses import replace
from datetime import date

import orjson
import pytest

from apps.xtream_api import payloads, playlist
from apps.xtream_api.dto import (
    AudioStream,
    EpisodeItem,
    EpisodeRef,
    MediaInfo,
    SeriesDetail,
    Text,
    VideoStream,
)
from apps.xtream_api.tests.contract import Contract
from apps.xtream_api.tests.fakes import (
    ALULA,
    BREAKING_BAD,
    MATRIX,
    SERIES_CATEGORIES,
    VOD_CATEGORIES,
    at,
)

SERVER = payloads.ServerInfo(host="tv.example.com", scheme="https", http_port=80, https_port=443)
GOLDEN_ACCOUNT = payloads.AccountInfo(
    username="mah-7k3p9q",
    password="example-password",  # noqa: S106 (the fixture's example pair)
    status="Active",
    expires_at=at(1822564800),
    created_at=at(1791028800),
    max_connections=2,
    active_connections=0,
)


def body(payload: object) -> bytes:
    return orjson.dumps(payload)


# --- Byte-for-byte the golden fixtures, from the same records -------------------------------


def test_login_matches_the_golden_fixture(contract: Contract) -> None:
    payload = payloads.login(GOLDEN_ACCOUNT, SERVER, at(1791043200))
    assert payload == contract.fixture("login.json")
    contract.check("login", body(payload))


def test_auth_failure_matches_the_golden_fixture(contract: Contract) -> None:
    assert json.loads(payloads.AUTH_FAILURE) == contract.fixture("auth_failure.json")
    contract.check("auth_failure", payloads.AUTH_FAILURE)


def test_catalog_payloads_match_the_golden_fixtures(contract: Contract) -> None:
    cases = {
        "get_vod_categories": payloads.categories(VOD_CATEGORIES, "en"),
        "get_series_categories": payloads.categories(SERIES_CATEGORIES, "en"),
        "get_vod_streams": payloads.vod_streams([ALULA.movie, MATRIX.movie], VOD_CATEGORIES, "en"),
        "get_vod_info": payloads.vod_info(MATRIX, VOD_CATEGORIES, "en"),
        "get_series": payloads.series_list([BREAKING_BAD.series], SERIES_CATEGORIES, "en"),
        "get_series_info": payloads.series_info(BREAKING_BAD, SERIES_CATEGORIES, "en"),
    }
    for action, payload in cases.items():
        assert payload == contract.fixture(f"{action}.json"), action
        contract.check(action, body(payload))


def test_empty_responses_are_typed(contract: Contract) -> None:
    for action in ("get_live_categories", "get_live_streams", "get_vod_streams", "get_series"):
        contract.check(action, payloads.EMPTY_LIST)
    for action in ("get_short_epg", "get_simple_data_table"):
        contract.check(action, payloads.EMPTY_EPG)


def test_playlist_matches_the_golden_fixture_without_its_live_channels(contract: Contract) -> None:
    movies = payloads.vod_streams([MATRIX.movie, ALULA.movie], VOD_CATEGORIES, "en")
    series = payloads.series_list([BREAKING_BAD.series], SERIES_CATEGORIES, "en")
    episodes = [EpisodeRef(e.xc_id, 2001, e.season, e.number) for e in BREAKING_BAD.episodes]
    entries = playlist.entries(
        movies,
        payloads.categories(VOD_CATEGORIES, "en"),
        series,
        payloads.categories(SERIES_CATEGORIES, "en"),
        episodes,
    )
    rendered = playlist.render(
        entries,
        origin="https://tv.example.com",
        username="mah-7k3p9q",
        password="example-password",  # noqa: S106
        live_ext="ts",
    )
    # The golden playlist also lists two live channels (M12): compare everything else.
    header, *rest = contract.fixture_text("m3u_plus.m3u").splitlines()
    pairs = [(rest[index], rest[index + 1]) for index in range(0, len(rest), 2)]
    without_live = [header, *(line for pair in pairs if "/live/" not in pair[1] for line in pair)]
    assert rendered.decode().splitlines() == without_live
    _, problems = contract.check_m3u(
        rendered,
        live_ext="ts",
        origin="https://tv.example.com",
        credentials=("mah-7k3p9q", "example-password"),
    )
    assert not problems


def test_empty_guide_is_valid_xmltv(contract: Contract) -> None:
    document = playlist.empty_guide('Smart "IPTV" & Co')
    assert not contract.check_xmltv(document)
    assert document.startswith(b'<?xml version="1.0" encoding="UTF-8"?>\n<tv ')


# --- Locale -----------------------------------------------------------------------------------


def test_arabic_users_get_arabic_names_with_fallbacks(contract: Contract) -> None:
    categories = payloads.categories(VOD_CATEGORIES, "ar")
    assert [c["category_name"] for c in categories] == ["أكشن", "خيال علمي", "وثائقيات"]
    info = payloads.series_info(BREAKING_BAD, SERIES_CATEGORIES, "ar")
    assert info is not None
    seasons = info["seasons"]
    assert isinstance(seasons, list)
    # Season names come in Arabic or are generated in Arabic, never in English.
    assert [season["name"] for season in seasons] == ["حلقات خاصة", "الموسم 1"]
    fields = info["info"]
    assert isinstance(fields, dict)
    assert (fields["name"], fields["genre"]) == ("بريكنج باد", "دراما, جريمة")
    # Text missing in Arabic falls back to English rather than to nothing.
    episodes = info["episodes"]
    assert isinstance(episodes, dict)
    assert episodes["1"][0]["title"] == "Pilot"
    contract.check("get_series_info", body(info))
    contract.check("get_vod_categories", body(categories))


def test_english_users_see_arabic_only_titles() -> None:
    [row] = payloads.vod_streams([ALULA.movie], VOD_CATEGORIES, "en")
    assert row["name"] == "رحلة إلى العُلا"


# --- Traps --------------------------------------------------------------------------------------


def test_items_keep_only_visible_categories_and_drop_out_without_any() -> None:
    visible = [category for category in VOD_CATEGORIES if category.xc_id != 12]
    rows = payloads.vod_streams([MATRIX.movie, ALULA.movie], visible, "en")
    assert [(row["stream_id"], row["category_id"], row["category_ids"]) for row in rows] == [
        (1001, "11", [11]),
        (1002, "14", [14]),
    ]
    only_documentaries = [category for category in VOD_CATEGORIES if category.xc_id == 14]
    assert [
        row["stream_id"] for row in payloads.vod_streams([MATRIX.movie], only_documentaries, "en")
    ] == []
    assert payloads.vod_info(MATRIX, only_documentaries, "en") is None
    assert payloads.series_info(BREAKING_BAD, [], "en") is None


def test_lists_follow_category_order_then_newest_first_and_number_from_one() -> None:
    newer = replace(MATRIX.movie, xc_id=1003, added=at(1791040000), title=Text("Newer"))
    duplicate = replace(MATRIX.movie, title=Text("Duplicate"))
    rows = payloads.vod_streams([ALULA.movie, MATRIX.movie, newer, duplicate], VOD_CATEGORIES, "en")
    assert [(row["num"], row["stream_id"]) for row in rows] == [(1, 1003), (2, 1001), (3, 1002)]
    filtered = payloads.vod_streams([ALULA.movie, MATRIX.movie, newer], VOD_CATEGORIES, "en", 14)
    assert [(row["num"], row["stream_id"]) for row in filtered] == [(1, 1002)]


def test_missing_values_are_empty_strings_and_lists_never_null(contract: Contract) -> None:
    bare_movie = replace(
        MATRIX,
        movie=replace(MATRIX.movie, poster="", rating=None, tmdb_id=None),
        poster_large="",
        backdrops=(),
        overview=Text(),
        cast=(),
        directors=(),
        genres=(),
        release_date=None,
        countries=(),
        imdb_id="",
        trailer_key="",
        certification="",
        media=MediaInfo(0, 0, VideoStream("h264", 640, 360)),
    )
    info = payloads.vod_info(bare_movie, VOD_CATEGORIES, "en")
    assert info is not None
    assert info["backdrop_path"] == []
    assert (info["rating"], info["rating_5based"]) == ("", 0)
    assert info["duration"] == "00:00:00"
    assert info["audio"] == {"codec_name": "", "channels": 0}
    assert None not in info.values()
    contract.check("get_vod_info", body(info))


def test_unsafe_values_are_dropped_not_passed_on(contract: Contract) -> None:
    risky = replace(
        MATRIX,
        movie=replace(MATRIX.movie, poster="https://media.example.com/a.avif", rating=11.5),
        backdrops=("/images/relative.jpg", "https://x.example/b.svg", "https://x.example/c.png"),
        imdb_id="0133093",
        trailer_key="https://youtu.be/vKQi3bBA1y8",
    )
    info = payloads.vod_info(risky, VOD_CATEGORIES, "en")
    assert info is not None
    assert (info["movie_image"], info["cover_big"]) == ("", MATRIX.poster_large)
    assert info["backdrop_path"] == ["https://x.example/c.png"]
    assert (info["imdb_id"], info["youtube_trailer"], info["rating"]) == ("", "", "")
    contract.check("get_vod_info", body(info))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (8.218, ("8.2", 4.1)),
        (8.9, ("8.9", 4.5)),
        (10, ("10.0", 5.0)),
        (0.04, ("0.0", 0.0)),
        (0, ("", 0)),
        (None, ("", 0)),
        (-1, ("", 0)),
    ],
)
def test_ratings(value: float | None, expected: tuple[str, float]) -> None:
    assert payloads.rating(value) == expected


def test_scalar_formats() -> None:
    assert payloads.duration(8160) == "02:16:00"
    assert payloads.duration(-5) == "00:00:00"
    assert payloads.duration(10**9) == "999:59:59"
    assert payloads.year(date(1999, 3, 30)) == "1999"
    assert payloads.year(None, 99) == ""
    assert payloads.unix(at(0)) == "0"
    assert payloads.tmdb(0) == ""
    assert payloads.minutes(0) == ""


def test_series_info_episode_shape(contract: Contract) -> None:
    extra = (
        # A duplicate of S01E01 (another file): listed once, lowest id first.
        replace(BREAKING_BAD.episodes[0], xc_id=5009),
        # Seasons beyond four digits cannot be keys.
        replace(BREAKING_BAD.episodes[0], xc_id=5010, season=10_000),
        # Season 10 sorts after season 2 ("10" after "2").
        replace(BREAKING_BAD.episodes[0], xc_id=5011, season=10, title=Text()),
        replace(BREAKING_BAD.episodes[0], xc_id=5012, season=2, number=3),
    )
    detail = SeriesDetail(
        series=BREAKING_BAD.series,
        seasons=BREAKING_BAD.seasons,
        episodes=(*BREAKING_BAD.episodes, *extra),
    )
    info = payloads.series_info(detail, SERIES_CATEGORIES, "en")
    assert info is not None
    episodes = info["episodes"]
    assert isinstance(episodes, dict)
    assert list(episodes) == ["0", "1", "2", "10"]
    assert [episode["id"] for episode in episodes["1"]] == ["5001", "5002"]
    assert episodes["10"][0]["title"] == "Episode 1"
    seasons = info["seasons"]
    assert isinstance(seasons, list)
    assert [(s["season_number"], s["name"], s["episode_count"]) for s in seasons] == [
        (0, "Specials", 1),
        (1, "Season 1", 2),
        (2, "Season 2", 1),
        (10, "Season 10", 1),
    ]
    # A season without its own poster or id borrows the series poster and a stable id.
    assert seasons[3]["cover"] == BREAKING_BAD.series.poster
    assert seasons[3]["id"] == 2001 * 10_000 + 10
    contract.check("get_series_info", body(info))


def test_series_without_playable_episodes_has_empty_object_episodes(contract: Contract) -> None:
    info = payloads.series_info(replace(BREAKING_BAD, episodes=()), SERIES_CATEGORIES, "en")
    assert info is not None
    assert (info["episodes"], info["seasons"]) == ({}, [])
    assert orjson.dumps(info["episodes"]) == b"{}"
    contract.check("get_series_info", body(info))


def test_login_never_sends_null_or_out_of_range_counts(contract: Contract) -> None:
    account = replace(GOLDEN_ACCOUNT, expires_at=None, max_connections=0, active_connections=-3)
    payload = payloads.login(account, SERVER, at(1791043200))
    user_info = payload["user_info"]
    assert isinstance(user_info, dict)
    assert user_info["exp_date"] == str(payloads.NO_END_TIMESTAMP)
    assert (user_info["max_connections"], user_info["active_cons"]) == ("1", "0")
    contract.check("login", body(payload))


def test_server_info_origin() -> None:
    assert SERVER.origin == "https://tv.example.com"
    dev = payloads.ServerInfo(host="tv.localhost", scheme="http", http_port=8080, https_port=443)
    assert dev.origin == "http://tv.localhost:8080"


def test_media_without_audio(contract: Contract) -> None:
    silent = replace(
        BREAKING_BAD.episodes[0],
        media=MediaInfo(60, 900, VideoStream("h264", 1280, 720), AudioStream()),
    )
    info = payloads.series_info(replace(BREAKING_BAD, episodes=(silent,)), SERIES_CATEGORIES, "en")
    assert info is not None
    contract.check("get_series_info", body(info))


def test_playable_seasons_ignores_impossible_numbers() -> None:
    episode = BREAKING_BAD.episodes[0]
    items: list[EpisodeItem] = [
        replace(episode, xc_id=1, season=-1),
        replace(episode, xc_id=2, number=-1),
        replace(episode, xc_id=0),
        replace(episode, xc_id=3),
    ]
    assert {
        season: [e.xc_id for e in eps] for season, eps in payloads.playable_seasons(items).items()
    } == {1: [3]}
