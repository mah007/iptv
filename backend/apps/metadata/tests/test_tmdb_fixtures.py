"""Fixture mode: the offline TMDB that lets the sample media match without a key."""

import io
import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from pytest_django import Settings

from apps.library.parsing import LibraryKind, parse_path
from apps.metadata.scoring import Candidate, MatchQuery, decide
from apps.metadata.tmdb import (
    DEFAULT_FIXTURES_DIR,
    MOVIE_APPEND,
    FixtureTransport,
    TMDBClient,
    TMDBNotFoundError,
    image_url,
)
from apps.metadata.tmdb.factory import client_from_settings
from apps.metadata.tmdb.recording import SAMPLE_TITLES, record_sample_fixtures

# The sample files are 30-60 s long, so the probed runtime matches no real runtime.
SAMPLE_RUNTIME_MIN = 0.5


@pytest.fixture
def client() -> TMDBClient:
    return TMDBClient.offline()


def ids(response: dict[str, Any]) -> list[int]:
    return [result["id"] for result in response["results"]]


def test_offline_client_needs_no_credentials(client: TMDBClient) -> None:
    assert client.fixture_mode
    config = client.configuration()
    assert config["images"]["secure_base_url"] == "https://image.tmdb.org/t/p/"
    assert "w500" in config["images"]["poster_sizes"]


@pytest.mark.parametrize(
    ("query", "year", "expected"),
    [
        ("The Matrix", 1999, [603]),
        ("The Matrix", None, [603, 604, 605, 624860]),
        ("the.matrix!", 2003, [604, 605]),
        ("Blade Runner 2049", 2017, [335984]),
        ("Blade Runner 2049", None, [335984, 78]),
        ("Inception", 2010, [27205]),
        ("وجدة", 2012, [129112]),
        ("وجده", None, [129112]),
        ("Wadjda", 2012, [129112]),
        ("The Matrix", 1990, []),
        ("A Title Nobody Recorded", None, []),
    ],
)
def test_offline_movie_search_filters_by_year_like_tmdb(
    client: TMDBClient, query: str, year: int | None, expected: list[int]
) -> None:
    response = client.search_movie(query, year=year)
    assert ids(response) == expected
    assert response["total_results"] == len(expected)
    assert response["page"] == 1


def test_offline_search_has_a_single_page(client: TMDBClient) -> None:
    assert client.search_movie("The Matrix", page=2)["results"] == []


def test_offline_series_and_seasons(client: TMDBClient) -> None:
    assert ids(client.search_tv("Breaking Bad")) == [1396]
    assert ids(client.search_tv("Breaking Bad", first_air_date_year=2008)) == [1396]
    assert ids(client.search_tv("Show")) == [9000001]
    series = client.tv_details(1396)
    assert series["external_ids"]["tvdb_id"] == 81189
    assert series["number_of_seasons"] == 5
    first, second = client.tv_season(1396, 1), client.tv_season(1396, 2)
    assert [e["episode_number"] for e in first["episodes"]] == list(range(1, 8))
    assert len(second["episodes"]) == 13
    assert first["episodes"][0]["name"] == "Pilot"
    assert all(e["still_path"] for e in first["episodes"])
    show_season = client.tv_season(9000001, 2)
    assert [e["name"] for e in show_season["episodes"]] == ["Episode 1", "Episode 2"]


def test_offline_details_carry_every_appended_block(client: TMDBClient) -> None:
    details = client.movie_details(603)
    assert "_fixture" not in details
    assert set(MOVIE_APPEND) <= set(details)
    certifications = {
        entry["iso_3166_1"]: entry["release_dates"][0]["certification"]
        for entry in details["release_dates"]["results"]
    }
    assert certifications == {"US": "R", "SA": "R15"}
    arabic = next(t for t in details["translations"]["translations"] if t["iso_639_1"] == "ar")
    assert arabic["data"]["title"] == "المصفوفة"
    directors = [c["name"] for c in details["credits"]["crew"] if c["job"] == "Director"]
    assert directors == ["Lana Wachowski", "Lilly Wachowski"]
    wadjda = client.movie_details(129112)
    assert (wadjda["original_language"], wadjda["original_title"]) == ("ar", "وجدة")


def test_offline_unknown_resources_are_404(client: TMDBClient) -> None:
    with pytest.raises(TMDBNotFoundError):
        client.movie_details(1)
    with pytest.raises(TMDBNotFoundError):
        client.tv_season(1396, 3)


def test_offline_genres_in_english_and_arabic(client: TMDBClient) -> None:
    english = {g["id"]: g["name"] for g in client.movie_genres()["genres"]}
    arabic = {g["id"]: g["name"] for g in client.movie_genres(language="ar-SA")["genres"]}
    assert english[878] == "Science Fiction"
    assert arabic[878] == "خيال علمي"
    assert set(english) == set(arabic)
    tv = {g["id"]: g["name"] for g in client.tv_genres(language="ar-SA")["genres"]}
    assert tv[18] == "دراما"


def test_offline_find_by_external_id(client: TMDBClient) -> None:
    assert [m["id"] for m in client.find("tt0133093", "imdb_id")["movie_results"]] == [603]
    assert [t["id"] for t in client.find("81189", "tvdb_id")["tv_results"]] == [1396]


