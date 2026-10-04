"""Customer sign-in (SPEC §10 Auth, §11; ADR-0013): portal sessions with CSRF, app
bearer tokens with rotating refresh tokens, password links and lockouts."""

import re
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from django.conf import settings
from django.utils.http import urlsafe_base64_encode
from rest_framework.test import APIClient

from apps.accounts import customer_auth, customer_tokens
from apps.accounts.customer_auth import PasswordLink
from apps.accounts.models import Device, DeviceKind, User, UserStatus
from apps.audit.models import AuditLog
from apps.conftest import AdminFactory, CustomerFactory
from apps.core.services import set_setting

pytestmark = pytest.mark.django_db

PORTAL = {"host": settings.APP_HOST}
API = {"host": settings.API_HOST}
ADMIN = {"host": settings.ADMIN_HOST}
PASSWORD = "correct horse battery staple"  # noqa: S105 (test data)


@pytest.fixture
def customer(make_customer: CustomerFactory) -> User:
    user = make_customer(name="Nadia Haddad")
    user.phone = "+966501234567"
    user.set_password(PASSWORD)
    user.save()
    return user


@pytest.fixture
def outbox() -> Iterator[list[PasswordLink]]:
    sent: list[PasswordLink] = []

    def sender(link: PasswordLink) -> bool:
        sent.append(link)
        return True

    installed = customer_auth._sender  # notifications installs the real one at start
    customer_auth.set_password_link_sender(sender)
    yield sent
    customer_auth.set_password_link_sender(installed)


def portal_login(client: APIClient, login: str, password: str = PASSWORD) -> Any:
    return client.post("/api/v1/auth/login", {"login": login, "password": password}, headers=PORTAL)


def app_login(login: str, password: str = PASSWORD, **extra: Any) -> Any:
    return APIClient().post(
        "/api/v1/auth/login", {"login": login, "password": password, **extra}, headers=API
    )


# --- Portal sessions --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "login", ["customer1", "CUSTOMER1@example.com", "+966501234567", "0501234567"]
)
def test_portal_sign_in_by_username_email_or_phone(customer: User, login: str) -> None:
    customer.username = "customer1"
    customer.email = "customer1@example.com"
    customer.save()
    client = APIClient()
    response = portal_login(client, login)
    assert response.status_code == 200, response.json()
    assert response["Cache-Control"] == "no-store"
    body = response.json()
    assert body["id"] == str(customer.pk)
    assert body["access"]["status"] == "active"
    assert client.get("/api/v1/me", headers=PORTAL).status_code == 200
    expiry = client.session.get_expiry_age()
    assert expiry > 29 * 86400


def test_portal_sign_in_checks_csrf(customer: User) -> None:
    client = APIClient(enforce_csrf_checks=True)
    response = portal_login(client, customer.username)
    assert response.status_code == 403
    client.get("/api/v1/auth/csrf", headers=PORTAL)
    token = client.cookies[settings.CSRF_COOKIE_NAME].value
    response = client.post(
        "/api/v1/auth/login",
        {"login": customer.username, "password": PASSWORD},
        headers={**PORTAL, "X-CSRFToken": token},
    )
    assert response.status_code == 200
    # Signed in, unsafe requests need the (rotated) token too.
    assert client.patch("/api/v1/me", {"name": "X"}, headers=PORTAL).status_code == 403


def test_every_bad_sign_in_reads_the_same(
    customer: User, make_admin: AdminFactory, make_customer: CustomerFactory
) -> None:
    admin = make_admin("support", username="sam")
    disabled = make_customer()
    disabled.set_password(PASSWORD)
    disabled.status = UserStatus.DISABLED
    disabled.save()
    attempts = [
        (customer.username, "wrong password"),
        ("nobody", PASSWORD),
        (admin.username, "admin-pass-1234"),
        (disabled.username, PASSWORD),
    ]
    for login, password in attempts:
        response = portal_login(APIClient(), login, password)
        assert response.status_code == 400
        assert response.json()["code"] == "INVALID_CREDENTIALS"
        assert response.json()["detail"] == ("The username, email, phone or password is incorrect.")


