"""`manage.py e2e_admin_account`: the admin the end-to-end suite signs in as."""

import io
from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.accounts.models import User
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
    assert "already has an open item" in run(monkeypatch, "--open-review")
    review.candidates = [{"id": 1, "title": "A"}, {"id": 2, "title": "B"}]
    review.status = ReviewStatus.RESOLVED
    review.chosen_provider_id = 2
    review.decided_at = timezone.now()
    review.save()
    assert "Reopened" in run(monkeypatch, "--open-review")
    reopened = MatchReview.objects.get(status=ReviewStatus.OPEN)
    assert reopened.media_file_id == review.media_file_id
    assert [candidate["id"] for candidate in reopened.candidates] == [2, 1]
