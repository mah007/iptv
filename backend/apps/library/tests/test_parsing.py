"""Parser behaviour the filename ground truth does not pin down."""

from datetime import date

import pytest

from apps.library.parsing import VIDEO_EXTENSIONS, ParseResult, ProviderIds, parse_path


@pytest.mark.parametrize("extension", sorted(VIDEO_EXTENSIONS))
def test_every_spec_video_extension_is_accepted(extension: str) -> None:
    result = parse_path(f"The.Matrix.1999.{extension.upper()}")
    assert result.kind == "movie"
    assert result.container == extension


@pytest.mark.parametrize(
    ("path", "reason"),
    [
        ("The.Matrix.1999.iso", "extension"),
        ("README", "extension"),
        ("", "extension"),
        ("Movies/.Trash-1000/x.mkv", "hidden"),
        ("#snapshot/Movie (2010).mkv", "hidden"),
        ("Show/Season 01/Samples/x.mkv", "sample"),
        ("Movie (2010)/Making Of/x.mkv", "extra"),
        ("Movie (2010)/Movie (2010)-clip.mkv", "extra"),
    ],
)
def test_ignored_paths_say_why(path: str, reason: str) -> None:
    result = parse_path(path)
    assert result.kind == "ignore"
    assert result.ignore_reason == reason
    assert result.title is None


def test_library_kind_forces_the_kind_and_flags_disagreement() -> None:
    movie = parse_path("Breaking.Bad.S01E01.720p.mkv", library_kind="movies")
    assert (movie.kind, movie.ambiguous) == ("movie", True)

    episode = parse_path("The.Matrix.1999.mkv", library_kind="series")
    assert (episode.kind, episode.ambiguous) == ("episode", True)

    sequel = parse_path("Star.Wars.Episode.IV.A.New.Hope.1977.mkv", library_kind="movies")
    assert (sequel.kind, sequel.ambiguous) == ("movie", False)


def test_mixed_library_decides_from_the_name() -> None:
    assert parse_path("The.Matrix.1999.mkv").kind == "movie"
    assert parse_path("Breaking.Bad.S01E01.mkv").kind == "episode"
    assert parse_path("Show/Season 1/5.mkv").kind == "episode"
    assert parse_path("Show 2024-03-12.mkv").kind == "episode"


def test_provider_tag_closest_to_the_file_wins() -> None:
    result = parse_path(
        "Show [tvdbid-1] {tmdb-2}/Season 1/Show S01E01 [tvdbid-81189] [imdbid-0903747].mkv"
    )
    assert result.provider_ids == ProviderIds(tmdb=2, imdb="tt0903747", tvdb=81189)
    assert result.title == "Show"


@pytest.mark.parametrize(
    "path",
    [
        "Movie (2010) [tmdbid-603].mkv",
        "Movie (2010) [tmdbid=603].mkv",
        "Movie (2010) {tmdb-603}.mkv",
        "Movie (2010) (tmdb:603).mkv",
    ],
)
def test_provider_tag_spellings(path: str) -> None:
    result = parse_path(path)
    assert result.provider_ids.tmdb == 603
    assert (result.title, result.year) == ("Movie", 2010)


def test_provider_ids_are_falsy_when_absent() -> None:
    assert not parse_path("The.Matrix.1999.mkv").provider_ids
    assert parse_path("The Matrix (1999) {imdb-tt0133093}.mkv").provider_ids


def test_release_details_are_extracted() -> None:
    result = parse_path("Movie.2010.2160p.UHD.BluRay.REMUX.HDR.HEVC.Atmos.mkv")
    assert (result.resolution, result.source, result.codec) == (
        "2160p",
        "Ultra HD Blu-ray",
        "H.265",
    )


@pytest.mark.parametrize(
    ("path", "languages", "subtitles"),
    [
        ("Frozen.2013.1080p.BluRay.x264.ARABIC.DUBBED.mkv", ("ar",), ()),
        ("Movie.2020.1080p.Arabic.Subs.mkv", (), ("ar",)),
        ("فيلم الرسالة 1976 مدبلج.mkv", ("ar",), ()),
        ("الرسالة 1976 مترجم.mkv", (), ("ar",)),
        ("Movie.2020.MULTi.1080p.mkv", ("mul",), ()),
        ("Star.Trek.Discovery.S01E01.1080p.WEB-DL.mkv", (), ()),
    ],
)
def test_language_tags(path: str, languages: tuple[str, ...], subtitles: tuple[str, ...]) -> None:
    result = parse_path(path)
    assert result.languages == languages
    assert result.subtitle_languages == subtitles


