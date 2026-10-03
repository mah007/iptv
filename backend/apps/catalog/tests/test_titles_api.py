"""Admin API of movies, series, the review queue and the TMDB search (SPEC §8.3):
RBAC, audit, filters, query counts and the shapes the admin pages use."""

from collections.abc import Callable
from typing import Any

import pytest
from django.conf import settings
from rest_framework.test import APIClient

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
    Movie,
    Person,
    Season,
    Series,
    TitleStatus,
)
from apps.conftest import AdminFactory
from apps.library.models import Library
from apps.library.parsing import parse_path

pytestmark = pytest.mark.django_db

ADMIN = {"host": settings.ADMIN_HOST}
type Capture = Callable[..., Any]


def client_for(user: Any) -> APIClient:
    client = APIClient()
    client.force_authenticate(user)
    return client


def make_movie(title: str, library: Library, **fields: Any) -> Movie:
    movie = Movie.objects.create(title=title, **fields)
    MediaFile.objects.create(
        library=library,
        storage_key=f"{title}.mkv",
        movie=movie,
        state=FileState.MATCHED,
        container="mkv",
        video_codec="hevc",
        probe_summary={
            "audio": [
                {
                    "stream_index": 1,
                    "codec": "ac3",
                    "channels": 6,
                    "language": "eng",
                    "title": None,
                    "default": True,
                    "forced": False,
                }
            ],
            "subtitles": [],
        },
    )
    MediaImage.objects.create(
        movie=movie,
        kind="poster",
        is_primary=True,
        sizes={"w500": {"webp": f"images/movie/{movie.pk}/poster/w500.abc.webp"}},
        width=600,
        height=900,
        blurhash="LEHV6nWB2yk8",
    )
    return movie


@pytest.fixture
def library(make_library: Callable[..., Library]) -> Library:
    return make_library()


@pytest.fixture
def series(library: Library) -> Series:
    show = Series.objects.create(title="Breaking Bad", title_ar="بريكنج باد", tmdb_id=1396)
    season = Season.objects.create(series=show, number=1)
    for number in (1, 2):
        episode = Episode.objects.create(season=season, number=number, title=f"E{number}")
        file = MediaFile.objects.create(
            library=library, storage_key=f"BB/S01E0{number}.mkv", state=FileState.MATCHED
        )
        file.episodes.add(episode)
        MediaImage.objects.create(episode=episode, kind="still", is_primary=True, sizes={})
    MediaImage.objects.create(season=season, kind="poster", is_primary=True, sizes={})
    return show


def test_movie_list_shape_filters_and_query_count(
    owner_client: APIClient, library: Library, django_assert_num_queries: Capture
) -> None:
    action = Category.objects.create(kind=CategoryKind.VOD, slug="action", name_en="A", name_ar="أ")
    matrix = make_movie("The Matrix", library, year=1999, overview_ar="ملخص", tmdb_id=603)
    matrix.categories.add(action)
    make_movie("Inception", library, year=2010, status=TitleStatus.READY)
    make_movie("Wadjda", library, year=2012, metadata_source="synthetic")
    with django_assert_num_queries(5):
        response = owner_client.get("/api/v1/admin/movies", headers=ADMIN)
    assert response.status_code == 200
    rows = response.json()["results"]
    assert [row["title"] for row in rows] == ["Inception", "The Matrix", "Wadjda"]
    row = rows[1]
    assert row["poster"]["url"] == (
        f"{settings.MEDIA_BASE_URL}/images/movie/{matrix.pk}/poster/w500.abc.webp"
    )
    assert row["poster"]["blurhash"] == "LEHV6nWB2yk8"
    assert (row["file_count"], row["has_arabic_overview"], row["xc_id"]) == (1, True, matrix.xc_id)
    assert row["categories"] == [
        {"id": str(action.pk), "kind": "vod", "name_en": "A", "name_ar": "أ"}
    ]
    assert rows[2]["synthetic"] is True

    def titles(query: str) -> list[str]:
        response = owner_client.get(f"/api/v1/admin/movies?{query}", headers=ADMIN)
        return [row["title"] for row in response.json()["results"]]

    assert titles("status=ready") == ["Inception"]
    assert titles(f"category={action.pk}") == ["The Matrix"]
    assert titles("missing_arabic=true") == ["Inception", "Wadjda"]
    assert titles("missing_arabic=false") == ["The Matrix"]
    assert titles("search=matr") == ["The Matrix"]
    assert titles("ordering=-year") == ["Wadjda", "Inception", "The Matrix"]
    assert titles("year_min=2000&year_max=2011") == ["Inception"]
    assert titles(f"library={library.pk}") == ["Inception", "The Matrix", "Wadjda"]


