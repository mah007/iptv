"""`apps.catalog.playable`: the catalogue lookup behind playback (ADR-0010)."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from apps.catalog.models import (
    Category,
    CategoryKind,
    Episode,
    FileState,
    MediaFile,
    Movie,
    Season,
    Series,
    TitleStatus,
)
from apps.catalog.playable import playable_title, playable_title_by_id
from apps.catalog.services import refresh_movie_status
from apps.library.models import Library
from apps.media.models import Rendition, RenditionKind, RenditionStatus
from apps.playback.models import TitleKind
from apps.playback.services import RenditionKind as Kind

pytestmark = pytest.mark.django_db


@pytest.fixture
def library(make_library: Callable[..., Library]) -> Library:
    return make_library()


def _file(library: Library, **fields: Any) -> MediaFile:
    return MediaFile.objects.create(
        library=library,
        storage_key=f"{uuid4().hex}.mkv",
        state=FileState.MATCHED,
        **fields,
    )


def _rendition(file: MediaFile, kind: str, **fields: Any) -> Rendition:
    values: dict[str, Any] = {
        "storage_key": file.pk.hex,
        "status": RenditionStatus.READY,
        "height": 1080,
        "duration_s": 5400.4,
    }
    values.update(fields)
    return Rendition.objects.create(media_file=file, kind=kind, **values)


def test_ready_movie_with_its_renditions_categories_and_runtime(
    library: Library, django_assert_max_num_queries: Any
) -> None:
    adult = Category.objects.create(
        kind=CategoryKind.VOD, slug="late", name_en="Late", name_ar="ليلي", is_adult=True
    )
    drama = Category.objects.create(
        kind=CategoryKind.VOD, slug="drama", name_en="Drama", name_ar="دراما"
    )
    expires = datetime(2030, 1, 1, tzinfo=UTC)
    movie = Movie.objects.create(
        title="Film", status=TitleStatus.READY, runtime_min=80, license_expires_at=expires
    )
    movie.categories.set([adult, drama])
    primary = _file(library, movie=movie)
    _rendition(primary, RenditionKind.COMPAT_MP4)
    _rendition(primary, RenditionKind.SOURCE, height=720, container="mp4")
    other = _file(library, movie=movie, is_primary=False)
    _rendition(other, RenditionKind.COMPAT_MP4, height=480)
    removed = _file(library, movie=movie, removed_at=expires)
    _rendition(removed, RenditionKind.COMPAT_MP4)
    pending = _file(library, movie=movie)
    _rendition(pending, RenditionKind.COMPAT_MP4, status=RenditionStatus.RUNNING)
    movie.refresh_from_db()

    with django_assert_max_num_queries(3):
        title = playable_title(TitleKind.MOVIE, movie.xc_id)

    assert title is not None
    assert title.kind == TitleKind.MOVIE
    assert title.id == movie.pk
    assert title.name == "Film"
    assert title.category_ids == frozenset({adult.pk, drama.pk})
    assert title.adult is True
    assert title.license_expires_at == expires
    assert title.runtime_s == 5400  # from the renditions, not the metadata's 80 min
    assert [(r.storage_key, r.kind, r.height) for r in title.renditions] == [
        (primary.pk.hex, Kind.COMPAT, 1080),
        (primary.pk.hex, Kind.SOURCE, 720),
        (other.pk.hex, Kind.COMPAT, 480),
    ]
    assert all("/" not in r.storage_key for r in title.renditions)
    assert playable_title_by_id(TitleKind.MOVIE, movie.pk) == title


def test_unknown_or_unready_titles_are_none(library: Library) -> None:
    movie = Movie.objects.create(title="Soon", status=TitleStatus.PROCESSING)
    _rendition(_file(library, movie=movie), RenditionKind.COMPAT_MP4)
    movie.refresh_from_db()

    assert playable_title(TitleKind.MOVIE, movie.xc_id) is None
    assert playable_title(TitleKind.MOVIE, 987_654_321) is None
    assert playable_title(TitleKind.EPISODE, movie.xc_id) is None
    assert playable_title_by_id(TitleKind.MOVIE, uuid4()) is None
    assert playable_title("live", movie.xc_id) is None  # type: ignore[arg-type]


def test_episode_of_a_ready_series(library: Library, django_assert_max_num_queries: Any) -> None:
    kids = Category.objects.create(
        kind=CategoryKind.SERIES, slug="kids", name_en="Kids", name_ar="أطفال"
    )
    series = Series.objects.create(title="Show", status=TitleStatus.READY, episode_run_time=42)
    series.categories.add(kids)
    season = Season.objects.create(series=series, number=1)
    first = Episode.objects.create(season=season, number=1, title="Pilot")
    second = Episode.objects.create(season=season, number=2)
    file = _file(library)
    file.episodes.add(first)
    _rendition(file, RenditionKind.COMPAT_MP4, height=720, duration_s=None)
    first.refresh_from_db()
    second.refresh_from_db()

    with django_assert_max_num_queries(3):
        title = playable_title(TitleKind.EPISODE, first.xc_id)

    assert title is not None
    assert title.name == "Show S01E01 Pilot"
    assert title.category_ids == frozenset({kids.pk})
    assert title.adult is False
    assert title.runtime_s == 42 * 60  # no rendition duration: the series' run time
    assert [(r.kind, r.height) for r in title.renditions] == [(Kind.COMPAT, 720)]

    # Listed with its ready series, but still being prepared: start_playback answers
    # TITLE_PREPARING for an empty rendition list.
    preparing = playable_title_by_id(TitleKind.EPISODE, second.pk)
    assert preparing is not None
    assert preparing.renditions == ()
    assert preparing.name == "Show S01E02"

    series.status = TitleStatus.HIDDEN
    series.save()
    assert playable_title(TitleKind.EPISODE, first.xc_id) is None


def test_a_ready_rendition_makes_the_title_ready(library: Library) -> None:
    movie = Movie.objects.create(title="Encoded")
    file = _file(library, movie=movie)
    refresh_movie_status(movie)
    assert movie.status == TitleStatus.PROCESSING

    rendition = _rendition(file, RenditionKind.COMPAT_MP4, status=RenditionStatus.RUNNING)
    refresh_movie_status(movie)
    assert movie.status == TitleStatus.PROCESSING

    rendition.status = RenditionStatus.READY
    rendition.save()
    refresh_movie_status(movie)
    assert movie.status == TitleStatus.READY
