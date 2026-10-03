"""Matching, enrichment, artwork and the review queue over the offline fixtures
(SPEC §7.2 steps 4-9; apps.metadata.services)."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.catalog.models import (
    Category,
    CategoryKind,
    Credit,
    Episode,
    FileState,
    MatchReview,
    MediaFile,
    MediaImage,
    MetadataSource,
    Movie,
    ReviewStatus,
    Series,
    TitleStatus,
)
from apps.core.errors import ErrorCode, ProblemError
from apps.core.services import set_setting
from apps.library.models import Library
from apps.library.parsing import ParseResult, parse_path
from apps.metadata import services
from apps.metadata.tmdb import TMDBClient

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("offline_tmdb")]


def make_file(
    library: Library,
    key: str,
    *,
    parsed: ParseResult | None = None,
    duration_ms: int = 30_000,
    direct_play: bool = False,
) -> MediaFile:
    parsed = parsed or parse_path(key, library_kind=library.kind)  # type: ignore[arg-type]
    return MediaFile.objects.create(
        library=library,
        storage_key=key,
        parse_result=parsed.to_json(),
        duration_ms=duration_ms,
        direct_play=direct_play,
        state=FileState.MATCHING,
    )


@pytest.fixture
def movies(make_library: Callable[..., Library]) -> Library:
    return make_library()


@pytest.fixture
def series_library(make_library: Callable[..., Library]) -> Library:
    return make_library("Series", "series", "series")


def test_a_movie_with_its_year_is_matched_and_enriched(
    movies: Library,
    django_capture_on_commit_callbacks: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.metadata import tasks  # noqa: PLC0415

    queued: list[tuple[str, str]] = []
    monkeypatch.setattr(tasks.fetch_title_images, "delay", lambda *a: queued.append(a))
    featured = Category.objects.create(
        kind=CategoryKind.VOD, slug="featured", name_en="Featured", name_ar="مميز"
    )
    movies.default_categories.add(featured)
    file = make_file(movies, "The.Matrix.1999.1080p.BluRay.x265.mkv")
    with django_capture_on_commit_callbacks(execute=True):
        services.match_file(file)
    file.refresh_from_db()
    movie = Movie.objects.get(tmdb_id=603)
    assert (file.movie_id, file.state, file.match_confidence) == (movie.pk, FileState.MATCHED, 1.0)
    assert (movie.title, movie.title_ar, movie.year, movie.runtime_min) == (
        "The Matrix",
        "المصفوفة",
        1999,
        136,
    )
    assert movie.overview_ar
    assert movie.tagline_ar
    assert (movie.imdb_id, movie.certification, movie.countries) == ("tt0133093", "R15", ["US"])
    assert movie.metadata_source == MetadataSource.SYNTHETIC
    assert movie.status == TitleStatus.PROCESSING  # an HEVC MKV is not direct play
    assert movie.alt_titles == ["المصفوفة", "ذا ماتريكس"]
    assert isinstance(movie.xc_id, int)
    genres = {g.tmdb_id: (g.name_en, g.name_ar) for g in movie.genres.all()}
    assert genres == {28: ("Action", "أكشن"), 878: ("Science Fiction", "خيال علمي")}
    slugs = set(movie.categories.values_list("slug", flat=True))
    assert slugs == {"action", "sci-fi", "featured"}
    roles = sorted(Credit.objects.filter(movie=movie).values_list("role", flat=True))
    assert roles.count("cast") == 4
    assert roles.count("director") == 2
    assert roles.count("writer") == 2
    assert ("movie", str(movie.pk)) in queued


def test_matching_again_reuses_the_movie(movies: Library, offline_tmdb: TMDBClient) -> None:
    first = make_file(movies, "The.Matrix.1999.1080p.BluRay.x265.mkv")
    second = make_file(movies, "The Matrix (1999)/The.Matrix.1999.720p.mkv")
    services.match_file(first)
    services.match_file(second)
    assert Movie.objects.count() == 1
    assert MediaFile.objects.filter(movie__tmdb_id=603).count() == 2


def test_a_name_shared_with_sequels_goes_to_review(movies: Library) -> None:
    file = make_file(movies, "The Matrix.mp4")
    services.match_file(file)
    file.refresh_from_db()
    review = MatchReview.objects.get(media_file=file)
    assert (file.state, review.status, review.reason, review.kind) == (
        FileState.REVIEW,
        ReviewStatus.OPEN,
        "ambiguous",
        "movie",
    )
    assert [c["id"] for c in review.candidates] == [603, 624860, 604, 605]
    top = review.candidates[0]
    assert top["breakdown"]["title"] == 1.0
    assert top["breakdown"]["runtime"] is None  # only 603 has details in the fixtures
    assert file.match_confidence == top["score"]
    assert review.parse_result["title"] == "The Matrix"
    # Matching again updates the same open review.
    file.state = FileState.MATCHING
    file.save()
    services.match_file(file)
    assert MatchReview.objects.filter(media_file=file).count() == 1


def test_thresholds_come_from_the_settings(
    movies: Library, django_capture_on_commit_callbacks: Callable[..., Any]
) -> None:
    file = make_file(movies, "Blade Runner 2049.mkv")
    services.match_file(file)
    assert MatchReview.objects.get(media_file=file).reason == "ambiguous"
    with django_capture_on_commit_callbacks(execute=True):  # settings apply on commit
        set_setting("metadata.match_margin", 0.0, actor=None)
    file.state = FileState.MATCHING
    file.save()
    services.match_file(file)
    file.refresh_from_db()
    assert file.movie is not None
    assert file.movie.tmdb_id == 335984
    assert MatchReview.objects.get(media_file=file).status == ReviewStatus.RESOLVED


@pytest.mark.parametrize(
    ("key", "tmdb_id"),
    [("Some Film [tmdbid-27205].mkv", 27205), ("Other {imdb-tt0133093}.mkv", 603)],
)
def test_provider_ids_match_with_full_confidence(movies: Library, key: str, tmdb_id: int) -> None:
    file = make_file(movies, key)
    services.match_file(file)
    file.refresh_from_db()
    assert file.movie is not None
    assert (file.movie.tmdb_id, file.match_confidence) == (tmdb_id, 1.0)


@pytest.mark.parametrize(
    ("parsed", "reason"),
    [
        (ParseResult(kind="movie", title="Zzyzx Nowhere Film", year=1990), "no_candidates"),
        (ParseResult(kind="movie", title=None), "no_candidates"),
        (ParseResult(kind="movie", title="The Matrix", year=1999, ambiguous=True),
         "classification"),
        (ParseResult(kind="ignore"), "classification"),
    ],
)  # fmt: skip
def test_undecidable_movies_go_to_review(movies: Library, parsed: ParseResult, reason: str) -> None:
    file = make_file(movies, "x.mkv", parsed=parsed)
    services.match_file(file)
    review = MatchReview.objects.get(media_file=file)
    assert review.reason == reason
    file.refresh_from_db()
    assert file.state == FileState.REVIEW


def test_an_unknown_provider_id_goes_to_review(movies: Library) -> None:
    file = make_file(movies, "Lost [tmdbid-999999999].mkv")
    services.match_file(file)
    assert MatchReview.objects.get(media_file=file).reason == "no_candidates"


def test_an_episode_creates_its_series_season_and_episodes(
    series_library: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    file = make_file(series_library, "Breaking Bad/Season 01/Breaking.Bad.S01E01.720p.mkv")
    services.match_file(file)
    file.refresh_from_db()
    series = Series.objects.get(tmdb_id=1396)
    assert (series.title, series.title_ar, series.tvdb_id, series.imdb_id) == (
        "Breaking Bad",
        "بريكنج باد",
        81189,
        "tt0903747",
    )
    assert series.certification == "TV-MA"  # no SA rating: the US one
    assert series.year == 2008
    assert series.seasons.count() == 5
    season = series.seasons.get(number=1)
    assert season.episodes.count() == 7
    episode = file.episodes.get()
    assert (episode.season_id, episode.number, episode.title) == (season.pk, 1, "Pilot")
    assert episode.title_ar == ""  # the fixtures fall back to English: no Arabic
    assert episode.still_path
    assert file.state == FileState.MATCHED
    writers = Credit.objects.filter(series=series, role="writer")
    assert [c.person.name for c in writers] == ["Vince Gilligan"]
    assert set(series.categories.values_list("slug", flat=True)) >= {"drama"}
    # The next episode reuses the series and season.
    second = make_file(series_library, "Breaking Bad/Season 01/Breaking.Bad.1x02.mkv")
    services.match_file(second)
    assert Series.objects.count() == 1
    assert second.episodes.get().number == 2


def test_a_double_episode_file_links_both(series_library: Library) -> None:
    file = make_file(series_library, "Show/Season 02/Show.S02E01E02.mkv")
    services.match_file(file)
    assert sorted(file.episodes.values_list("number", flat=True)) == [1, 2]
    assert Series.objects.get().title_ar == "العرض"


@pytest.mark.parametrize(
    ("parsed", "reason"),
    [
        (ParseResult(kind="episode", title="Breaking Bad", season=1, episodes=(99,)),
         "episode_not_found"),
        (ParseResult(kind="episode", title="Breaking Bad", season=9, episodes=(1,)),
         "episode_not_found"),
        (ParseResult(kind="episode", title="Breaking Bad", absolute_episode=5), "numbering"),
        (ParseResult(kind="episode", title="Unknown Show Zzyzx", season=1, episodes=(1,)),
         "no_candidates"),
        (ParseResult(kind="episode", title=None, season=1, episodes=(1,)), "no_candidates"),
        (ParseResult(kind="episode", title="Breaking Bad", season=1, episodes=(1,),
                     ambiguous=True), "classification"),
    ],
)  # fmt: skip
def test_unplaceable_episodes_go_to_review(
    series_library: Library, parsed: ParseResult, reason: str
) -> None:
    file = make_file(series_library, "x.mkv", parsed=parsed)
    services.match_file(file)
    assert MatchReview.objects.get(media_file=file).reason == reason
    assert not file.episodes.exists()


def test_episode_provider_ids(series_library: Library) -> None:
    for key in ("Breaking Bad [tvdbid-81189]/S01E03.mkv", "Breaking Bad [tmdbid-1396]/S01E04.mkv"):
        file = make_file(series_library, key)
        services.match_file(file)
        file.refresh_from_db()
        assert file.match_confidence == 1.0, key
    assert Series.objects.count() == 1


def test_resolving_a_review_links_the_file(movies: Library, staff_user: User) -> None:
    file = make_file(movies, "The Matrix.mp4", direct_play=True)
    services.match_file(file)
    review = MatchReview.objects.get(media_file=file)
    services.resolve_review(review, 603, actor=staff_user, ip="203.0.113.5")
    review.refresh_from_db()
    file.refresh_from_db()
    assert (review.status, review.chosen_provider_id, review.decided_by) == (
        ReviewStatus.RESOLVED,
        603,
        staff_user,
    )
    assert file.movie is not None
    assert (file.movie.tmdb_id, file.match_confidence, file.state) == (603, 1.0, FileState.MATCHED)
    assert file.movie.status == TitleStatus.READY  # a direct-play file
    entry = AuditLog.objects.get(action="review.resolve")
    assert entry.after == {"kind": "movie", "tmdb_id": 603, "file": str(file.pk)}
    with pytest.raises(ProblemError) as caught:
        services.resolve_review(review, 603, actor=staff_user, ip=None)
    assert caught.value.problem_code == ErrorCode.CONFLICT


def test_resolving_as_a_series_episode(movies: Library) -> None:
    parsed = ParseResult(kind="episode", title="Show", season=2, episodes=(1, 2), ambiguous=True)
    file = make_file(movies, "Show.S02E01E02.mkv", parsed=parsed)
    services.match_file(file)
    review = MatchReview.objects.get(media_file=file)
    assert review.kind == "tv"
    services.resolve_review(review, 9000001, actor=None, ip=None)
    assert sorted(file.episodes.values_list("number", flat=True)) == [1, 2]


def test_resolving_with_a_bad_choice_keeps_the_review_open(movies: Library) -> None:
    file = make_file(movies, "The Matrix.mp4")
    services.match_file(file)
    review = MatchReview.objects.get(media_file=file)
    with pytest.raises(ProblemError) as caught:
        services.resolve_review(review, 999999999, actor=None, ip=None)
    assert caught.value.problem_code == ErrorCode.VALIDATION_ERROR
    with pytest.raises(ProblemError) as caught:
        services.resolve_review(review, 1396, kind="tv", actor=None, ip=None)
    assert caught.value.problem_code == ErrorCode.VALIDATION_ERROR  # no season/episode
    review.refresh_from_db()
    assert review.status == ReviewStatus.OPEN


def test_skipping_a_review(movies: Library, staff_user: User) -> None:
    file = make_file(movies, "The Matrix.mp4")
    services.match_file(file)
    review = MatchReview.objects.get(media_file=file)
    services.skip_review(review, actor=staff_user, ip=None)
    review.refresh_from_db()
    assert (review.status, review.decided_by) == (ReviewStatus.SKIPPED, staff_user)
    assert AuditLog.objects.filter(action="review.skip").exists()
    with pytest.raises(ProblemError):
        services.skip_review(review, actor=staff_user, ip=None)


def test_refresh_keeps_locked_fields(movies: Library) -> None:
    file = make_file(movies, "The.Matrix.1999.1080p.BluRay.x265.mkv")
    services.match_file(file)
    movie = Movie.objects.get()
    Movie.objects.filter(pk=movie.pk).update(
        title="Matrix (edited)", overview="old", metadata_locked_fields=["title"]
    )
    movie.refresh_from_db()
    services.refresh_movie(movie)
    movie.refresh_from_db()
    assert movie.title == "Matrix (edited)"
    assert movie.overview.startswith("Synthetic overview")
    series_file = make_file(movies, "Show.S02E01.mkv", parsed=ParseResult(
        kind="episode", title="Show", season=2, episodes=(1,)))  # fmt: skip
    services.match_file(series_file)
    series = Series.objects.get()
    Series.objects.filter(pk=series.pk).update(title_ar="معدل", metadata_locked_fields=["title_ar"])
    series.refresh_from_db()
    services.refresh_series(series)
    series.refresh_from_db()
    assert series.title_ar == "معدل"
    assert series.seasons.get(number=2).episodes.count() == 2


def test_refresh_needs_a_tmdb_id() -> None:
    with pytest.raises(ProblemError):
        services.refresh_movie(Movie.objects.create(title="Home video"))
    with pytest.raises(ProblemError):
        services.refresh_series(Series.objects.create(title="Home series"))


def test_genre_categories_follow_the_setting(movies: Library) -> None:
    set_setting("metadata.genre_category_map", '{"878": "space"}', actor=None)
    services.match_file(make_file(movies, "The.Matrix.1999.mkv"))
    category = Category.objects.get(slug="space")
    assert (category.kind, category.name_en, category.name_ar) == (
        "vod",
        "Science Fiction",
        "خيال علمي",
    )
    assert list(Movie.objects.get().categories.values_list("slug", flat=True)) == ["space"]


def test_movie_artwork_is_stored(movies: Library, data_root: Path) -> None:
    services.match_file(make_file(movies, "The.Matrix.1999.mkv"))
    movie = Movie.objects.get()
    assert services.fetch_movie_images(movie) == 4
    images = {(i.kind, i.language, i.is_primary) for i in MediaImage.objects.filter(movie=movie)}
    assert images == {
        ("poster", "en", True),
        ("poster", "ar", False),
        ("backdrop", "", True),
        ("logo", "en", True),
    }
    poster = MediaImage.objects.get(movie=movie, kind="poster", is_primary=True)
    key = poster.sizes["w500"]["webp"]
    assert key.startswith(f"images/movie/{movie.pk}/poster/w500.")
    assert (data_root / key).is_file()
    assert (poster.width, poster.height) == (600, 900)
    assert poster.blurhash
    assert services.fetch_movie_images(movie) == 0  # already stored
    assert services.fetch_movie_images(Movie.objects.create(title="No TMDB")) == 0


def test_series_artwork_covers_seasons_and_stills_with_files(
    series_library: Library, data_root: Path
) -> None:
    services.match_file(make_file(series_library, "Breaking Bad/Season 01/Breaking.Bad.S01E01.mkv"))
    series = Series.objects.get()
    added = services.fetch_series_images(series)
    assert added == 6  # en and ar posters, backdrop, logo, season 1 poster, the pilot's still
    assert MediaImage.objects.filter(season__number=1, kind="poster").exists()
    still = MediaImage.objects.get(kind="still")
    assert still.episode == Episode.objects.get(files__isnull=False)
    assert services.fetch_series_images(Series.objects.create(title="No TMDB")) == 0


def test_failed_downloads_are_skipped(movies: Library, data_root: Path) -> None:
    services.match_file(make_file(movies, "The.Matrix.1999.mkv"))
    movie = Movie.objects.get()
    picks = [services.ImagePick("poster", "/missing-image-without-size.jpg", "", True)]
    assert services.store_images({"movie": movie}, f"movie/{movie.pk}", picks, services.tmdb()) == 0


def test_search_for_manual_matching() -> None:
    movies = services.search("movie", "The Matrix")
    assert [m["id"] for m in movies] == [603, 604, 605, 624860]
    assert movies[0]["title"] == "The Matrix"
    assert movies[0]["year"] == 1999
    shows = services.search("tv", "Breaking Bad", 2008)
    assert [s["id"] for s in shows] == [1396]
    assert services.poster_preview_url(movies[0]["poster_path"]) is None  # fixture mode
    assert services.poster_preview_url(None) is None


def test_the_task_body_matches_waiting_files_only(movies: Library) -> None:
    file = make_file(movies, "The.Matrix.1999.mkv")
    MediaFile.objects.filter(pk=file.pk).update(state=FileState.PENDING)
    services.match_file_task_body(str(file.pk))
    assert not Movie.objects.exists()
    MediaFile.objects.filter(pk=file.pk).update(state=FileState.MATCHING)
    services.match_file_task_body(str(file.pk))
    assert Movie.objects.exists()
    services.match_file_task_body("00000000-0000-0000-0000-000000000000")
