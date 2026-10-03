"""Admin sign-in on the admin host: password, then TOTP (plan §3.1, SPEC §11)."""

import time
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from axes.models import AccessAttempt
from django.conf import settings
from django.test import RequestFactory
from rest_framework.test import APIClient

from apps.accounts import auth, crypto, lockout, mfa
from apps.accounts.models import MfaTotp, User, UserStatus
from apps.audit.models import AuditLog
from apps.conftest import AdminFactory
from apps.core.services import set_setting

pytestmark = pytest.mark.django_db

ADMIN = {"host": settings.ADMIN_HOST}
PASSWORD = "admin-pass-1234"  # noqa: S105 (make_admin's password)
LOGIN = "/api/v1/auth/login"
VERIFY = "/api/v1/auth/mfa/verify"
ME = "/api/v1/auth/me"
LOGOUT = "/api/v1/auth/logout"
CSRF = "/api/v1/auth/csrf"


def login(client: APIClient, login_value: str, password: str = PASSWORD) -> Any:
    return client.post(LOGIN, {"login": login_value, "password": password}, headers=ADMIN)


# A fixed TOTP step, so tests never straddle a 30-second boundary.
FIXED_STEP = 60_000_000


@pytest.fixture(autouse=True)
def _frozen_totp_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mfa, "current_step", lambda now=None: FIXED_STEP)


def code_for(user: User, step_offset: int = 0) -> str:
    totp = MfaTotp.objects.get(user=user)
    return mfa.code_at(crypto.decrypt(totp.secret_encrypted), mfa.current_step() + step_offset)


def enrol(client: APIClient, user: User) -> Any:
    assert login(client, user.username).json()["status"] == "mfa_setup_required"
    return client.post(VERIFY, {"code": code_for(user)}, headers=ADMIN)


@pytest.fixture
def admin(make_admin: AdminFactory) -> User:
    user = make_admin("support", username="sam")
    user.email = "sam@example.com"
    user.save()
    return user


def test_csrf_endpoint_sets_the_cookie() -> None:
    client = APIClient()
    response = client.get(CSRF, headers=ADMIN)
    assert response.status_code == 204
    assert settings.CSRF_COOKIE_NAME in response.cookies
    assert "no-cache" in response["Cache-Control"]


def test_first_sign_in_enrols_an_authenticator_then_signs_in(admin: User) -> None:
    client = APIClient()
    response = login(client, "sam")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "mfa_setup_required"
    assert response["Cache-Control"] == "no-store"
    uri = urlsplit(body["otpauth_uri"])
    assert uri.scheme == "otpauth"
    query = parse_qs(uri.query)
    assert query["issuer"] == ["Smart IPTV"]
    # The secret is stored encrypted, never in clear.
    totp = MfaTotp.objects.get(user=admin)
    assert query["secret"][0] not in totp.secret_encrypted
    assert crypto.decrypt(totp.secret_encrypted) == query["secret"][0]

    # Half signed in: nothing is reachable yet.
    assert client.get(ME, headers=ADMIN).status_code == 401
    assert client.get("/api/v1/admin/customers", headers=ADMIN).status_code == 401

    response = client.post(VERIFY, {"code": code_for(admin)}, headers=ADMIN)
    assert response.status_code == 200
    me = response.json()
    assert me["username"] == "sam"
    assert me["roles"] == ["support"]
    assert "customers.view" in me["permissions"]
    assert "settings.edit" not in me["permissions"]
    assert me["mfa_enabled"] is True

    admin.refresh_from_db()
    assert admin.mfa_enabled
    assert admin.last_login is not None
    assert admin.last_login_ip == "127.0.0.1"
    assert MfaTotp.objects.get(user=admin).confirmed_at is not None
    assert client.get(ME, headers=ADMIN).json()["username"] == "sam"
    assert client.get("/api/v1/admin/customers", headers=ADMIN).status_code == 200
    actions = AuditLog.objects.filter(target_id=str(admin.pk)).values_list("action", flat=True)
    assert sorted(actions) == ["auth.login", "auth.mfa_enroll"]


