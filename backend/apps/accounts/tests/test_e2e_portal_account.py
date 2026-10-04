"""`manage.py e2e_portal_account`: the customer the portal end-to-end journey signs in as."""

import io
from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.accounts.models import User
from apps.catalog.tests.builders import Builder, build, clean_stores
from apps.engagement.models import Favorite, WatchProgress

__all__ = ["build", "clean_stores"]

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("clean_stores")]

SECRET = "e2e-Portal-0123456789"  # noqa: S105 (a test value)


def run(monkeypatch: pytest.MonkeyPatch, user: str = "e2e-portal") -> str:
    monkeypatch.setenv("E2E_PORTAL_USER", user)
    monkeypatch.setenv("E2E_PORTAL_PASS", SECRET)
    out = io.StringIO()
    call_command("e2e_portal_account", stdout=out)
    return out.getvalue()


def test_creates_then_resets_a_customer(
    settings: Any, monkeypatch: pytest.MonkeyPatch, build: Builder
) -> None:
    settings.DEBUG = True
    assert "Created" in run(monkeypatch)
    user = User.objects.get(username="e2e-portal")
    assert not user.is_staff
    assert user.check_password(SECRET)
    assert user.access.max_streams == 3
    assert user.access.expires_at is None

    movie = build.movie("Watched")
    WatchProgress.objects.create(user=user, movie=movie, position_ms=60_000, duration_ms=600_000)
    Favorite.objects.create(user=user, movie=movie)
    User.objects.filter(pk=user.pk).update(status="suspended")
    output = run(monkeypatch)
    assert "Updated" in output
    assert SECRET not in output
    user.refresh_from_db()
    assert user.status == "active"
    assert not WatchProgress.objects.filter(user=user).exists()
    assert not Favorite.objects.filter(user=user).exists()


def test_refuses_without_debug_or_credentials(
    settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.DEBUG = False
    with pytest.raises(CommandError, match="DEBUG"):
        run(monkeypatch)
    settings.DEBUG = True
    monkeypatch.setenv("E2E_PORTAL_USER", "")
    with pytest.raises(CommandError, match="E2E_PORTAL_USER"):
        call_command("e2e_portal_account", stdout=io.StringIO())


def test_refuses_to_turn_an_admin_into_a_customer(
    settings: Any, monkeypatch: pytest.MonkeyPatch, staff_user: User
) -> None:
    settings.DEBUG = True
    with pytest.raises(CommandError, match="admin account"):
        run(monkeypatch, user=staff_user.username)
