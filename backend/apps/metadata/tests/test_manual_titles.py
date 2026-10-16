"""Manual titles (ADR-0026): a movie an admin describes by hand for a file no metadata
provider knows. Creation from open and skipped reviews and from a file, every refusal,
RBAC, audit with the rights confirmation, queued work, imported rights, the form's
defaults, reopening skipped reviews, refreshing refused, matching to TMDB later, the
editor's new fields, adult categories and the customer API's credit."""

import base64
import io
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.catalog.browse import CustomerScope, visible_movies
from apps.catalog.models import (
    Category,
    CategoryKind,
    FileState,
    Genre,
    MatchReview,
    MediaFile,
    MetadataSource,
    Movie,
    ReviewStatus,
    TitleStatus,
)
from apps.catalog.serializers_customer import MovieDetailSerializer
from apps.catalog.services import MANUAL_MOVIE_LOCKS, update_title
from apps.conftest import AdminFactory
from apps.core.errors import ErrorCode, ProblemError
from apps.imports.models import FileKind, ImportFile, ImportJob, JobOrigin
from apps.library.models import Library
from apps.library.parsing import parse_path
from apps.media import tasks as media_tasks
from apps.metadata import services
from apps.metadata import tasks as metadata_tasks
from apps.metadata.tmdb import TMDBClient, TMDBError
from apps.search import index

pytestmark = pytest.mark.django_db

ADMIN = {"host": settings.ADMIN_HOST}
MANUAL = "/api/v1/admin/movies/manual"
QUEUE = "/api/v1/admin/review-queue"
type Capture = Callable[..., Any]

RIGHTS = {
    "rights_holder": "Mahmoud Family Archive",
    "license_ref": "OWN-2026-001",
    "rights_confirmed": True,
}


def png(width: int = 300, height: int = 450) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (40, 90, 160)).save(buffer, format="PNG")
    return buffer.getvalue()


def client_for(user: Any) -> APIClient:
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.fixture
def library(make_library: Callable[..., Library]) -> Library:
    return make_library()


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, tuple[Any, ...]]]:
    """Tasks the manual flow queues, captured; on_commit callbacks run at once."""
    calls: list[tuple[str, tuple[Any, ...]]] = []
    for name in ("store_title_image", "frame_artwork", "prepare_media_file"):
        task = getattr(media_tasks, name)
        monkeypatch.setattr(task, "delay", lambda *a, _n=name, **_k: calls.append((_n, a)))
    monkeypatch.setattr("apps.metadata.services.transaction.on_commit", lambda fn, **_k: fn())
    return calls


def review_file(
    library: Library,
    key: str = "Our Trip to AlUla (2025).mp4",
    *,
    status: str = ReviewStatus.OPEN,
    state: str = FileState.REVIEW,
    duration_ms: int | None = 5_430_000,
) -> MatchReview:
    file = MediaFile.objects.create(
        library=library,
        storage_key=key,
        parse_result=parse_path(key, library_kind="movies").to_json(),
        state=state,
        duration_ms=duration_ms,
        direct_play=True,
    )
    return MatchReview.objects.create(
        media_file=file,
        kind="movie",
        reason="no_candidates",
        parse_result=file.parse_result,
        status=status,
    )


def body(review: MatchReview | None = None, **fields: Any) -> dict[str, Any]:
    data: dict[str, Any] = {"title": "Our Trip to AlUla", **RIGHTS, **fields}
    if review is not None:
        data["review"] = str(review.pk)
    return data


# --- Creating ---------------------------------------------------------------------------------


