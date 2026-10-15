"""TheTVDB as the TV fallback (ADR-0023), over the offline TMDB and TheTVDB fixtures:
when it is asked, the cross-walk to TMDB, TheTVDB-sourced series, reviews with both
providers, manual matching, artwork, refresh and the episode order."""

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from django.conf import settings

from apps.audit.models import AuditLog
from apps.catalog.models import (
    Credit,
    CreditRole,
    Episode,
    EpisodeOrdering,
    FileState,
    ImageKind,
    ImageSource,
    MatchReview,
    MediaFile,
    MediaImage,
    MetadataSource,
    Person,
    ReviewKind,
    ReviewStatus,
    Series,
)
from apps.core.errors import ErrorCode, ProblemError
from apps.library.models import Library
from apps.library.parsing import ParseResult, parse_path
from apps.metadata import services, tvdb_series
from apps.metadata.scoring import Candidate
from apps.metadata.tvdb import TVDBClient, TVDBServerError

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("offline_tvdb")]
ADMIN = {"host": settings.ADMIN_HOST}


def make_file(library: Library, key: str, parsed: ParseResult | None = None) -> MediaFile:
    parsed = parsed or parse_path(key, library_kind=library.kind)  # type: ignore[arg-type]
    return MediaFile.objects.create(
        library=library,
        storage_key=key,
        parse_result=parsed.to_json(),
        duration_ms=42 * 60_000,
        state=FileState.MATCHING,
    )


@pytest.fixture
def shows(make_library: Callable[..., Library]) -> Library:
    return make_library("Series", "series", "series")