def test_every_fixture_says_it_is_synthetic() -> None:
    files = sorted(DEFAULT_FIXTURES_DIR.rglob("*.json"))
    assert len(files) >= 20
    for path in files:
        note = json.loads(path.read_text(encoding="utf-8"))["_fixture"]
        assert note["synthetic"] is True, path.name
        assert "not TMDB data" in note["note"]


def test_fixture_images_are_drawn_at_the_requested_size(client: TMDBClient) -> None:
    poster = client.movie_details(603)["poster_path"]
    with client.open_image_client() as http:
        small = http.get(image_url(poster, "w185"))
        original = http.get(image_url(poster))
        again = http.get(image_url(poster))
        missing = http.get(image_url("/unknown.jpg"))
    assert small.headers["Content-Type"] == "image/jpeg"
    assert Image.open(io.BytesIO(small.content)).size == (185, 278)
    assert Image.open(io.BytesIO(original.content)).size == (600, 900)
    assert original.content == again.content  # deterministic bytes
    assert missing.status_code == 404


SAMPLE_MEDIA = [
    ("The.Matrix.1999.1080p.BluRay.x265.mkv", "movies", 603),
    ("Blade.Runner.2049.2017.2160p.UHD.BluRay.x265.10bit.HDR.mkv", "movies", 335984),
    ("Inception (2010)/Inception.2010.1080p.BluRay.DTS.x264.mkv", "movies", 27205),
    ("وجدة (2012)/وجدة.2012.1080p.WEB-DL.mkv", "movies", 129112),
    ("Breaking Bad/Season 01/Breaking.Bad.S01E01.720p.mkv", "series", 1396),
    ("Breaking Bad/Season 01/Breaking.Bad.1x02.mkv", "series", 1396),
    ("Show/Season 02/Show.S02E01E02.mkv", "series", 9000001),
]


@pytest.mark.parametrize(("path", "library_kind", "tmdb_id"), SAMPLE_MEDIA)
def test_sample_media_matches_offline(
    client: TMDBClient, path: str, library_kind: LibraryKind, tmdb_id: int
) -> None:
    """Parse -> search -> details -> score, the way the matcher will run it."""
    parsed = parse_path(path, library_kind=library_kind)
    assert parsed.title
    if parsed.kind == "movie":
        results = client.search_movie(parsed.title, year=parsed.year)["results"]
        candidates = [Candidate.from_tmdb(client.movie_details(r["id"]), "movie") for r in results]
    else:
        results = client.search_tv(parsed.title, first_air_date_year=parsed.year)["results"]
        candidates = [Candidate.from_tmdb(client.tv_details(r["id"]), "tv") for r in results]
    query = MatchQuery(parsed.title, parsed.year, runtime_min=SAMPLE_RUNTIME_MIN)
    decision = decide(query, candidates)
    assert decision.reason == "auto_accepted", decision.to_json()
    assert decision.accepted is not None
    assert decision.accepted.candidate.provider_id == tmdb_id


def test_recording_writes_what_fixture_mode_serves(tmp_path: Path) -> None:
    """Record through a stand-in for the live API, then serve the recording offline."""
    saved = record_sample_fixtures(api_key="recording-key", inner=FixtureTransport(), root=tmp_path)
    assert tmp_path / "movie" / "603.json" in saved
    assert tmp_path / "tv" / "1396" / "season" / "2.json" in saved
    assert tmp_path / "genre" / "tv" / "list.ar-SA.json" in saved
    for path in saved:
        text = path.read_text(encoding="utf-8")
        assert "recording-key" not in text
        assert json.loads(text)["_fixture"]["synthetic"] is False
    recorded = TMDBClient.offline(tmp_path)
    reference = TMDBClient.offline()
    for title in SAMPLE_TITLES:
        if title.kind == "movie":
            assert recorded.movie_details(title.tmdb_id) == reference.movie_details(title.tmdb_id)
            assert ids(recorded.search_movie(title.query, year=title.year)) == [title.tmdb_id]
        else:
            assert recorded.tv_details(title.tmdb_id) == reference.tv_details(title.tmdb_id)
            for season in title.seasons:
                assert recorded.tv_season(title.tmdb_id, season) == reference.tv_season(
                    title.tmdb_id, season
                )
    queries = json.loads((tmp_path / "search" / "movie.json").read_text(encoding="utf-8"))
    assert set(queries["queries"]) == {"matrix", "inception", "blade runner 2049", "وجده"}


def test_factory_uses_fixture_mode_without_credentials(settings: Settings) -> None:
    settings.TMDB_API_KEY = ""
    settings.TMDB_READ_ACCESS_TOKEN = ""
    with client_from_settings() as client:
        assert client.fixture_mode
        assert ids(client.search_movie("The Matrix", year=1999)) == [603]


def test_factory_builds_a_live_client_from_a_credential(settings: Settings) -> None:
    settings.TMDB_READ_ACCESS_TOKEN = "token"  # noqa: S105
    settings.TMDB_RATE_LIMIT_PER_S = 10
    with client_from_settings() as client:
        assert not client.fixture_mode