def test_create_from_an_open_review(
    owner_client: APIClient, owner: Any, library: Library, queued: list[Any]
) -> None:
    family = Category.objects.create(
        kind=CategoryKind.VOD, slug="family", name_en="Family", name_ar="عائلي"
    )
    library.default_categories.add(family)
    genre = Genre.objects.create(tmdb_id=99, name_en="Documentary", name_ar="وثائقي")
    review = review_file(library)
    upload = SimpleUploadedFile("poster.png", png(), content_type="image/png")

    response = owner_client.post(
        MANUAL,
        {
            **body(review),
            "title_ar": "رحلتنا إلى العلا",
            "overview": "A family holiday.",
            "year": "2025",
            "original_language": "AR",
            "countries": ["sa", "SA"],
            "genres": [str(genre.pk)],
            "rights_confirmed": "true",
            "poster": upload,
        },
        format="multipart",
        headers=ADMIN,
    )

    assert response.status_code == 201, response.json()
    data = response.json()
    assert (data["metadata_source"], data["tmdb_id"], data["synthetic"]) == ("manual", None, False)
    movie = Movie.objects.get(pk=data["id"])
    assert (movie.title, movie.title_ar, movie.year) == (
        "Our Trip to AlUla",
        "رحلتنا إلى العلا",
        2025,
    )
    assert (movie.original_language, movie.countries) == ("ar", ["SA"])
    assert movie.runtime_min == 91  # 90.5 minutes of video, rounded up
    assert (movie.rights_holder, movie.license_ref) == ("Mahmoud Family Archive", "OWN-2026-001")
    assert set(movie.metadata_locked_fields) == set(MANUAL_MOVIE_LOCKS)
    assert "status" not in movie.metadata_locked_fields
    assert list(movie.categories.all()) == [family]  # none chosen: the library's
    assert list(movie.genres.all()) == [genre]
    assert movie.status == TitleStatus.READY  # a direct-play file plays at once
    assert data["xc_id"] > 0

    file = review.media_file
    file.refresh_from_db()
    assert (file.movie_id, file.state, file.match_confidence) == (movie.pk, FileState.MATCHED, None)
    review.refresh_from_db()
    assert (review.status, review.chosen_provider, review.chosen_provider_id) == (
        ReviewStatus.RESOLVED,
        "manual",
        None,
    )
    assert (review.decided_by, review.decided_at is not None) == (owner, True)

    created = AuditLog.objects.get(action="title.create_manual")
    after = created.after or {}
    assert created.actor == owner
    assert after["rights_confirmed"] is True
    assert after["rights_holder"] == "Mahmoud Family Archive"
    assert after["images"] == ["poster"]
    decided = AuditLog.objects.get(action="review.manual")
    assert (decided.target_id, (decided.after or {})["rights_confirmed"]) == (str(review.pk), True)

    names = [name for name, _args in queued]
    assert names.count("prepare_media_file") == 1  # the media pipeline takes over
    stored = [args[0] for name, args in queued if name == "store_title_image"]
    assert [(r["kind"], r["primary"], r["title_id"]) for r in stored] == [
        ("poster", True, str(movie.pk))
    ]
    assert base64.b64decode(stored[0]["upload_b64"]) == png()
    # The backdrop wasn't uploaded: the worker takes it from a frame.
    assert ("frame_artwork", (str(file.pk), ["backdrop"])) in queued


def test_create_from_a_skipped_review_with_chosen_categories(
    owner_client: APIClient, library: Library, queued: list[Any]
) -> None:
    family = Category.objects.create(
        kind=CategoryKind.VOD, slug="family", name_en="Family", name_ar="عائلي"
    )
    kids = Category.objects.create(
        kind=CategoryKind.VOD, slug="kids", name_en="Kids", name_ar="أطفال"
    )
    library.default_categories.add(family)
    review = review_file(library, status=ReviewStatus.SKIPPED)

    response = owner_client.post(
        MANUAL,
        body(review, categories=[str(kids.pk)], runtime_min=95),
        format="json",
        headers=ADMIN,
    )

    assert response.status_code == 201, response.json()
    movie = Movie.objects.get(pk=response.json()["id"])
    assert list(movie.categories.all()) == [kids]  # what the admin ticked, nothing added
    assert movie.runtime_min == 95
    review.refresh_from_db()
    assert review.status == ReviewStatus.RESOLVED
    assert ("frame_artwork", (str(review.media_file_id), ["poster", "backdrop"])) in queued