def test_movie_detail_has_metadata_art_credits_and_files(
    owner_client: APIClient, library: Library, django_assert_num_queries: Capture
) -> None:
    movie = make_movie("The Matrix", library, year=1999, tmdb_id=603)
    person = Person.objects.create(name="Keanu Reeves", tmdb_id=6384)
    Credit.objects.create(movie=movie, person=person, role="cast", character="Neo", order=0)
    with django_assert_num_queries(7):
        response = owner_client.get(f"/api/v1/admin/movies/{movie.pk}", headers=ADMIN)
    body = response.json()
    assert body["credits"][0]["person"]["name"] == "Keanu Reeves"
    file = body["files"][0]
    assert file["relative_path"] == "The Matrix.mkv"
    assert "storage_key" not in file
    assert library.path not in str(body)
    assert file["library"] == {"id": str(library.pk), "name": library.name}
    assert file["audio"][0]["codec"] == "ac3"
    assert body["images"][0]["sizes"]["w500"]["webp"].endswith("w500.abc.webp")
    missing = owner_client.get(f"/api/v1/admin/movies/{library.pk}", headers=ADMIN)
    assert missing.status_code == 404


def test_series_list_and_detail(
    owner_client: APIClient, series: Series, django_assert_num_queries: Capture
) -> None:
    with django_assert_num_queries(5):
        response = owner_client.get("/api/v1/admin/series", headers=ADMIN)
    row = response.json()["results"][0]
    assert (row["title"], row["file_count"], row["episode_count"]) == ("Breaking Bad", 2, 2)
    with django_assert_num_queries(11):
        response = owner_client.get(f"/api/v1/admin/series/{series.pk}", headers=ADMIN)
    body = response.json()
    season = body["seasons"][0]
    assert season["number"] == 1
    assert season["poster"] is not None
    assert [e["number"] for e in season["episodes"]] == [1, 2]
    assert season["episodes"][0]["files"][0]["relative_path"] == "BB/S01E01.mkv"
    assert season["episodes"][0]["still"]["kind"] == "still"


