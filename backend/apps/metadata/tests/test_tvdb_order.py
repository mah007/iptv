"""TheTVDB's DVD or absolute order for series matched on TMDB (ADR-0024), over the offline
TMDB fixtures and the synthetic TheTVDB ones (Breaking Bad: TMDB 1396, TheTVDB 81189,
with an invented DVD order whose season 1 ends with what aired as S02E01)."""

from collections.abc import Callable
from typing import Any

import pytest
from django.conf import settings

from apps.catalog.models import (
    EpisodeOrdering,
    FileState,
    MatchReview,
    MediaFile,
    ReviewStatus,
    Series,
)
from apps.library.models import Library
from apps.library.parsing import ParseResult, parse_path
from apps.metadata import services, tvdb_order, tvdb_series
from apps.metadata.services import PlacementError

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("offline_tvdb")]
ADMIN = {"host": settings.ADMIN_HOST}
BREAKING_BAD = 1396


def make_file(library: Library, key: str) -> MediaFile:
    parsed = parse_path(key, library_kind=library.kind)  # type: ignore[arg-type]
    return MediaFile.objects.create(
        library=library,
        storage_key=key,
        parse_result=parsed.to_json(),
        duration_ms=47 * 60_000,
        state=FileState.MATCHING,
        match_confidence=0.97,
    )


@pytest.fixture
def shows(make_library: Callable[..., Library]) -> Library:
    return make_library("Series", "series", "series")


@pytest.fixture(autouse=True)
def _no_image_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.metadata import tasks  # noqa: PLC0415

    monkeypatch.setattr(tasks.fetch_title_images, "delay", lambda *a: None)


def place(file: MediaFile) -> list[tuple[int, int]]:
    parsed = ParseResult.from_json(file.parse_result)
    services.accept_episode(file, BREAKING_BAD, parsed, 0.97)
    return sorted(file.episodes.values_list("season__number", "number"))


@pytest.fixture
def series(shows: Library) -> Series:
    """Breaking Bad from TMDB, with the TheTVDB id TMDB names (81189)."""
    assert place(make_file(shows, "Breaking Bad/Season 01/Breaking.Bad.S01E01.mkv")) == [(1, 1)]
    found = Series.objects.get(tmdb_id=BREAKING_BAD)
    assert found.tvdb_id == 81189
    return found


def use(series: Series, ordering: str) -> None:
    series.episode_ordering = ordering
    series.save(update_fields=["episode_ordering"])


def test_tmdb_order_takes_the_numbers_as_they_are(shows: Library, series: Series) -> None:
    assert place(make_file(shows, "Breaking Bad/Season 01/Breaking.Bad.S01E07.mkv")) == [(1, 7)]
    absolute = make_file(shows, "Breaking Bad/Breaking Bad - 008.mkv")
    with pytest.raises(PlacementError) as refused:
        place(absolute)
    assert refused.value.reason == "numbering"


def test_dvd_numbers_map_to_tmdb_through_tvdb(shows: Library, series: Series) -> None:
    use(series, EpisodeOrdering.TVDB_DVD)
    # DVD S01E08 aired as S02E01; DVD S02E01 as S02E02.
    assert place(make_file(shows, "Breaking Bad/Season 01/Breaking.Bad.S01E08.mkv")) == [(2, 1)]
    assert place(make_file(shows, "Breaking Bad/Season 02/Breaking.Bad.S02E01.mkv")) == [(2, 2)]
    # A two-episode file across the DVD set's boundary lands in both aired seasons.
    double = make_file(shows, "Breaking Bad/Season 01/Breaking.Bad.S01E07E08.mkv")
    assert place(double) == [(1, 7), (2, 1)]
    missing = make_file(shows, "Breaking Bad/Season 01/Breaking.Bad.S01E09.mkv")
    with pytest.raises(PlacementError) as refused:
        place(missing)
    assert refused.value.reason == "episode_not_found"