def test_create_from_a_file_closes_its_open_review(
    owner_client: APIClient, library: Library, queued: list[Any]
) -> None:
    review = review_file(library)
    review.kind = "tv"
    review.save()
    response = owner_client.post(
        MANUAL,
        {"title": "Home video", **RIGHTS, "media_file": str(review.media_file_id)},
        format="json",
        headers=ADMIN,
    )
    assert response.status_code == 201, response.json()
    review.refresh_from_db()
    assert (review.status, review.kind, review.chosen_provider) == ("resolved", "movie", "manual")
    assert AuditLog.objects.filter(action="review.manual", target_id=str(review.pk)).exists()


def test_a_previous_owner_is_refreshed(library: Library, queued: list[Any]) -> None:
    """A file matched again automatically lands in review still pointing at its old
    movie; once it is a manual movie's, the old one has no file left and hides."""
    review = review_file(library)
    old = Movie.objects.create(title="Wrong", tmdb_id=604, status=TitleStatus.READY)
    MediaFile.objects.filter(pk=review.media_file_id).update(movie=old)
    file = MediaFile.objects.get(pk=review.media_file_id)
    services.create_manual_movie(file=file, review=review, data=body(), actor=None, ip=None)
    old.refresh_from_db()
    assert old.status == TitleStatus.HIDDEN


def test_imported_rights_fill_the_expiry_and_prefill_the_form(
    owner_client: APIClient,
    library: Library,
    queued: list[Any],
    django_assert_num_queries: Capture,
) -> None:
    review = review_file(library)
    expiry = timezone.now() + timedelta(days=365)
    job = ImportJob.objects.create(
        origin=JobOrigin.LINK,
        library=library,
        rights_holder="Studio Al Khobar",
        license_ref="LIC-77",
        license_expires_at=expiry,
    )
    ImportFile.objects.create(
        job=job,
        library=library,
        storage_key=review.media_file.storage_key,
        remote_path="https://example.test/trip.mp4",
        kind=FileKind.VIDEO,
    )
    family = Category.objects.create(
        kind=CategoryKind.VOD, slug="family", name_en="Family", name_ar="عائلي"
    )
    library.default_categories.add(family)

    with django_assert_num_queries(4):  # review, import record, library categories, auth
        defaults = owner_client.get(f"{MANUAL}/defaults?review={review.pk}", headers=ADMIN)
    assert defaults.status_code == 200, defaults.json()
    data = defaults.json()
    assert (data["title"], data["year"], data["runtime_min"]) == ("Our Trip to AlUla", 2025, 91)
    assert (data["rights_holder"], data["license_ref"]) == ("Studio Al Khobar", "LIC-77")
    assert data["license_expires_at"] is not None
    assert data["categories"] == [str(family.pk)]
    by_file = owner_client.get(
        f"{MANUAL}/defaults?media_file={review.media_file_id}", headers=ADMIN
    )
    assert by_file.json()["title"] == "Our Trip to AlUla"
    assert owner_client.get(f"{MANUAL}/defaults", headers=ADMIN).status_code == 400

    response = owner_client.post(MANUAL, body(review), format="json", headers=ADMIN)
    movie = Movie.objects.get(pk=response.json()["id"])
    # The admin's rights stay; the expiry the form left empty comes from the import.
    assert (movie.rights_holder, movie.license_expires_at) == ("Mahmoud Family Archive", expiry)
    entry = AuditLog.objects.get(action="title.create_manual")
    assert (entry.after or {})["import_rights"] == ["license_expires_at"]


@pytest.mark.parametrize(
    ("change", "field", "code"),
    [
        ({"rights_confirmed": False}, "rights_confirmed", "rights_confirmation_required"),
        ({"rights_holder": "  "}, "rights_holder", "blank"),
        ({"license_ref": None}, "license_ref", "null"),
        ({"title": ""}, "title", "blank"),
        ({"original_language": "arabic"}, "original_language", "invalid_language_code"),
        ({"countries": ["XX"]}, "countries", "invalid_country"),
        ({"year": 1500}, "year", "min_value"),
        ({"review": None, "media_file": None}, "review", "null"),
    ],
)
@pytest.mark.usefixtures("queued")
def test_invalid_forms_are_refused(
    owner_client: APIClient, library: Library, change: dict[str, Any], field: str, code: str
) -> None:
    review = review_file(library)
    response = owner_client.post(MANUAL, {**body(review), **change}, format="json", headers=ADMIN)
    assert response.status_code == 400
    problem = response.json()
    assert problem["field_error_codes"][field] == [code], problem
    assert not Movie.objects.exists()