def test_enrolled_admins_need_a_fresh_code_and_codes_are_single_use(admin: User) -> None:
    first = APIClient()
    enrol(first, admin)
    used_code = code_for(admin)

    second = APIClient()
    response = login(second, "sam@EXAMPLE.com")  # email works too, case-insensitively
    assert response.json() == {"status": "mfa_required"}
    response = second.post(VERIFY, {"code": used_code}, headers=ADMIN)
    assert response.status_code == 400
    assert response.json()["code"] == "MFA_INVALID"
    # The next step's code (within the drift window) is accepted.
    response = second.post(VERIFY, {"code": code_for(admin, step_offset=1)}, headers=ADMIN)
    assert response.status_code == 200


def test_wrong_codes_are_refused_and_counted(admin: User) -> None:
    client = APIClient()
    login(client, "sam")
    response = client.post(VERIFY, {"code": "000000"}, headers=ADMIN)
    assert response.status_code == 400
    assert response.json()["code"] == "MFA_INVALID"
    assert AccessAttempt.objects.get(username="sam").failures_since_start == 1
    assert client.post(VERIFY, {"code": "12ab"}, headers=ADMIN).json()["code"] == "MFA_INVALID"


@pytest.mark.parametrize(
    "login_value",
    ["sam", "nobody", "customer"],
    ids=["wrong-password", "unknown-user", "customer-account"],
)
def test_invalid_credentials_look_the_same(
    login_value: str, admin: User, customer_user: User
) -> None:
    password = "wrong-password-1" if login_value == "sam" else "customer-pass-123"
    response = login(APIClient(), login_value, password)
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_CREDENTIALS"
    assert response.json()["detail"] == "The username or password is incorrect."


@pytest.mark.parametrize("status", [UserStatus.SUSPENDED, UserStatus.DISABLED])
def test_inactive_admins_cannot_sign_in(status: UserStatus, admin: User) -> None:
    admin.status = status
    admin.save()
    response = login(APIClient(), "sam")
    assert response.json()["code"] == "INVALID_CREDENTIALS"


def test_repeated_failures_lock_the_username_and_ip(admin: User) -> None:
    client = APIClient()
    for _ in range(settings.AXES_FAILURE_LIMIT - 1):
        assert login(client, "sam", "wrong-password-1").json()["code"] == "INVALID_CREDENTIALS"
    response = login(client, "sam", "wrong-password-1")
    assert response.status_code == 429
    assert response.json()["code"] == "ACCOUNT_LOCKED"
    assert int(response["Retry-After"]) == int(lockout.COOL_OFF_BASE.total_seconds())
    # Locked: even the right password is refused, without revealing it was right.
    response = login(client, "sam")
    assert response.status_code == 429
    assert response.json()["code"] == "ACCOUNT_LOCKED"


def test_cool_off_doubles_past_the_limit_and_is_capped(admin: User) -> None:
    request = RequestFactory().post(LOGIN)
    assert lockout.cool_off(None) == lockout.COOL_OFF_BASE
    assert lockout.cool_off(request) == lockout.COOL_OFF_BASE
    request.axes_login_username = "sam"  # type: ignore[attr-defined]
    request.axes_ip_address = "203.0.113.9"  # type: ignore[attr-defined]
    attempt = AccessAttempt.objects.create(
        username="sam",
        ip_address="203.0.113.9",
        user_agent="",
        http_accept="",
        path_info="",
        get_data="",
        post_data="",
        failures_since_start=settings.AXES_FAILURE_LIMIT,
    )
    assert lockout.cool_off(request) == lockout.COOL_OFF_BASE
    attempt.failures_since_start += 2
    attempt.save()
    assert lockout.cool_off(request) == lockout.COOL_OFF_BASE * 4
    attempt.failures_since_start += 20
    attempt.save()
    assert lockout.cool_off(request) == lockout.COOL_OFF_MAX