def test_absolute_numbers_map_to_tmdb_through_tvdb(shows: Library, series: Series) -> None:
    use(series, EpisodeOrdering.TVDB_ABSOLUTE)
    assert place(make_file(shows, "Breaking Bad/Breaking Bad - 008.mkv")) == [(2, 1)]
    assert place(make_file(shows, "Breaking Bad/Breaking Bad - 020.mkv")) == [(2, 13)]
    # TheTVDB's absolute order is one season: S01E09 means absolute 9.
    assert place(make_file(shows, "Breaking Bad/Season 01/Breaking.Bad.S01E09.mkv")) == [(2, 2)]
    # Other seasons keep their numbers.
    assert place(make_file(shows, "Breaking Bad/Season 02/Breaking.Bad.S02E05.mkv")) == [(2, 5)]


def test_without_a_tvdb_key_the_file_waits(
    shows: Library, series: Series, monkeypatch: pytest.MonkeyPatch
) -> None:
    use(series, EpisodeOrdering.TVDB_DVD)
    monkeypatch.setattr(tvdb_series, "tvdb", lambda: None)
    with pytest.raises(PlacementError) as refused:
        place(make_file(shows, "Breaking Bad/Season 01/Breaking.Bad.S01E08.mkv"))
    assert refused.value.reason == "tvdb_unavailable"


def test_matching_places_through_the_order_or_opens_a_review(
    shows: Library, series: Series
) -> None:
    use(series, EpisodeOrdering.TVDB_DVD)
    # "La Quimica" is only on TheTVDB, which hands it back to TMDB's Breaking Bad.
    placed = make_file(shows, "La Quimica/Season 01/La.Quimica.S01E08.mkv")
    services.match_file(placed)
    placed.refresh_from_db()
    assert placed.state == FileState.MATCHED
    assert list(placed.episodes.values_list("season__number", "number")) == [(2, 1)]
    unknown = make_file(shows, "La Quimica/Season 01/La.Quimica.S01E09.mkv")
    services.match_file(unknown)
    review = MatchReview.objects.get(media_file=unknown, status=ReviewStatus.OPEN)
    assert review.reason == "episode_not_found"


def test_changing_the_order_places_the_files_again(
    shows: Library,
    series: Series,
    owner_client: Any,
    monkeypatch: pytest.MonkeyPatch,
    django_capture_on_commit_callbacks: Callable[..., Any],
) -> None:
    from apps.metadata import tasks  # noqa: PLC0415

    # Named after the DVDs, matched while the series used TMDB's order: S01E08 sat on
    # TMDB's S01E08, which doesn't exist... so it was a review; S02E01 sat on S02E01.
    dvd_named = make_file(shows, "Breaking Bad/Season 02/Breaking.Bad.S02E01.mkv")
    assert place(dvd_named) == [(2, 1)]
    queued: list[str] = []
    monkeypatch.setattr(tasks.replace_series_files, "delay", queued.append)
    with django_capture_on_commit_callbacks(execute=True):
        changed = owner_client.patch(
            f"/api/v1/admin/series/{series.pk}",
            {"episode_ordering": "tvdb_dvd"},
            format="json",
            headers=ADMIN,
        )
    assert changed.status_code == 200, changed.content
    assert changed.json()["episode_ordering"] == "tvdb_dvd"
    assert queued == [str(series.pk)]
    assert tasks.replace_series_files(str(series.pk)) == {"placed": 2, "kept": 0}
    dvd_named.refresh_from_db()
    assert list(dvd_named.episodes.values_list("season__number", "number")) == [(2, 2)]


def test_a_tmdb_series_without_a_tvdb_id_keeps_tmdbs_order(
    series: Series, owner_client: Any
) -> None:
    series.tvdb_id = None
    series.save(update_fields=["tvdb_id"])
    refused = owner_client.patch(
        f"/api/v1/admin/series/{series.pk}",
        {"episode_ordering": "tvdb_absolute"},
        format="json",
        headers=ADMIN,
    )
    assert refused.status_code == 400
    assert refused.json()["field_error_codes"] == {"episode_ordering": ["ordering_needs_tvdb_id"]}
    assert tvdb_order.replace_series_files(str(series.pk)) == {"placed": 1, "kept": 0}
