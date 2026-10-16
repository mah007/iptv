"""The rights stated with an import reach the title its file is matched to (ADR-0025)."""

from collections.abc import Callable
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.catalog.models import FileState, MediaFile, Movie, TitleStatus
from apps.imports.models import FileKind, ImportFile, ImportJob
from apps.imports.rights import apply_import_rights
from apps.library.models import Library
from apps.library.parsing import parse_path
from apps.metadata import services as metadata

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("offline_tmdb")]
KEY = "The Matrix (1999)/The.Matrix.1999.1080p.mkv"


@pytest.fixture
def library(make_library: Callable[..., Library]) -> Library:
    return make_library()


def imported(library: Library, key: str = KEY, **rights: object) -> ImportFile:
    job = ImportJob.objects.create(
        origin="link",
        library=library,
        rights_holder=str(rights.get("rights_holder", "Warner Bros. (licence 2026-17)")),
        license_ref=str(rights.get("license_ref", "LIC-2026-17")),
        license_expires_at=rights.get("license_expires_at"),  # type: ignore[misc]
    )
    return ImportFile.objects.create(
        job=job, library=library, storage_key=key, remote_path=key, kind=FileKind.VIDEO, size=1
    )


def media_file(library: Library, key: str = KEY) -> MediaFile:
    return MediaFile.objects.create(
        library=library,
        storage_key=key,
        parse_result=parse_path(key, library_kind="movies").to_json(),
        duration_ms=30_000,
        state=FileState.MATCHING,
    )


def test_a_matched_import_brings_its_rights(library: Library) -> None:
    record = imported(library)
    file = media_file(library)
    metadata.match_file(file)
    movie = Movie.objects.get(tmdb_id=603)
    assert (movie.rights_holder, movie.license_ref) == (
        "Warner Bros. (licence 2026-17)",
        "LIC-2026-17",
    )
    record.refresh_from_db()
    assert record.rights_applied_at is not None


def test_an_expired_licence_hides_the_title_at_once(library: Library) -> None:
    imported(library, license_expires_at=timezone.now() - timedelta(days=1))
    metadata.match_file(media_file(library))
    assert Movie.objects.get(tmdb_id=603).status == TitleStatus.LICENSE_EXPIRED


def test_rights_already_set_or_locked_are_kept(library: Library) -> None:
    movie = Movie.objects.create(
        title="The Matrix",
        tmdb_id=603,
        rights_holder="Owner",
        metadata_locked_fields=["license_ref"],
    )
    imported(library)
    file = media_file(library)
    assert apply_import_rights(file, movie) == []
    movie.refresh_from_db()
    assert (movie.rights_holder, movie.license_ref) == ("Owner", "")


def test_files_that_were_not_imported_change_nothing(library: Library) -> None:
    movie = Movie.objects.create(title="Other", tmdb_id=1)
    assert apply_import_rights(media_file(library, "Other (2000).mkv"), movie) == []