def test_a_locked_pair_cannot_finish_mfa(admin: User) -> None:
    client = APIClient()
    login(client, "sam")
    for _ in range(settings.AXES_FAILURE_LIMIT - 1):
        assert client.post(VERIFY, {"code": "000000"}, headers=ADMIN).status_code == 400
    response = client.post(VERIFY, {"code": "000000"}, headers=ADMIN)
    assert response.status_code == 429
    assert response.json()["code"] == "ACCOUNT_LOCKED"
    # The pending sign-in is gone; a correct code no longer helps.
    response = client.post(VERIFY, {"code": code_for(admin)}, headers=ADMIN)
    assert response.status_code == 401


def test_verify_needs_a_recent_password_step(admin: User, monkeypatch: pytest.MonkeyPatch) -> None:
    client = APIClient()
    response = client.post(VERIFY, {"code": "123456"}, headers=ADMIN)
    assert response.status_code == 401
    assert response.json()["code"] == "NOT_AUTHENTICATED"

    login(client, "sam")
    later = time.time() + auth.PENDING_TTL_S + 1
    monkeypatch.setattr(auth, "time", SimpleNamespace(time=lambda: later))
    response = client.post(VERIFY, {"code": code_for(admin)}, headers=ADMIN)
    assert response.status_code == 401


def test_enrolment_restarts_when_mfa_was_reset_meanwhile(admin: User) -> None:
    client = APIClient()
    enrol(client, admin)
    other = APIClient()
    assert login(other, "sam").json()["status"] == "mfa_required"
    MfaTotp.objects.filter(user=admin).update(confirmed_at=None)
    response = other.post(VERIFY, {"code": code_for(admin, step_offset=1)}, headers=ADMIN)
    assert response.status_code == 401
    assert response.json()["detail"] == "Sign in with your password again."


def test_sessions_follow_the_idle_timeout_setting(admin: User, owner: User) -> None:
    set_setting("security.admin_idle_timeout_min", 10, actor=owner)
    client = APIClient()
    enrol(client, admin)
    assert client.session.get_expiry_age() == 600


def test_a_new_password_step_ends_the_previous_session(
    admin: User, make_admin: AdminFactory
) -> None:
    client = APIClient()
    enrol(client, admin)
    other = make_admin("viewer", username="vic")
    assert login(client, "vic").json()["status"] == "mfa_setup_required"
    assert client.get(ME, headers=ADMIN).status_code == 401
    assert client.post(VERIFY, {"code": code_for(other)}, headers=ADMIN).json()["username"] == "vic"


def test_logout_ends_the_session(admin: User) -> None:
    client = APIClient()
    enrol(client, admin)
    response = client.post(LOGOUT, headers=ADMIN)
    assert response.status_code == 204
    assert client.get(ME, headers=ADMIN).status_code == 401
    assert AuditLog.objects.filter(action="auth.logout", actor=admin).exists()
    # Logging out while signed out is harmless.
    assert APIClient().post(LOGOUT, headers=ADMIN).status_code == 204


def test_me_refuses_customers(customer_user: User) -> None:
    client = APIClient()
    client.force_authenticate(customer_user)
    assert client.get(ME, headers=ADMIN).json()["code"] == "PERMISSION_DENIED"


def test_sign_in_requires_the_csrf_token(admin: User) -> None:
    client = APIClient(enforce_csrf_checks=True)
    response = login(client, "sam")
    assert response.status_code == 403
    assert response.json()["code"] == "PERMISSION_DENIED"
    assert not MfaTotp.objects.filter(user=admin).exists()

    client.get(CSRF, headers=ADMIN)
    token = client.cookies[settings.CSRF_COOKIE_NAME].value
    response = client.post(
        LOGIN,
        {"login": "sam", "password": PASSWORD},
        headers={**ADMIN, "X-CSRFToken": token, "Origin": f"http://{settings.ADMIN_HOST}"},
    )
    assert response.status_code == 200, response.json()
    assert response.json()["status"] == "mfa_setup_required"


def test_sign_in_rejects_a_malformed_body() -> None:
    response = APIClient().post(LOGIN, {"login": "sam"}, headers=ADMIN)
    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert "password" in response.json()["field_errors"]
