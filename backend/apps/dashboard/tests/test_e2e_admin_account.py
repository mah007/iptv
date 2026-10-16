"""`manage.py e2e_admin_account`: the admin the end-to-end suite signs in as."""

import io
from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.accounts.models import User, WebAuthnCredential
from apps.accounts.rbac import permission_codes
from apps.catalog.models import MatchReview, ReviewStatus
from apps.dashboard.tests.conftest import open_review
from apps.library.models import Library

pytestmark = pytest.mark.django_db

SECRET = "e2e-Pass-0123456789"  # noqa: S105 (a test value)


def run(monkeypatch: pytest.MonkeyPatch, *args: str, user: str = "e2e-admin") -> str:
    monkeypatch.setenv("E2E_ADMIN_USER", user)
    monkeypatch.setenv("E2E_ADMIN_PASS", SECRET)
    out = io.StringIO()
    call_command("e2e_admin_account", *args, stdout=out)
    return out.getvalue()


def test_creates_then_updates_an_owner_admin(
    settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.DEBUG = True
    assert "Created" in run(monkeypatch)
    user = User.objects.get(username="e2e-admin")
    assert user.is_staff
    assert user.check_password(SECRET)
    assert "customers.edit" in permission_codes(user)
    User.objects.filter(pk=user.pk).update(status="disabled")
    output = run(monkeypatch)
    assert "Updated" in output
    assert SECRET not in output
    user.refresh_from_db()
    assert user.status == "active"


def test_forgets_security_keys_of_earlier_runs(
    settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.DEBUG = True
    run(monkeypatch)
    user = User.objects.get(username="e2e-admin")
    WebAuthnCredential.objects.create(
        user=user, credential_id="a2V5LWZyb20tYS1kZWFkLWJyb3dzZXI", public_key=b"\x01", name="Old"
    )
    run(monkeypatch)
    assert not WebAuthnCredential.objects.filter(user=user).exists()


def test_refuses_without_debug_or_credentials(
    settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.DEBUG = False
    with pytest.raises(CommandError, match="DEBUG"):
        run(monkeypatch)
    settings.DEBUG = True
    monkeypatch.setenv("E2E_ADMIN_USER", "")
    with pytest.raises(CommandError, match="E2E_ADMIN_USER"):
        call_command("e2e_admin_account", stdout=io.StringIO())


def test_refuses_to_turn_a_customer_into_an_admin(
    settings: Any, monkeypatch: pytest.MonkeyPatch, customer_user: User
) -> None:
    settings.DEBUG = True
    with pytest.raises(CommandError, match="customer"):
        run(monkeypatch, user=customer_user.username)


def test_reopens_the_latest_decided_review_with_the_chosen_candidate_first(
    settings: Any, monkeypatch: pytest.MonkeyPatch, library: Library
) -> None:
    settings.DEBUG = True
    with pytest.raises(CommandError, match="No decided movie review"):
        run(monkeypatch, "--open-review")
    review = open_review(library)
    with pytest.raises(CommandError, match="No decided movie review"):
        run(monkeypatch, "--open-review")  # an open item without candidates doesn't count
    review.candidates = [{"id": 1, "title": "A"}, {"id": 2, "title": "B"}]
    review.save()
    assert "already has an open item" in run(monkeypatch, "--open-review")
    review.status = ReviewStatus.RESOLVED
    review.chosen_provider_id = 2
    review.decided_at = timezone.now()
    review.save()
    assert "Reopened" in run(monkeypatch, "--open-review")
    reopened = MatchReview.objects.get(status=ReviewStatus.OPEN)
    assert reopened.media_file_id == review.media_file_id
    assert [candidate["id"] for candidate in reopened.candidates] == [2, 1]


def test_sets_the_home_video_back_to_a_skipped_review(
    settings: Any, monkeypatch: pytest.MonkeyPatch, library: Library
) -> None:
    from apps.catalog.models import FileState, MediaFile, MetadataSource, Movie  # noqa: PLC0415
    from apps.dashboard.management.commands.e2e_admin_account import HOME_VIDEO  # noqa: PLC0415

    settings.DEBUG = True
    with pytest.raises(CommandError, match="media-ready"):
        run(monkeypatch, "--manual-review")
    file = MediaFile.objects.create(library=library, storage_key=HOME_VIDEO, state=FileState.REVIEW)
    MatchReview.objects.create(media_file=file, kind="movie", reason="no_candidates")
    assert "skipped" in run(monkeypatch, "--manual-review")
    (review,) = MatchReview.objects.filter(media_file=file)
    assert review.status == ReviewStatus.SKIPPED

    # An earlier run's manual movie goes; the file is back in review.
    manual = Movie.objects.create(title="Home video", metadata_source=MetadataSource.MANUAL)
    MediaFile.objects.filter(pk=file.pk).update(movie=manual, state=FileState.MATCHED)
    review.status = ReviewStatus.RESOLVED
    review.save()
    run(monkeypatch, "--manual-review")
    assert not Movie.objects.filter(pk=manual.pk).exists()
    file.refresh_from_db()
    assert (file.movie_id, file.state) == (None, FileState.REVIEW)
    assert MatchReview.objects.filter(media_file=file, status=ReviewStatus.SKIPPED).count() == 1

    # Anything else keeps its row: a provider's title only loses the file.
    kept = Movie.objects.create(title="Someone else's", tmdb_id=42)
    MediaFile.objects.filter(pk=file.pk).update(movie=kept, state=FileState.MATCHED)
    run(monkeypatch, "--manual-review")
    assert Movie.objects.filter(pk=kept.pk).exists()
    file.refresh_from_db()
    assert file.movie_id is None