@pytest.fixture(autouse=True)
def _no_image_tasks(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    from apps.metadata import tasks  # noqa: PLC0415

    queued: list[tuple[str, str]] = []
    monkeypatch.setattr(tasks.fetch_title_images, "delay", lambda *a: queued.append(a))
    return queued


def test_a_series_tmdb_does_not_know_is_built_from_tvdb(shows: Library) -> None:
    file = make_file(shows, "The Lantern Keepers/Season 01/The.Lantern.Keepers.S01E02.mkv")
    services.match_file(file)
    file.refresh_from_db()
    assert file.state == FileState.MATCHED
    series = Series.objects.get(tvdb_id=9100001)
    assert series.tmdb_id is None
    assert series.metadata_source == MetadataSource.TVDB
    assert series.episode_ordering == EpisodeOrdering.TVDB_AIRED
    assert series.title == "The Lantern Keepers"
    assert series.title_ar == "حراس الفانوس"
    assert series.overview_ar.startswith("ثلاثة إخوة")
    assert series.imdb_id == "tt99100001"
    assert series.countries == ["SA"]
    assert series.original_language == "ar"
    assert series.certification == "PG"  # metadata.certification_country: SA
    assert series.trailer_youtube_key == "lAnTeRn0001"
    assert series.year == 2021
    assert series.episode_run_time == 42
    assert "Lantern Keepers" in series.alt_titles
    assert list(series.genres.order_by("tmdb_id").values_list("tmdb_id", flat=True)) == [18, 35]
    # The season and its episodes, in TheTVDB's aired order, with Arabic names.
    episodes = list(Episode.objects.filter(season__series=series).order_by("number"))
    assert [(e.season.number, e.number) for e in episodes] == [(1, 1), (1, 2), (1, 3)]
    second = episodes[1]
    assert list(file.episodes.all()) == [second]
    assert second.tvdb_id == 9500102
    assert second.title == "Oil and Wick"
    assert second.title_ar == "الزيت والفتيل"
    assert second.absolute_number == 2
    assert second.still_path.startswith("https://artworks.thetvdb.com/")
    # Cast and the director, as people of TheTVDB.
    roles = {(c.person.name, c.role, c.character) for c in Credit.objects.filter(series=series)}
    assert roles == {
        ("Noura Haddad", CreditRole.CAST, "Salma"),
        ("Omar Saleh", CreditRole.CAST, "Fahad"),
        ("Rania Fares", CreditRole.DIRECTOR, ""),
    }
    assert Person.objects.filter(tvdb_id=9400001, tmdb_id=None).exists()
    assert not MatchReview.objects.filter(media_file=file, status=ReviewStatus.OPEN).exists()


def test_an_arabic_file_name_matches_through_the_translations(shows: Library) -> None:
    parsed = ParseResult(kind="episode", title="حراس الفانوس", season=1, episodes=(1,))
    file = make_file(shows, "حراس الفانوس/S01E01.mkv", parsed)
    services.match_file(file)
    file.refresh_from_db()
    assert file.state == FileState.MATCHED
    assert file.episodes.get().title_ar == "المتجر"


def test_a_tvdb_match_that_knows_its_tmdb_id_is_handed_back_to_tmdb(shows: Library) -> None:
    file = make_file(shows, "La Quimica/Season 01/La.Quimica.S01E01.mkv")
    services.match_file(file)
    file.refresh_from_db()
    assert file.state == FileState.MATCHED
    series = file.episodes.get().season.series
    assert series.tmdb_id == 1396
    assert series.metadata_source == MetadataSource.SYNTHETIC  # TMDB's fixture mode
    review = MatchReview.objects.filter(media_file=file).first()
    assert review is None or review.chosen_provider == "tmdb"
    # The next file finds the series by the TheTVDB id TMDB recorded: no second build.
    other = make_file(shows, "La Quimica/Season 01/La.Quimica.S01E02.mkv")
    services.match_file(other)
    other.refresh_from_db()
    assert other.episodes.get().season.series == series
    assert Series.objects.count() == 1


def test_without_a_tvdb_key_the_review_queue_gets_the_file(
    shows: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tvdb_series, "tvdb", lambda: None)
    file = make_file(shows, "The Lantern Keepers/Season 01/The.Lantern.Keepers.S01E02.mkv")
    services.match_file(file)
    review = MatchReview.objects.get(media_file=file, status=ReviewStatus.OPEN)
    assert review.reason == "no_candidates"
    assert review.candidates == []
    assert not Series.objects.exists()


def test_an_undecided_tvdb_answer_shares_the_review_with_its_provider(shows: Library) -> None:
    file = make_file(shows, "Lanterns/Season 01/Lanterns.S01E01.mkv")
    services.match_file(file)
    review = MatchReview.objects.get(media_file=file, status=ReviewStatus.OPEN)
    assert review.reason == "ambiguous"
    assert {item["provider"] for item in review.candidates} == {"tvdb"}
    assert {item["id"] for item in review.candidates} == {9100001, 9100002}
    assert all(item["poster_path"].startswith("https://artworks") for item in review.candidates)
    assert not Series.objects.exists()


def test_a_tvdb_outage_never_stops_matching(
    shows: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: Any, **kwargs: Any) -> Any:
        raise TVDBServerError("TheTVDB answered 503 on search", path="search", status_code=503)

    client = tvdb_series.tvdb()
    assert client is not None
    monkeypatch.setattr(client, "search_series", broken)
    file = make_file(shows, "The Lantern Keepers/Season 01/The.Lantern.Keepers.S01E02.mkv")
    services.match_file(file)
    review = MatchReview.objects.get(media_file=file, status=ReviewStatus.OPEN)
    assert review.reason == "no_candidates"


def test_an_episode_tvdb_does_not_have_opens_a_review(shows: Library) -> None:
    file = make_file(shows, "The Lantern Keepers/Season 01/The.Lantern.Keepers.S01E09.mkv")
    services.match_file(file)
    review = MatchReview.objects.get(media_file=file, status=ReviewStatus.OPEN)
    assert review.reason == "episode_not_found"
    assert review.candidates[0]["provider"] == "tvdb"


def test_a_tvdb_id_tmdb_does_not_know_is_matched_on_tvdb(shows: Library) -> None:
    file = make_file(shows, "Lantern Show [tvdbid-9100001]/Season 01/Lantern.Show.S01E03.mkv")
    assert ParseResult.from_json(file.parse_result).provider_ids.tvdb == 9100001
    services.match_file(file)
    file.refresh_from_db()
    assert file.state == FileState.MATCHED
    assert file.match_confidence == 1.0
    assert file.episodes.get().title == "Ramadan Nights"


def test_the_admin_resolves_a_review_with_a_tvdb_series(
    shows: Library, owner: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tvdb_series, "tvdb", lambda: None)
    file = make_file(shows, "Lanterns/Season 01/Lanterns.S01E01.mkv")
    services.match_file(file)
    review = MatchReview.objects.get(media_file=file, status=ReviewStatus.OPEN)
    offline = TVDBClient.offline()
    monkeypatch.setattr(tvdb_series, "tvdb", lambda: offline)
    services.resolve_review(review, 9100001, provider="tvdb", actor=owner, ip="203.0.113.5")
    review.refresh_from_db()
    assert review.status == ReviewStatus.RESOLVED
    assert (review.chosen_provider, review.chosen_provider_id) == ("tvdb", 9100001)
    assert file.episodes.get().title == "The Shop"
    entry = AuditLog.objects.get(action="review.resolve")
    assert (entry.after or {})["tvdb_id"] == 9100001


def test_tvdb_resolves_series_only_and_needs_a_key(
    shows: Library, owner: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    file = make_file(shows, "Lanterns/Season 01/Lanterns.S01E01.mkv")
    services.match_file(file)
    review = MatchReview.objects.get(media_file=file, status=ReviewStatus.OPEN)
    with pytest.raises(ProblemError) as raised:
        services.resolve_review(
            review, 9100001, provider="tvdb", kind=ReviewKind.MOVIE, actor=owner, ip=None
        )
    assert raised.value.field_error_codes == {"kind": ["tvdb_series_only"]}
    monkeypatch.setattr(tvdb_series, "tvdb", lambda: None)
    with pytest.raises(ProblemError) as raised:
        services.resolve_review(review, 9100001, provider="tvdb", actor=owner, ip=None)
    assert raised.value.problem_code == ErrorCode.CONFLICT
    with pytest.raises(ProblemError) as raised:
        services.search("tv", "lanterns", provider="tvdb")
    assert raised.value.problem_code == ErrorCode.CONFLICT


def test_manual_search_on_tvdb(shows: Library) -> None:
    found = services.search("tv", "The Lantern Keepers", provider="tvdb")
    assert [(item["provider"], item["id"]) for item in found] == [
        ("tvdb", 9100001),
        ("tvdb", 9100002),
    ]
    assert services.search("movie", "The Lantern Keepers", provider="tvdb") == []
    url = found[0]["poster_path"]
    assert services.poster_preview_url(url) == url


@pytest.mark.usefixtures("data_root")
def test_tvdb_artwork_is_stored_with_its_source(shows: Library) -> None:
    file = make_file(shows, "The Lantern Keepers/Season 01/The.Lantern.Keepers.S01E01.mkv")
    services.match_file(file)
    series = Series.objects.get(tvdb_id=9100001)
    added = services.fetch_series_images(series)
    rows = MediaImage.objects.filter(series=series)
    kinds = sorted((row.kind, row.language) for row in rows)
    assert kinds == [
        (ImageKind.BACKDROP, ""),
        (ImageKind.LOGO, ""),
        (ImageKind.POSTER, ""),
        (ImageKind.POSTER, "ar"),
    ]
    assert all(row.source == ImageSource.TVDB for row in rows)
    assert rows.get(kind=ImageKind.POSTER, language="").is_primary
    assert MediaImage.objects.filter(season__series=series, kind=ImageKind.POSTER).count() == 1
    assert MediaImage.objects.filter(episode__season__series=series).count() == 1
    assert added == 6
    # Stored once: a second run adds nothing.
    assert services.fetch_series_images(series) == 0


def test_refresh_keeps_locked_fields_and_dvd_order_renumbers(shows: Library) -> None:
    file = make_file(shows, "The Lantern Keepers/Season 01/The.Lantern.Keepers.S01E01.mkv")
    services.match_file(file)
    series = Series.objects.get(tvdb_id=9100001)
    series.title = "Lantern Keepers (edited)"
    series.metadata_locked_fields = ["title"]
    series.episode_ordering = EpisodeOrdering.TVDB_DVD
    series.save()
    services.refresh_series(series)
    series.refresh_from_db()
    assert series.title == "Lantern Keepers (edited)"
    numbers = dict(Episode.objects.filter(season__series=series).values_list("tvdb_id", "number"))
    assert numbers == {9500102: 1, 9500101: 2, 9500103: 3}


def test_a_series_without_tvdb_access_is_left_alone(
    shows: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    file = make_file(shows, "The Lantern Keepers/Season 01/The.Lantern.Keepers.S01E01.mkv")
    services.match_file(file)
    series = Series.objects.get(tvdb_id=9100001)
    monkeypatch.setattr(tvdb_series, "tvdb", lambda: None)
    assert services.fetch_series_images(series) == 0
    with pytest.raises(ProblemError) as raised:
        services.refresh_series(series)
    assert raised.value.problem_code == ErrorCode.CONFLICT


def test_tvdb_candidates_from_search_results() -> None:
    candidate = Candidate.from_tvdb(
        {
            "tvdb_id": "9100001",
            "name": "حراس الفانوس",
            "year": "2021",
            "aliases": ["Lantern Keepers"],
            "translations": {"eng": "The Lantern Keepers", "ara": "حراس الفانوس"},
            "overviews": {"eng": "Siblings."},
            "image_url": "https://artworks.thetvdb.com/banners/x.jpg",
        }
    )
    assert candidate.provider == "tvdb"
    assert candidate.provider_id == 9100001
    assert candidate.title == "The Lantern Keepers"
    assert candidate.original_title == "حراس الفانوس"
    assert candidate.alternative_titles == ("Lantern Keepers",)
    assert candidate.year == 2021
    assert candidate.overview == "Siblings."
    assert Candidate.from_tvdb({"id": "series-81189", "name": "X"}).provider_id == 81189
    assert Candidate.from_tvdb({"name": "X"}).provider_id == 0


def test_the_client_follows_the_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.core.services import set_setting  # noqa: PLC0415

    monkeypatch.undo()  # the real accessor, not the fixture's client
    tvdb_series.reset_caches()
    assert tvdb_series.tvdb() is None
    set_setting("metadata.tvdb_api_key", "abcd" + "1234" * 5, actor=None)
    client = tvdb_series.tvdb()
    assert isinstance(client, TVDBClient)
    assert tvdb_series.tvdb() is client
    set_setting("metadata.tvdb_rate_limit_per_s", 5, actor=None)
    assert tvdb_series.tvdb() is not client
    tvdb_series.reset_caches()


def test_the_review_api_takes_a_tvdb_id(
    shows: Library, owner_client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    file = make_file(shows, "Lanterns/Season 01/Lanterns.S01E01.mkv")
    services.match_file(file)
    review = MatchReview.objects.get(media_file=file, status=ReviewStatus.OPEN)
    client = owner_client
    url = f"/api/v1/admin/review-queue/{review.pk}/resolve"
    both = client.post(url, {"tmdb_id": 1, "tvdb_id": 2}, format="json", headers=ADMIN)
    assert both.status_code == 400
    assert both.json()["field_error_codes"] == {"tmdb_id": ["one_provider"]}
    response = client.post(url, {"tvdb_id": 9100001}, format="json", headers=ADMIN)
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["chosen_provider"] == "tvdb"
    assert body["chosen_provider_id"] == 9100001
    found = client.get(
        "/api/v1/admin/metadata/search",
        {"kind": "tv", "query": "The Lantern Keepers", "provider": "tvdb"},
        headers=ADMIN,
    )
    assert found.status_code == 200
    assert found.json()[0]["provider"] == "tvdb"
    assert found.json()[0]["poster_url"].startswith("https://artworks.thetvdb.com/")


def test_the_rematch_api_takes_a_tvdb_id(shows: Library, owner_client: Any) -> None:
    file = make_file(shows, "La Quimica/Season 01/La.Quimica.S01E01.mkv")
    services.match_file(file)
    series = file.episodes.get().season.series
    client = owner_client
    url = f"/api/v1/admin/titles/{series.pk}/rematch"
    refused = client.post(url, {"tvdb_id": 9100001, "kind": "movie"}, format="json", headers=ADMIN)
    assert refused.status_code == 400
    assert refused.json()["field_error_codes"] == {"kind": ["tvdb_series_only"]}
    response = client.post(url, {"tvdb_id": 9100001}, format="json", headers=ADMIN)
    body = response.json()
    assert response.status_code == 200, body
    assert body["automatic"] is False
    assert Series.objects.get(pk=body["title_ids"][0]).tvdb_id == 9100001


def test_artwork_from_another_host_is_refused() -> None:
    with (
        TVDBClient.offline() as client,
        client.open_artwork_client() as http,
        pytest.raises(httpx.RequestError),
    ):
        http.get("https://image.tmdb.org/t/p/w185/x.jpg")


def test_the_episode_order_is_editable_for_tvdb_series_only(
    shows: Library,
    owner_client: Any,
    monkeypatch: pytest.MonkeyPatch,
    django_capture_on_commit_callbacks: Callable[..., Any],
) -> None:
    from apps.metadata import tasks  # noqa: PLC0415

    refreshed: list[tuple[str, str]] = []
    monkeypatch.setattr(tasks.refresh_title, "delay", lambda *a: refreshed.append(a))
    lantern = make_file(shows, "The Lantern Keepers/Season 01/The.Lantern.Keepers.S01E01.mkv")
    quimica = make_file(shows, "La Quimica/Season 01/La.Quimica.S01E01.mkv")
    services.match_file(lantern)
    services.match_file(quimica)
    tvdb_built = Series.objects.get(tvdb_id=9100001)
    tmdb_built = Series.objects.get(tmdb_id=1396)
    refused = owner_client.patch(
        f"/api/v1/admin/series/{tmdb_built.pk}",
        {"episode_ordering": "tvdb_dvd"},
        format="json",
        headers=ADMIN,
    )
    assert refused.status_code == 400
    assert refused.json()["field_error_codes"] == {"episode_ordering": ["ordering_source"]}
    with django_capture_on_commit_callbacks(execute=True):
        changed = owner_client.patch(
            f"/api/v1/admin/series/{tvdb_built.pk}",
            {"episode_ordering": "tvdb_dvd"},
            format="json",
            headers=ADMIN,
        )
    assert changed.status_code == 200, changed.content
    assert changed.json()["episode_ordering"] == "tvdb_dvd"
    assert refreshed == [("series", str(tvdb_built.pk))]