def test_editing_locks_fields_and_is_audited(
    owner_client: APIClient, library: Library, owner: Any
) -> None:
    movie = make_movie("The Matrix", library, tmdb_id=603)
    drama = Category.objects.create(kind=CategoryKind.VOD, slug="drama", name_en="D", name_ar="د")
    response = owner_client.patch(
        f"/api/v1/admin/movies/{movie.pk}",
        {"title_ar": "المصفوفة", "overview_ar": "نص", "categories": [str(drama.pk)]},
        headers=ADMIN,
    )
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["title_ar"] == "المصفوفة"
    assert body["metadata_locked_fields"] == ["title_ar", "overview_ar", "categories"]
    assert [c["id"] for c in body["categories"]] == [str(drama.pk)]
    entry = AuditLog.objects.get(action="movie.update")
    assert entry.actor == owner
    assert entry.after is not None
    assert entry.after["title_ar"] == "المصفوفة"
    # Hide, then show again: the files decide the status once the lock is gone.
    response = owner_client.patch(
        f"/api/v1/admin/movies/{movie.pk}", {"status": "hidden"}, headers=ADMIN
    )
    assert response.json()["status"] == "hidden"
    assert "status" in response.json()["metadata_locked_fields"]
    response = owner_client.patch(
        f"/api/v1/admin/movies/{movie.pk}", {"status": "ready"}, headers=ADMIN
    )
    assert response.json()["status"] == "processing"
    # Locks can be set explicitly; unknown names are dropped.
    response = owner_client.patch(
        f"/api/v1/admin/movies/{movie.pk}",
        {"metadata_locked_fields": ["overview", "nonsense"]},
        headers=ADMIN,
    )
    assert response.json()["metadata_locked_fields"] == ["overview"]
    series_category = Category.objects.create(
        kind=CategoryKind.SERIES, slug="drama", name_en="D", name_ar="د"
    )
    response = owner_client.patch(
        f"/api/v1/admin/movies/{movie.pk}", {"categories": [str(series_category.pk)]}, headers=ADMIN
    )
    assert response.status_code == 400
    response = owner_client.patch(f"/api/v1/admin/movies/{movie.pk}", {"rating": 11}, headers=ADMIN)
    assert response.status_code == 400


def test_editing_a_series(owner_client: APIClient, series: Series) -> None:
    response = owner_client.patch(
        f"/api/v1/admin/series/{series.pk}", {"episode_run_time": 47}, headers=ADMIN
    )
    assert response.status_code == 200
    assert response.json()["episode_run_time"] == 47
    assert AuditLog.objects.filter(action="series.update").exists()