def test_suspended_customers_still_sign_in(customer: User) -> None:
    customer.status = UserStatus.SUSPENDED
    customer.save()
    assert portal_login(APIClient(), customer.username).status_code == 200


def test_failed_sign_ins_lock_the_username_and_ip(customer: User) -> None:
    for _ in range(settings.AXES_FAILURE_LIMIT):
        portal_login(APIClient(), customer.username, "nope")
    response = portal_login(APIClient(), customer.username)
    assert response.status_code == 429
    assert response.json()["code"] == "ACCOUNT_LOCKED"


def test_the_ip_budget_covers_every_username(customer: User) -> None:
    set_setting("security.customer_auth_requests_per_ip", 5, actor=None)
    for index in range(5):
        portal_login(APIClient(), f"user{index}", "nope")
    response = portal_login(APIClient(), customer.username)
    assert response.status_code == 429
    assert response.json()["code"] == "RATE_LIMITED"
    assert int(response["Retry-After"]) > 0


def test_portal_logout_retires_the_browser_device(customer: User) -> None:
    client = APIClient()
    portal_login(client, customer.username)
    device = Device.objects.create(user=customer, kind=DeviceKind.WEB, name="Firefox on Linux")
    session = client.session
    session[customer_auth.SESSION_DEVICE_KEY] = str(device.pk)
    session.save()
    assert client.post("/api/v1/auth/logout", headers=PORTAL).status_code == 204
    device.refresh_from_db()
    assert device.revoked_at is not None
    assert client.get("/api/v1/me", headers=PORTAL).status_code == 401
    # Signed out already: still fine.
    assert APIClient().post("/api/v1/auth/logout", headers=PORTAL).status_code == 204


# --- App tokens -------------------------------------------------------------------------------


def test_app_sign_in_returns_tokens_on_a_new_app_device(customer: User) -> None:
    response = app_login(customer.username, device_name="Living room")
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["token_type"] == "Bearer"  # noqa: S105 (a scheme name)
    assert body["access_token"].startswith(customer_tokens.ACCESS_PREFIX)
    assert body["expires_in"] == 600
    assert body["user"]["id"] == str(customer.pk)
    device = Device.objects.get(user=customer, kind=DeviceKind.APP)
    assert device.name == "Living room"
    customer.refresh_from_db()
    assert customer.last_login is not None

    client = APIClient()
    bearer = {"Authorization": f"Bearer {body['access_token']}"}
    assert client.get("/api/v1/me", headers={**API, **bearer}).status_code == 200
    # Bearer tokens are no credential on the portal host.
    assert client.get("/api/v1/me", headers={**PORTAL, **bearer}).status_code == 401


def test_bad_bearer_tokens_answer_401_with_the_bearer_scheme(customer: User) -> None:
    client = APIClient()
    for value in ("Bearer nope", "Bearer siptv_at_unknown", "Bearer a b"):
        response = client.get("/api/v1/me", headers={**API, "Authorization": value})
        assert response.status_code == 401
        assert response.json()["code"] == "NOT_AUTHENTICATED"
        assert response["WWW-Authenticate"].startswith("Bearer")
    assert client.get("/api/v1/me", headers=API)["WWW-Authenticate"].startswith("Bearer")


def test_refresh_rotates_and_reuse_revokes_the_family(customer: User) -> None:
    first = app_login(customer.username).json()
    client = APIClient()
    rotated = client.post(
        "/api/v1/auth/refresh", {"refresh_token": first["refresh_token"]}, headers=API
    )
    assert rotated.status_code == 200
    second = rotated.json()
    assert second["refresh_token"] != first["refresh_token"]
    new_bearer = {"Authorization": f"Bearer {second['access_token']}"}
    assert client.get("/api/v1/me", headers={**API, **new_bearer}).status_code == 200

    reused = client.post(
        "/api/v1/auth/refresh", {"refresh_token": first["refresh_token"]}, headers=API
    )
    assert reused.status_code == 401
    assert reused.json()["code"] == "NOT_AUTHENTICATED"
    assert client.get("/api/v1/me", headers={**API, **new_bearer}).status_code == 401
    assert (
        client.post(
            "/api/v1/auth/refresh", {"refresh_token": second["refresh_token"]}, headers=API
        ).status_code
        == 401
    )
    assert Device.objects.get(user=customer, kind=DeviceKind.APP).revoked_at is not None
    assert AuditLog.objects.filter(action="auth.token_reuse").exists()