def test_service_level_refusals(library: Library, queued: list[Any]) -> None:
    review = review_file(library)
    file = review.media_file

    def refused(data: dict[str, Any], **kwargs: Any) -> ProblemError:
        with pytest.raises(ProblemError) as caught:
            services.create_manual_movie(
                file=kwargs.get("file", file),
                review=kwargs.get("review", review),
                data=data,
                images=kwargs.get("images", ()),
                actor=None,
                ip=None,
            )
        return caught.value

    def codes(problem: ProblemError) -> dict[str, list[str]]:
        return problem.field_error_codes or {}

    assert codes(refused(body(rights_confirmed=None))) == {
        "rights_confirmed": ["rights_confirmation_required"]
    }
    past = timezone.now() - timedelta(days=1)
    assert codes(refused(body(license_expires_at=past))) == {
        "license_expires_at": ["not_in_future"]
    }
    series = Category.objects.create(
        kind=CategoryKind.SERIES, slug="crime", name_en="Crime", name_ar="جريمة"
    )
    assert codes(refused(body(categories=[series]))) == {"categories": ["category_kind"]}
    junk = services.ManualImage("poster", b"not an image")
    assert codes(refused(body(), images=[junk])) == {"poster": ["unsupported_format"]}
    huge = services.ManualImage("backdrop", b"x" * (services.MANUAL_IMAGE_MAX_BYTES + 1))
    assert codes(refused(body(), images=[huge])) == {"backdrop": ["too_large"]}

    MatchReview.objects.filter(pk=review.pk).update(status=ReviewStatus.RESOLVED)
    problem = refused(body())
    assert (problem.problem_code, codes(problem)) == (
        ErrorCode.CONFLICT,
        {"non_field_errors": ["review_decided"]},
    )
    MatchReview.objects.filter(pk=review.pk).update(status=ReviewStatus.OPEN)
    MediaFile.objects.filter(pk=file.pk).update(duration_ms=None)
    assert codes(refused(body())) == {"non_field_errors": ["file_not_probed"]}
    MediaFile.objects.filter(pk=file.pk).update(duration_ms=1000, removed_at=timezone.now())
    assert codes(refused(body())) == {"non_field_errors": ["file_removed"]}
    MediaFile.objects.filter(pk=file.pk).update(removed_at=None, state=FileState.MATCHED)
    assert codes(refused(body(), review=None)) == {"non_field_errors": ["file_matched"]}
    assert not Movie.objects.exists()


def test_rbac_and_unknown_sources(
    make_admin: AdminFactory, library: Library, queued: list[Any]
) -> None:
    review = review_file(library)
    viewer = client_for(make_admin("viewer"))
    assert viewer.post(MANUAL, body(review), format="json", headers=ADMIN).status_code == 403
    assert viewer.get(f"{MANUAL}/defaults?review={review.pk}", headers=ADMIN).status_code == 403
    reviewer = client_for(make_admin(permissions=["library.review"]))
    missing = body(media_file="00000000-0000-7000-8000-000000000000")
    assert reviewer.post(MANUAL, missing, format="json", headers=ADMIN).status_code == 404
    response = reviewer.post(MANUAL, body(review), format="json", headers=ADMIN)
    assert response.status_code == 201, response.json()


