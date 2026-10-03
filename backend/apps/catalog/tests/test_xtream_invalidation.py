"""Catalogue writers retire the Xtream catalogue cache (SPEC §7.5) once they commit."""

from collections.abc import Callable
from typing import Any, cast

import pytest
from django.db import transaction

from apps.catalog import services
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
from apps.core.stores import cache_redis
from apps.library.models import Library
from apps.library.services import delete_library
from apps.xtream_api import cache as xtream_cache

pytestmark = pytest.mark.django_db
type Capture = Callable[..., Any]


def version() -> bytes | None:
    return cast("bytes | None", cache_redis().get(xtream_cache.VERSION_KEY))


@pytest.fixture
def library(make_library: Callable[..., Library]) -> Library:
    return make_library()


def bumps(capture: Capture, action: Callable[[], object]) -> bool:
    xtream_cache.invalidate()
    before = version()
    with capture(execute=True):
        action()
    return version() != before


def test_category_writes_invalidate(django_capture_on_commit_callbacks: Capture) -> None:
    capture = django_capture_on_commit_callbacks
    holder: dict[str, Category] = {}

    def create() -> None:
        holder["c"] = services.create_category(
            {"kind": CategoryKind.VOD, "name_en": "Action", "name_ar": "أكشن"}, actor=None, ip=None
        )

    assert bumps(capture, create)
    category = holder["c"]
    assert bumps(
        capture,
        lambda: services.update_category(
            category, {"visible_in_xtream": False}, actor=None, ip=None
        ),
    )
    assert bumps(
        capture,
        lambda: services.reorder_categories(CategoryKind.VOD, [category.pk], actor=None, ip=None),
    )
    assert bumps(capture, lambda: services.delete_category(category, actor=None, ip=None))


def test_genre_categories_created_on_first_use_invalidate(
    django_capture_on_commit_callbacks: Capture,
) -> None:
    assert bumps(
        django_capture_on_commit_callbacks,
        lambda: services.categories_for_genres(CategoryKind.VOD, [(28, "Action", "أكشن")]),
    )


def test_title_edits_invalidate(
    library: Library, django_capture_on_commit_callbacks: Capture
) -> None:
    movie = Movie.objects.create(title="The Matrix", status=TitleStatus.READY)
    assert bumps(
        django_capture_on_commit_callbacks,
        lambda: services.update_title(movie, {"status": "hidden"}, actor=None, ip=None),
    )


def test_file_changes_invalidate_even_when_the_status_stays(
    library: Library, django_capture_on_commit_callbacks: Capture
) -> None:
    show = Series.objects.create(title="Breaking Bad", status=TitleStatus.READY)
    season = Season.objects.create(series=show, number=1)
    files = []
    for number in (1, 2):
        episode = Episode.objects.create(season=season, number=number)
        file = MediaFile.objects.create(
            library=library,
            storage_key=f"BB/S01E0{number}.mp4",
            state=FileState.MATCHED,
            direct_play=True,
        )
        file.episodes.add(episode)
        files.append(file)
    # One episode's file goes away: still ready, but get_series_info lists one episode less.
    MediaFile.objects.filter(pk=files[0].pk).update(removed_at=files[0].created_at)
    assert bumps(
        django_capture_on_commit_callbacks,
        lambda: services.refresh_status_of_files([files[0].pk]),
    )
    show.refresh_from_db()
    assert show.status == TitleStatus.READY
    assert bumps(
        django_capture_on_commit_callbacks,
        lambda: delete_library(library, actor=None, ip=None),
    )


def test_rolled_back_writes_do_not_invalidate(
    django_capture_on_commit_callbacks: Capture,
) -> None:
    xtream_cache.invalidate()
    before = version()

    def failing() -> None:
        with transaction.atomic():
            services.create_category(
                {"kind": CategoryKind.VOD, "name_en": "Drama", "name_ar": "دراما"},
                actor=None,
                ip=None,
            )
            raise RuntimeError

    with django_capture_on_commit_callbacks(execute=True), pytest.raises(RuntimeError):
        failing()
    assert version() == before