def test_app_logout_revokes_the_tokens_and_device(customer: User) -> None:
    body = app_login(customer.username).json()
    bearer = {"Authorization": f"Bearer {body['access_token']}"}
    client = APIClient()
    assert client.post("/api/v1/auth/logout", headers={**API, **bearer}).status_code == 204
    assert client.get("/api/v1/me", headers={**API, **bearer}).status_code == 401
    assert Device.objects.get(user=customer, kind=DeviceKind.APP).revoked_at is not None


def test_a_disabled_account_loses_its_tokens(customer: User) -> None:
    body = app_login(customer.username).json()
    customer.status = UserStatus.DISABLED
    customer.save()
    bearer = {"Authorization": f"Bearer {body['access_token']}"}
    assert APIClient().get("/api/v1/me", headers={**API, **bearer}).status_code == 401
    refresh = APIClient().post(
        "/api/v1/auth/refresh", {"refresh_token": body["refresh_token"]}, headers=API
    )
    assert refresh.status_code == 401


# --- Password links ---------------------------------------------------------------------------


def _link_params(link: PasswordLink) -> dict[str, str]:
    query = parse_qs(urlsplit(link.url).query)
    return {"uid": query["uid"][0], "token": query["token"][0]}


def test_forgot_password_emails_a_single_use_link(
    customer: User, outbox: list[PasswordLink]
) -> None:
    client = APIClient()
    response = client.post(
        "/api/v1/auth/password/forgot", {"login": customer.email}, headers=PORTAL
    )
    assert response.status_code == 202
    assert len(outbox) == 1
    link = outbox[0]
    assert link.purpose == "reset"
    assert link.url.startswith(f"{customer_auth.portal_origin()}/reset-password?")
    params = _link_params(link)

    new_password = "a much better passphrase"  # noqa: S105
    reset = client.post(
        "/api/v1/auth/password/reset", {**params, "password": new_password}, headers=PORTAL
    )
    assert reset.status_code == 204
    customer.refresh_from_db()
    assert customer.check_password(new_password)
    assert AuditLog.objects.filter(action="customer.password_set", target_id=str(customer.pk))
    again = client.post(
        "/api/v1/auth/password/reset", {**params, "password": "yet another phrase"}, headers=PORTAL
    )
    assert again.status_code == 400
    assert again.json()["field_error_codes"]["token"] == ["invalid_token"]
    assert portal_login(APIClient(), customer.username, new_password).status_code == 200


def test_forgot_password_answers_the_same_for_unknown_accounts(
    customer: User, outbox: list[PasswordLink], make_customer: CustomerFactory
) -> None:
    no_email = make_customer()
    no_email.email = ""
    no_email.save()
    for login in ("nobody@example.com", no_email.username):
        response = APIClient().post("/api/v1/auth/password/forgot", {"login": login}, headers=API)
        assert response.status_code == 202
    assert outbox == []


def test_reset_emails_are_limited_per_account(customer: User, outbox: list[PasswordLink]) -> None:
    for _ in range(5):
        APIClient().post("/api/v1/auth/password/forgot", {"login": customer.username}, headers=API)
    assert len(outbox) == 3


def test_reset_links_expire(
    customer: User, outbox: list[PasswordLink], monkeypatch: pytest.MonkeyPatch
) -> None:
    customer_auth.request_password_reset(_request(), customer.username)
    params = _link_params(outbox[0])
    later = datetime.now() + timedelta(minutes=61)  # noqa: DTZ005 (Django's naive clock)
    monkeypatch.setattr(customer_auth._LinkTokens, "_now", lambda self: later)
    response = APIClient().post(
        "/api/v1/auth/password/reset", {**params, "password": "long enough phrase"}, headers=API
    )
    assert response.status_code == 400


