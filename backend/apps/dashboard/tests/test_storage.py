"""GET /api/v1/admin/storage: storage usage by library, over 90 days and per title."""

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from django.conf import settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.catalog.models import Episode, FileState, MediaFile, Movie
from apps.conftest import AdminFactory
from apps.dashboard import storage
from apps.dashboard.tests.conftest import make_episode, make_movie
from apps.library.models import Library
from apps.media.models import Rendition, RenditionKind, RenditionStatus

pytestmark = pytest.mark.django_db

ADMIN = {"host": settings.ADMIN_HOST}
URL = "/api/v1/admin/storage"
RIYADH = ZoneInfo("Asia/Riyadh")
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
MB = 1_000_000


def source(
    library: Library,
    size: int,
    *,
    movie: Movie | None = None,
    episodes: tuple[Episode, ...] = (),
    created: datetime = NOW - timedelta(days=200),
    removed: datetime | None = None,
) -> MediaFile:
    file = MediaFile.objects.create(
        library=library,
        storage_key=f"{uuid4().hex}.mkv",
        size=size,
        movie=movie,
        state=FileState.MATCHED,
        removed_at=removed,
    )
    MediaFile.objects.filter(pk=file.pk).update(created_at=created)
    file.episodes.set(episodes)
    return file


def output(
    file: MediaFile,
    size: int,
    *,
    kind: str = RenditionKind.COMPAT_MP4,
    name: str = "",
    ready: datetime = NOW - timedelta(days=200),
    linked: bool = False,
) -> Rendition:
    return Rendition.objects.create(
        media_file=file,
        kind=kind,
        name=name,
        storage_key=uuid4().hex[:16],
        size=size,
        status=RenditionStatus.READY,
        ready_at=ready,
        details={"linked": True} if linked else {},
    )


def test_usage_by_library_counts_present_sources_and_rendition_bytes(
    make_library: Callable[..., Library],
) -> None:
    films = make_library("Films", "movies", "films")
    shows = make_library("Shows", "series", "shows")
    make_library("Empty", "movies", "empty")
    movie = make_movie("Big")
    big = source(films, 900 * MB, movie=movie)
    output(big, 300 * MB)
    output(big, 900 * MB, kind=RenditionKind.SOURCE, linked=True)  # a link: no space
    gone = source(films, 50 * MB, removed=NOW - timedelta(days=1))
    output(gone, 20 * MB)  # still on disk until the cleanup
    episode = make_episode("Show")
    output(source(shows, 100 * MB, episodes=(episode,)), 40 * MB)

    usage = storage.compute_storage(tz=RIYADH, now=NOW)

    by_name = {item.name: item for item in usage.libraries}
    assert [item.name for item in usage.libraries] == ["Films", "Shows", "Empty"]
    assert by_name["Films"].files == 1
    assert by_name["Films"].sources == 900 * MB
    assert by_name["Films"].renditions == 320 * MB
    assert by_name["Shows"].sources == 100 * MB
    assert by_name["Shows"].renditions == 40 * MB
    assert (by_name["Empty"].files, by_name["Empty"].sources) == (0, 0)
    assert (usage.files, usage.sources, usage.renditions) == (2, 1000 * MB, 360 * MB)


def test_growth_rebuilds_each_days_balance(make_library: Callable[..., Library]) -> None:
    films = make_library("Films")
    source(films, 10 * MB)  # before the window: the opening balance
    added = source(films, 5 * MB, created=NOW - timedelta(days=10))
    output(added, 2 * MB, ready=NOW - timedelta(days=9))
    source(films, 3 * MB, created=NOW - timedelta(days=20), removed=NOW - timedelta(days=5))

    growth = storage.compute_storage(tz=RIYADH, now=NOW).growth

    assert len(growth) == 90
    assert growth[-1].date == date(2026, 10, 4)
    by_day = {point.date: (point.sources, point.renditions) for point in growth}
    assert growth[0].sources == 10 * MB
    assert by_day[date(2026, 9, 14)] == (13 * MB, 0)
    assert by_day[date(2026, 9, 24)] == (18 * MB, 0)
    assert by_day[date(2026, 9, 25)] == (18 * MB, 2 * MB)
    assert by_day[date(2026, 9, 29)] == (15 * MB, 2 * MB)
    assert growth[-1].sources == 15 * MB


def test_largest_titles_count_a_series_and_its_episodes_once(
    make_library: Callable[..., Library],
) -> None:
    films = make_library("Films")
    small = source(films, 10 * MB, movie=make_movie("Small"))
    output(small, 5 * MB)
    first = make_episode("Saga")
    second = Episode.objects.create(season=first.season, number=2, title="Two")
    double = source(films, 200 * MB, episodes=(first, second))  # S01E01E02 in one file
    output(double, 50 * MB)
    output(double, 10 * MB, kind=RenditionKind.HLS_VARIANT, name="v0")
    source(films, 0, movie=make_movie("Gone"), removed=NOW - timedelta(days=1))

    largest = storage.compute_storage(tz=RIYADH, now=NOW).largest

    assert [(item.kind, item.title) for item in largest] == [
        ("series", "Saga"),
        ("movie", "Small"),
    ]
    assert (largest[0].files, largest[0].sources, largest[0].renditions) == (
        1,
        200 * MB,
        60 * MB,
    )
    assert largest[0].title_ar == "Saga (ar)"


def test_endpoint_is_cached_with_constant_queries(
    owner: User,
    owner_client: APIClient,
    make_library: Callable[..., Library],
    django_assert_num_queries: Any,
) -> None:
    films = make_library("Films")
    for index in range(5):
        output(source(films, MB, movie=make_movie(f"Film {index}")), MB)
    output(source(films, MB, episodes=(make_episode("Show"),)), MB)
    # Permissions; libraries; growth; largest titles with their movies and series.
    with django_assert_num_queries(6):
        body = owner_client.get(URL, headers=ADMIN).json()
    assert body["time_zone"] == "Asia/Riyadh"
    assert body["sources"] == 6 * MB
    assert len(body["growth"]) == 90
    assert len(body["largest"]) == 6
    assert body["libraries"][0]["name"] == "Films"
    assert "storage_key" not in str(body)
    # Cached for 30 s: only the permission lookup.
    with django_assert_num_queries(1):
        assert owner_client.get(URL, headers=ADMIN).json() == body


@pytest.mark.parametrize(
    ("permissions", "status"),
    [(["library.view"], 200), (["library.manage"], 200), (["customers.view"], 403)],
)
def test_storage_needs_a_library_permission(
    make_admin: AdminFactory, permissions: list[str], status: int
) -> None:
    client = APIClient()
    client.force_authenticate(make_admin(permissions=permissions))
    assert client.get(URL, headers=ADMIN).status_code == status