def test_an_oversized_upload_is_refused_before_reading(
    owner_client: APIClient,
    library: Library,
    queued: list[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(services, "MANUAL_IMAGE_MAX_BYTES", 10)
    review = review_file(library)
    response = owner_client.post(
        MANUAL,
        {
            **body(review),
            "rights_confirmed": "true",
            "backdrop": SimpleUploadedFile("b.png", png(), content_type="image/png"),
        },
        format="multipart",
        headers=ADMIN,
    )
    assert response.status_code == 400
    assert response.json()["field_error_codes"] == {"backdrop": ["too_large"]}


# --- Reopening skipped reviews ----------------------------------------------------------------


def test_reopen_a_skipped_review(
    owner_client: APIClient, owner: Any, library: Library, make_admin: AdminFactory
) -> None:
    review = review_file(library, status=ReviewStatus.SKIPPED, state=FileState.ERROR)
    url = f"{QUEUE}/{review.pk}/reopen"
    assert client_for(make_admin("viewer")).post(url, headers=ADMIN).status_code == 403

    response = owner_client.post(url, headers=ADMIN)

    assert response.status_code == 200, response.json()
    assert (response.json()["status"], response.json()["decided_by"]) == ("open", None)
    review.media_file.refresh_from_db()
    assert review.media_file.state == FileState.REVIEW
    assert AuditLog.objects.get(action="review.reopen").actor == owner
    again = owner_client.post(url, headers=ADMIN)
    assert again.status_code == 409
    assert again.json()["field_error_codes"] == {"non_field_errors": ["review_not_skipped"]}


@pytest.mark.parametrize(
    ("setup", "code"),
    [
        ("matched", "file_matched"),
        ("matching", "file_matching"),
        ("removed", "file_removed"),
        ("open", "review_open_exists"),
    ],
)
def test_reopen_refusals(owner_client: APIClient, library: Library, setup: str, code: str) -> None:
    review = review_file(library, status=ReviewStatus.SKIPPED)
    files = MediaFile.objects.filter(pk=review.media_file_id)
    if setup == "matched":
        files.update(state=FileState.MATCHED)
    elif setup == "matching":
        files.update(state=FileState.MATCHING)
    elif setup == "removed":
        files.update(removed_at=timezone.now())
    else:
        MatchReview.objects.create(media_file=review.media_file, kind="movie", reason="ambiguous")
    response = owner_client.post(f"{QUEUE}/{review.pk}/reopen", headers=ADMIN)
    assert response.status_code == 409
    assert response.json()["field_error_codes"] == {"non_field_errors": [code]}


def test_resolved_manual_reviews_name_their_movie(
    owner_client: APIClient,
    library: Library,
    queued: list[Any],
    django_assert_num_queries: Capture,
) -> None:
    review = review_file(library)
    movie = services.create_manual_movie(
        file=review.media_file, review=review, data=body(), actor=None, ip=None
    )
    with django_assert_num_queries(3):
        rows = owner_client.get(f"{QUEUE}?status=resolved", headers=ADMIN).json()["results"]
    assert (rows[0]["chosen_provider"], rows[0]["media_file"]["movie"]) == ("manual", str(movie.pk))


# --- Refreshing, matching to TMDB later -------------------------------------------------------


@pytest.fixture
def manual_movie(library: Library, queued: list[Any]) -> Movie:
    review = review_file(library, key="The Matrix (1999).mp4")
    return services.create_manual_movie(
        file=review.media_file,
        review=review,
        data=body(
            title="My Matrix",
            title_ar="ماتريكس الخاص",
            overview_ar="نسخة العائلة.",
            categories=[
                Category.objects.create(
                    kind=CategoryKind.VOD, slug="sci-fi", name_en="Sci-Fi", name_ar="خيال"
                )
            ],
        ),
        actor=None,
        ip=None,
    )


def test_refreshing_a_manual_title_is_refused(
    owner_client: APIClient, manual_movie: Movie, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[Any] = []
    monkeypatch.setattr(metadata_tasks.refresh_title, "delay", lambda *a: sent.append(a))
    response = owner_client.post(
        f"/api/v1/admin/movies/{manual_movie.pk}/refresh-metadata", headers=ADMIN
    )
    assert response.status_code == 409
    assert response.json()["code"] == "MANUAL_METADATA"
    assert sent == []
    assert not AuditLog.objects.filter(action="movie.refresh").exists()
    with pytest.raises(ProblemError) as caught:
        services.refresh_movie(manual_movie)
    assert caught.value.problem_code == ErrorCode.MANUAL_METADATA
    metadata_tasks.refresh_title("movie", str(manual_movie.pk))  # the task leaves it alone
    manual_movie.refresh_from_db()
    assert manual_movie.metadata_source == MetadataSource.MANUAL


@pytest.mark.usefixtures("offline_tmdb")
def test_match_a_manual_title_to_tmdb_in_place(
    owner_client: APIClient, manual_movie: Movie, monkeypatch: pytest.MonkeyPatch
) -> None:
    class WithoutArabic(TMDBClient):
        """TMDB's fixture movie without its Arabic translation."""

        def movie_details(self, *args: Any, **kwargs: Any) -> Any:
            details = dict(super().movie_details(*args, **kwargs))
            details["translations"] = {"translations": []}
            return details

    images: list[Any] = []
    monkeypatch.setattr(services, "tmdb", WithoutArabic.offline)
    monkeypatch.setattr(metadata_tasks.fetch_title_images, "delay", lambda *a: images.append(a))
    monkeypatch.setattr("apps.metadata.services.transaction.on_commit", lambda fn, **_k: fn())
    xc_id = manual_movie.xc_id

    response = owner_client.post(
        f"/api/v1/admin/titles/{manual_movie.pk}/rematch",
        {"tmdb_id": 603},
        format="json",
        headers=ADMIN,
    )

    assert response.status_code == 200, response.json()
    assert response.json()["title_ids"] == [str(manual_movie.pk)]
    movie = Movie.objects.get(pk=manual_movie.pk)
    assert (movie.tmdb_id, movie.xc_id, movie.title) == (603, xc_id, "The Matrix")
    assert movie.metadata_source == MetadataSource.SYNTHETIC  # TMDB's fixtures in tests
    assert movie.overview.startswith("Synthetic")  # TMDB's English replaces the admin's
    # TMDB has no Arabic here: the admin's stays, locked against the next refresh.
    assert (movie.title_ar, movie.overview_ar) == ("ماتريكس الخاص", "نسخة العائلة.")
    assert movie.rights_holder == "Mahmoud Family Archive"
    assert [c.slug for c in movie.categories.all()] == ["sci-fi"]  # locked: kept
    assert movie.genres.exists()  # TMDB's genres
    locked = set(movie.metadata_locked_fields)
    assert {"rights_holder", "license_ref", "categories", "title_ar", "overview_ar"} <= locked
    assert not locked & {"title", "overview", "genres", "year"}
    assert ("movie", str(movie.pk)) in images
    after = AuditLog.objects.get(action="title.rematch").after or {}
    assert after["converted"] is True
    assert set(after["kept"]) == {"title_ar", "overview_ar"}


@pytest.mark.usefixtures("offline_tmdb")
def test_matching_to_a_tmdb_movie_already_in_the_catalogue_moves_the_files(
    owner_client: APIClient, manual_movie: Movie
) -> None:
    existing = services.upsert_movie(603, services.tmdb())
    response = owner_client.post(
        f"/api/v1/admin/titles/{manual_movie.pk}/rematch",
        {"tmdb_id": 603},
        format="json",
        headers=ADMIN,
    )
    assert response.status_code == 200, response.json()
    assert response.json()["title_ids"] == [str(existing.pk)]
    manual_movie.refresh_from_db()
    assert manual_movie.status == TitleStatus.HIDDEN  # no file left
    assert manual_movie.metadata_source == MetadataSource.MANUAL


def test_matching_again_automatically_or_with_tmdb_down_is_refused(
    owner_client: APIClient, manual_movie: Movie, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = f"/api/v1/admin/titles/{manual_movie.pk}/rematch"
    automatic = owner_client.post(url, {}, format="json", headers=ADMIN)
    assert (automatic.status_code, automatic.json()["code"]) == (409, "MANUAL_METADATA")

    class Down(TMDBClient):
        def movie_details(self, *_a: Any, **_k: Any) -> Any:
            raise TMDBError("down")

    monkeypatch.setattr(services, "tmdb", Down.offline)
    down = owner_client.post(url, {"tmdb_id": 603}, format="json", headers=ADMIN)
    assert down.status_code == 503
    manual_movie.refresh_from_db()
    assert (manual_movie.tmdb_id, manual_movie.metadata_source) == (None, MetadataSource.MANUAL)
    assert set(manual_movie.metadata_locked_fields) == set(MANUAL_MOVIE_LOCKS)


# --- The editor's new fields, genres ----------------------------------------------------------


@pytest.mark.usefixtures("offline_tmdb")
def test_edited_genres_language_and_countries_are_locked_against_refreshes(
    owner_client: APIClient, owner: Any
) -> None:
    movie = services.upsert_movie(603, services.tmdb())
    mine = Genre.objects.create(name_en="Home video", name_ar="فيديو منزلي")
    response = owner_client.patch(
        f"/api/v1/admin/movies/{movie.pk}",
        {"genres": [str(mine.pk)], "original_language": "AR", "countries": ["eg", "sa"]},
        format="json",
        headers=ADMIN,
    )
    assert response.status_code == 200, response.json()
    assert [g["name_en"] for g in response.json()["genres"]] == ["Home video"]
    services.refresh_movie(movie)
    movie.refresh_from_db()
    assert list(movie.genres.all()) == [mine]
    assert (movie.original_language, movie.countries) == ("ar", ["EG", "SA"])
    assert {"genres", "original_language", "countries"} <= set(movie.metadata_locked_fields)
    bad = owner_client.patch(
        f"/api/v1/admin/movies/{movie.pk}", {"countries": ["ZZ"]}, format="json", headers=ADMIN
    )
    assert bad.json()["field_error_codes"] == {"countries": ["invalid_country"]}
    update_title(movie, {"genres": []}, actor=owner, ip=None)
    assert not movie.genres.exists()


def test_genres_list(owner_client: APIClient, django_assert_num_queries: Capture) -> None:
    for name in ("Drama", "Action", "Comedy"):
        Genre.objects.create(name_en=name)
    with django_assert_num_queries(3):  # permissions, count, page: not one per genre
        response = owner_client.get("/api/v1/admin/genres?page_size=100", headers=ADMIN)
    assert [g["name_en"] for g in response.json()["results"]] == ["Action", "Comedy", "Drama"]
    Genre.objects.bulk_create(Genre(name_en=f"Genre {n}") for n in range(20))
    with django_assert_num_queries(3):
        owner_client.get("/api/v1/admin/genres?page_size=100", headers=ADMIN)


# --- Adult categories and the customer API ----------------------------------------------------


def test_a_manual_title_in_an_adult_category_follows_the_adult_rules(
    library: Library, queued: list[Any]
) -> None:
    adult = Category.objects.create(
        kind=CategoryKind.VOD, slug="late", name_en="Late night", name_ar="ليلي", is_adult=True
    )
    review = review_file(library)
    movie = services.create_manual_movie(
        file=review.media_file, review=review, data=body(categories=[adult]), actor=None, ip=None
    )
    assert movie.status == TitleStatus.READY
    every_category = CustomerScope()  # "every category" means every non-adult one
    assert not visible_movies(every_category).filter(pk=movie.pk).exists()
    granted = CustomerScope(categories=frozenset({adult.pk}))
    assert visible_movies(granted).filter(pk=movie.pk).exists()
    # Search filters `is_adult = false` unless an adult category was granted.
    assert index._title_doc("movie", movie)["is_adult"] is True


def test_the_customer_api_credits_no_provider(library: Library, queued: list[Any]) -> None:
    review = review_file(library)
    movie = services.create_manual_movie(
        file=review.media_file, review=review, data=body(), actor=None, ip=None
    )
    assert MovieDetailSerializer(context={}).get_attribution(movie) == "manual"