def test_weak_passwords_are_refused_with_codes(customer: User, outbox: list[PasswordLink]) -> None:
    customer_auth.request_password_reset(_request(), customer.username)
    response = APIClient().post(
        "/api/v1/auth/password/reset",
        {**_link_params(outbox[0]), "password": "12345"},
        headers=API,
    )
    assert response.status_code == 400
    codes = response.json()["field_error_codes"]["password"]
    assert "password_too_short" in codes


def test_a_reset_ends_app_sign_ins(
    customer: User, outbox: list[PasswordLink], django_capture_on_commit_callbacks: Any
) -> None:
    body = app_login(customer.username).json()
    customer_auth.request_password_reset(_request(), customer.username)
    with django_capture_on_commit_callbacks(execute=True):
        response = APIClient().post(
            "/api/v1/auth/password/reset",
            {**_link_params(outbox[0]), "password": "a fresh passphrase"},
            headers=PORTAL,
        )
    assert response.status_code == 204
    bearer = {"Authorization": f"Bearer {body['access_token']}"}
    assert APIClient().get("/api/v1/me", headers={**API, **bearer}).status_code == 401


def test_bad_links_are_refused(customer: User) -> None:
    for params in (
        {"uid": "bad", "token": "x-y"},
        {"uid": "MTIz", "token": "nope"},
        {"uid": urlsafe_base64_encode(str(customer.pk).encode()), "token": "1-2"},
    ):
        response = APIClient().post(
            "/api/v1/auth/password/reset", {**params, "password": "long enough phrase"}, headers=API
        )
        assert response.status_code == 400


# --- Admin invitations ------------------------------------------------------------------------


def test_admin_invites_a_customer_without_a_password(
    make_customer: CustomerFactory, owner_client: APIClient, outbox: list[PasswordLink]
) -> None:
    fresh = make_customer()
    assert not fresh.has_usable_password()
    response = owner_client.post(
        f"/api/v1/admin/customers/{fresh.pk}/password-invite", headers=ADMIN
    )
    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    body = response.json()
    assert body["emailed"] is True
    assert "welcome=1" in body["url"]
    assert outbox[0].purpose == "invite"
    entry = AuditLog.objects.get(action="customer.password_invite")
    assert "token" not in str(entry.after)

    query = parse_qs(urlsplit(body["url"]).query)
    reset = APIClient().post(
        "/api/v1/auth/password/reset",
        {"uid": query["uid"][0], "token": query["token"][0], "password": "my own passphrase"},
        headers=PORTAL,
    )
    assert reset.status_code == 204
    assert portal_login(APIClient(), fresh.username, "my own passphrase").status_code == 200


def test_invitations_without_a_sender_are_not_emailed(
    make_customer: CustomerFactory, owner_client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(customer_auth, "_sender", None)
    fresh = make_customer()
    body = owner_client.post(
        f"/api/v1/admin/customers/{fresh.pk}/password-invite", headers=ADMIN
    ).json()
    assert body["emailed"] is False
    assert re.search(r"[?&]token=", body["url"])


def test_invitations_need_customers_edit(
    make_customer: CustomerFactory, make_admin: AdminFactory, owner_client: APIClient
) -> None:
    fresh = make_customer()
    viewer = APIClient()
    viewer.force_authenticate(make_admin("viewer"))
    url = f"/api/v1/admin/customers/{fresh.pk}/password-invite"
    assert viewer.post(url, headers=ADMIN).status_code == 403
    fresh.status = UserStatus.DISABLED
    fresh.save()
    assert owner_client.post(url, headers=ADMIN).status_code == 409
    staff = User.objects.filter(is_staff=True).first()
    assert staff is not None
    assert (
        owner_client.post(
            f"/api/v1/admin/customers/{staff.pk}/password-invite", headers=ADMIN
        ).status_code
        == 404
    )


def test_browser_names_from_user_agents() -> None:
    assert (
        customer_auth.browser_name(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/140.0 Safari/537.36"
        )
        == "Chrome on Windows"
    )
    assert customer_auth.browser_name(
        "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Firefox/140.0"
    ) == ("Firefox on Linux")
    assert customer_auth.browser_name("curl/8.0") == "Web browser"


def _request() -> Any:
    from django.test import RequestFactory  # noqa: PLC0415

    return RequestFactory().post("/api/v1/auth/password/forgot", REMOTE_ADDR="10.0.0.9")