def test_language_tags_never_leak_into_titles() -> None:
    assert parse_path("Movie.2020.ARABIC.1080p.mkv").title == "Movie"
    assert parse_path("Show.S01E01.ARABIC.1080p.mkv").episode_title is None
    assert parse_path("فيلم الرسالة 1976 مدبلج.mkv").title == "الرسالة"


def test_multi_part_movie_files_keep_their_part() -> None:
    assert parse_path("Some Movie (2010)/CD2.mkv", library_kind="movies").part == 2
    assert parse_path("Some Movie (2010) - Part 1.mkv").part == 1
    sequel = parse_path("Dune.Part.Two.2024.mkv")
    assert (sequel.title, sequel.part) == ("Dune Part Two", None)


def test_explicit_edition_tag_is_verbatim_and_beats_guessit() -> None:
    result = parse_path("Aliens (1986) {edition-Extended Special Cut}.mkv")
    assert result.edition == "Extended Special Cut"


@pytest.mark.parametrize(
    ("folder", "season"),
    [
        ("Season 3", 3),
        ("season.03", 3),
        ("S03", 3),
        ("Staffel 3", 3),
        ("Specials", 0),
        ("الموسم 3", 3),
        ("الموسم الثالث", 3),
        ("الجزء الثاني", 2),
        ("الموسم ٣", 3),
    ],
)
def test_season_folders(folder: str, season: int) -> None:
    result = parse_path(f"Show/{folder}/Show - E05.mkv")
    assert (result.title, result.season, result.episodes) == ("Show", season, (5,))


def test_old_style_three_digit_numbers_inside_a_season_folder() -> None:
    assert parse_path("Show/Season 01/Show - 101.mkv").episodes == (1,)
    assert parse_path("Show/Season 2/Show - 25.mkv").episodes == (25,)
    absolute = parse_path("Show - 101.mkv")
    assert (absolute.season, absolute.episodes, absolute.absolute_episode) == (None, (), 101)


def test_specials_without_a_number_are_not_ambiguous() -> None:
    result = parse_path("Show/Specials/Behind the Show.mkv", library_kind="series")
    assert (result.season, result.episodes, result.ambiguous) == (0, (), False)


def test_episode_without_any_number_is_ambiguous() -> None:
    result = parse_path("Show/Season 1/whatever.mkv")
    assert result.kind == "episode"
    assert result.ambiguous


def test_dated_episode() -> None:
    result = parse_path("The Daily Show/The.Daily.Show.2024.03.12.Guest.720p.mkv")
    assert result.air_date == date(2024, 3, 12)
    assert (result.season, result.episodes, result.absolute_episode) == (None, (), None)
    assert result.episode_title == "Guest"


def test_year_numbered_season_folder_is_not_the_series_year() -> None:
    result = parse_path("The Tonight Show/Season 2024/The.Tonight.Show.2024.03.12.mkv")
    assert (result.season, result.year) == (2024, None)


def test_windows_separators_and_leading_slash_are_accepted() -> None:
    result = parse_path("\\Breaking Bad\\Season 01\\Breaking.Bad.S01E02.mkv")
    assert (result.title, result.season, result.episodes) == ("Breaking Bad", 1, (2,))


def test_json_round_trip() -> None:
    for path in (
        "The Matrix (1999) [tmdbid-603]/The.Matrix.1999.1080p.BluRay.x265.mkv",
        "The Daily Show/The.Daily.Show.2024.03.12.mkv",
        "Show.S01E01E02.ARABIC.mkv",
        "The.Matrix.1999.nfo",
    ):
        result = parse_path(path)
        assert ParseResult.from_json(result.to_json()) == result


def test_json_is_plain_data() -> None:
    data = parse_path("The Daily Show/The.Daily.Show.2024.03.12.mkv").to_json()
    assert data["air_date"] == "2024-03-12"
    assert data["episodes"] == []
    assert data["provider_ids"] == {"tmdb": None, "imdb": None, "tvdb": None}


def test_from_json_ignores_unknown_keys() -> None:
    data = parse_path("The.Matrix.1999.mkv").to_json() | {"future_field": 1}
    assert ParseResult.from_json(data).title == "The Matrix"
