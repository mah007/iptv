"""Changing one's own password while signed in (ADR-0023): admins (current password plus
a code or key) and customers (current password), other sign-ins ended, audited, emailed."""

from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.accounts import mfa
from apps.accounts.models import User
from apps.accounts.tests.test_auth_api import ADMIN, ME, PASSWORD, VERIFY, code_for, enrol, login
from apps.audit.models import AuditLog
from apps.conftest import AdminFactory
from apps.notifications.models import NotificationOutbox

pytestmark = pytest.mark.django_db
CHANGE = "/api/v1/auth/password"
NEW_PASSWORD = "a-much-longer-pass-" + "2026"  # a test password, built from parts


@pytest.fixture(autouse=True)
def _frozen_totp_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mfa, "current_step", lambda now=None: 60_000_000)


@pytest.fixture
def sam(make_admin: AdminFactory) -> User:
    user = make_admin("support", username="sam")
    user.email = "sam@example.com"
    user.set_password(PASSWORD)
    user.save()
    return user


def signed_in(user: User) -> APIClient:
    client = APIClient()
    assert enrol(client, user).status_code == 200
    return client


def another_session(user: User) -> APIClient:
    """A second browser: the password, then the next authenticator code."""
    client = APIClient()
    assert login(client, user.username).json()["status"] == "mfa_required"
    assert client.post(VERIFY, {"code": code_for(user, 1)}, headers=ADMIN).status_code == 200
    return client


def change(client: APIClient, current: str, new: str = NEW_PASSWORD) -> Any:
    return client.post(
        CHANGE, {"current_password": current, "new_password": new}, format="json", headers=ADMIN
    )


def test_an_admin_changes_their_password_and_other_sessions_end(sam: User) -> None:
    here = signed_in(sam)
    elsewhere = another_session(sam)
    assert elsewhere.get(ME, headers=ADMIN).status_code == 200
    response = change(here, PASSWORD)
    assert response.status_code == 204, response.content
    sam.refresh_from_db()
    assert sam.check_password(NEW_PASSWORD)
    assert here.get(ME, headers=ADMIN).status_code == 200
    assert here.get(ME, headers=ADMIN).json()["step_up_until"] is not None
    assert elsewhere.get(ME, headers=ADMIN).status_code in (401, 403)
    entry = AuditLog.objects.get(action="auth.password_change")
    assert entry.actor == sam
    assert NEW_PASSWORD not in str(entry.after)
    assert PASSWORD not in str(entry.before)
    mail = NotificationOutbox.objects.get(user=sam, template_key="password_changed")
    assert "changed_at" in mail.payload


def test_a_wrong_current_password_is_refused_and_counted(sam: User) -> None:
    client = signed_in(sam)
    for _ in range(4):
        refused = change(client, "not-the-password")
        assert refused.status_code == 400
        assert refused.json()["field_error_codes"] == {"current_password": ["wrong_password"]}
    signed_out = change(client, "not-the-password")
    assert signed_out.status_code == 401
    assert client.get(ME, headers=ADMIN).status_code in (401, 403)
    sam.refresh_from_db()
    assert sam.check_password(PASSWORD)


@pytest.mark.parametrize(
    ("new", "code"),
    [
        (PASSWORD, "password_unchanged"),
        ("short-1", "password_too_short"),
        ("123456789012345", "password_entirely_numeric"),
    ],
)
def test_the_new_password_is_checked(sam: User, new: str, code: str) -> None:
    response = change(signed_in(sam), PASSWORD, new)
    assert response.status_code == 400
    assert code in response.json()["field_error_codes"]["new_password"]


@pytest.mark.step_up
def test_changing_it_needs_a_recent_confirmation(sam: User) -> None:
    client = signed_in(sam)
    assert client.delete("/api/v1/auth/step-up", headers=ADMIN).status_code == 204
    refused = change(client, PASSWORD)
    assert refused.status_code == 403
    assert refused.json()["code"] == "STEP_UP_REQUIRED"


def test_customers_cannot_use_the_admin_route(make_customer: Any) -> None:
    client = APIClient()
    client.force_authenticate(make_customer())
    assert change(client, PASSWORD).status_code == 403