def test_refresh_is_queued_and_audited(
    owner_client: APIClient,
    library: Library,
    series: Series,
    django_capture_on_commit_callbacks: Capture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.metadata import tasks  # noqa: PLC0415

    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(tasks.refresh_title, "delay", lambda *args: sent.append(args))
    movie = make_movie("The Matrix", library, tmdb_id=603)
    with django_capture_on_commit_callbacks(execute=True):
        response = owner_client.post(
            f"/api/v1/admin/movies/{movie.pk}/refresh-metadata", headers=ADMIN
        )
        owner_client.post(f"/api/v1/admin/series/{series.pk}/refresh-metadata", headers=ADMIN)
    assert (response.status_code, response.json()) == (202, {"queued": True})
    assert sent == [("movie", str(movie.pk)), ("series", str(series.pk))]
    assert AuditLog.objects.filter(action__in=["movie.refresh", "series.refresh"]).count() == 2


def test_rbac(make_admin: AdminFactory, library: Library, series: Series) -> None:
    movie = make_movie("The Matrix", library)
    viewer = client_for(make_admin("viewer"))
    assert viewer.get("/api/v1/admin/movies", headers=ADMIN).status_code == 200
    assert viewer.get(f"/api/v1/admin/series/{series.pk}", headers=ADMIN).status_code == 200
    assert viewer.get("/api/v1/admin/review-queue", headers=ADMIN).status_code == 200
    patch = viewer.patch(f"/api/v1/admin/movies/{movie.pk}", {"title": "X"}, headers=ADMIN)
    assert patch.status_code == 403
    support = client_for(make_admin("support"))
    assert support.get("/api/v1/admin/movies", headers=ADMIN).status_code == 403
    manager = client_for(make_admin("content_manager"))
    patch = manager.patch(f"/api/v1/admin/movies/{movie.pk}", {"title": "X"}, headers=ADMIN)
    assert patch.status_code == 200
    assert APIClient().get("/api/v1/admin/movies", headers=ADMIN).status_code == 401


# --- Review queue ---------------------------------------------------------------------------------


@pytest.fixture
def review(library: Library) -> MatchReview:
    file = MediaFile.objects.create(
        library=library,
        storage_key="The Matrix.mp4",
        parse_result=parse_path("The Matrix.mp4", library_kind="movies").to_json(),
        state=FileState.REVIEW,
        direct_play=True,
    )
    candidate = {
        "provider": "tmdb",
        "kind": "movie",
        "id": 603,
        "title": "The Matrix",
        "original_title": "The Matrix",
        "year": 1999,
        "runtime_min": None,
        "popularity": 80.5,
        "poster_path": "/synthetic-movie-603-poster-en-600x900.jpg",
        "overview": "",
        "score": 1.0,
        "breakdown": {
            "title": 1.0,
            "year": None,
            "runtime": None,
            "popularity": 1.0,
            "matched_title": "The Matrix",
        },
    }
    return MatchReview.objects.create(
        media_file=file,
        kind="movie",
        reason="ambiguous",
        parse_result=file.parse_result,
        candidates=[candidate],
    )


@pytest.mark.usefixtures("offline_tmdb")
def test_review_queue_list_and_detail(
    owner_client: APIClient, review: MatchReview, django_assert_num_queries: Capture
) -> None:
    with django_assert_num_queries(3):
        response = owner_client.get("/api/v1/admin/review-queue", headers=ADMIN)
    rows = response.json()["results"]
    assert [row["id"] for row in rows] == [str(review.pk)]
    row = rows[0]
    assert row["media_file"]["relative_path"] == "The Matrix.mp4"
    assert row["parse_result"]["title"] == "The Matrix"
    assert row["candidates"][0]["breakdown"]["title"] == 1.0
    assert row["candidates"][0]["poster_url"] is None  # fixture mode
    resolved = owner_client.get("/api/v1/admin/review-queue?status=resolved", headers=ADMIN)
    assert resolved.json()["results"] == []
    detail = owner_client.get(f"/api/v1/admin/review-queue/{review.pk}", headers=ADMIN)
    assert detail.json()["reason"] == "ambiguous"


@pytest.mark.usefixtures("offline_tmdb")
def test_resolve_and_skip_through_the_api(
    make_admin: AdminFactory, review: MatchReview, library: Library
) -> None:
    viewer = client_for(make_admin("viewer"))
    url = f"/api/v1/admin/review-queue/{review.pk}/resolve"
    assert viewer.post(url, {"tmdb_id": 603}, headers=ADMIN).status_code == 403
    reviewer = client_for(make_admin("content_manager"))
    assert reviewer.post(url, {"tmdb_id": 0}, headers=ADMIN).status_code == 400
    response = reviewer.post(url, {"tmdb_id": 603}, headers=ADMIN)
    assert response.status_code == 200, response.json()
    assert response.json()["status"] == "resolved"
    movie = Movie.objects.get(tmdb_id=603)
    assert movie.status == TitleStatus.READY
    assert reviewer.post(url, {"tmdb_id": 603}, headers=ADMIN).status_code == 409
    other = MediaFile.objects.create(library=library, storage_key="x.mkv", state=FileState.REVIEW)
    pending = MatchReview.objects.create(media_file=other, kind="movie", reason="no_candidates")
    response = reviewer.post(f"/api/v1/admin/review-queue/{pending.pk}/skip", headers=ADMIN)
    assert response.json()["status"] == "skipped"


@pytest.mark.usefixtures("offline_tmdb")
def test_tmdb_search(owner_client: APIClient, make_admin: AdminFactory) -> None:
    response = owner_client.get(
        "/api/v1/admin/metadata/search?kind=movie&query=matrix&year=1999", headers=ADMIN
    )
    assert [row["id"] for row in response.json()] == [603]
    response = owner_client.get("/api/v1/admin/metadata/search?kind=tv&query=show", headers=ADMIN)
    assert response.json()[0]["title"] == "Show"
    assert owner_client.get("/api/v1/admin/metadata/search", headers=ADMIN).status_code == 400
    viewer = client_for(make_admin("viewer"))
    search = viewer.get("/api/v1/admin/metadata/search?kind=tv&query=x", headers=ADMIN)
    assert search.status_code == 403
