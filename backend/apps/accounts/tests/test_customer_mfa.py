"""Optional two-factor sign-in for customers (ADR-0024): setting it up and switching it off
in the portal (after the password step-up), the code at sign-in on the portal and on the
API host, app sign-ins issued before it was switched on, and support's audited reset."""

from typing import Any

import pytest
from django.conf import settings
from rest_framework.test import APIClient

from apps.accounts import auth, crypto, customer_mfa, customer_tokens, mfa
from apps.accounts.models import MfaTotp, User
from apps.audit.models import AuditLog
from apps.conftest import AdminFactory, CustomerFactory
from apps.core.stores import state_redis

pytestmark = pytest.mark.django_db

PORTAL = {"host": settings.APP_HOST}
API = {"host": settings.API_HOST}
ADMIN = {"host": settings.ADMIN_HOST}
PASSWORD = "correct horse battery staple"  # noqa: S105 (test data)
FIXED_STEP = 60_000_000


@pytest.fixture(autouse=True)
def totp_clock(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """The authenticator's clock: tests move it by changing `clock[0]`."""
    clock = [FIXED_STEP]
    monkeypatch.setattr(mfa, "current_step", lambda now=None: clock[0])
    return clock


@pytest.fixture
def customer(make_customer: CustomerFactory) -> User:
    user = make_customer(name="Salma")
    user.set_password(PASSWORD)
    user.save()
    return user


@pytest.fixture
def portal(customer: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(customer)
    return client


def code_for(user: User, step_offset: int = 0) -> str:
    totp = MfaTotp.objects.get(user=user)
    return mfa.code_at(crypto.decrypt(totp.secret_encrypted), mfa.current_step() + step_offset)


def switch_on(portal: APIClient, user: User) -> None:
    setup = portal.post("/api/v1/me/mfa", headers=PORTAL)
    assert setup.status_code == 200, setup.json()
    assert setup["Cache-Control"] == "no-store"
    assert setup.json()["otpauth_uri"].startswith("otpauth://totp/")
    confirmed = portal.post("/api/v1/me/mfa/confirm", {"code": code_for(user)}, headers=PORTAL)
    assert confirmed.status_code == 200, confirmed.json()
    assert confirmed.json()["mfa_enabled"] is True


def sign_in(host: dict[str, str], code: str | None = None, **extra: Any) -> Any:
    body: dict[str, Any] = {"login": "salma", "password": PASSWORD, **extra}
    if code is not None:
        body["code"] = code
    return APIClient().post("/api/v1/auth/login", body, headers=host)


@pytest.fixture
def salma(customer: User) -> User:
    customer.username = "salma"
    customer.save(update_fields=["username"])
    return customer


def test_setting_it_up_needs_the_right_first_code(portal: APIClient, salma: User) -> None:
    assert portal.get("/api/v1/me", headers=PORTAL).json()["mfa_enabled"] is False
    portal.post("/api/v1/me/mfa", headers=PORTAL)
    wrong = portal.post("/api/v1/me/mfa/confirm", {"code": "000000"}, headers=PORTAL)
    assert wrong.status_code == 400
    assert wrong.json()["code"] == "MFA_INVALID"
    # Not on yet: the password alone still signs in.
    assert sign_in(PORTAL).status_code == 200
    switch_on(portal, salma)
    salma.refresh_from_db()
    assert salma.mfa_enabled
    assert AuditLog.objects.filter(action="customer.mfa_enable", target_id=str(salma.pk)).exists()
    # Once on, setting it up again is refused until it is switched off.
    again = portal.post("/api/v1/me/mfa", headers=PORTAL)
    assert again.status_code == 409


def test_a_new_secret_each_time_until_confirmed(portal: APIClient, salma: User) -> None:
    first = portal.post("/api/v1/me/mfa", headers=PORTAL).json()["otpauth_uri"]
    second = portal.post("/api/v1/me/mfa", headers=PORTAL).json()["otpauth_uri"]
    assert first != second
    assert portal.post("/api/v1/me/mfa/confirm", {"code": "123456"}, headers=PORTAL).status_code
    assert MfaTotp.objects.filter(user=salma, confirmed_at__isnull=True).count() == 1
    # Confirming without a waiting secret is a conflict.
    MfaTotp.objects.filter(user=salma).delete()
    nothing = portal.post("/api/v1/me/mfa/confirm", {"code": "123456"}, headers=PORTAL)
    assert nothing.status_code == 409


@pytest.mark.parametrize("host", [PORTAL, API], ids=["portal", "api"])
def test_sign_in_asks_for_the_code(
    portal: APIClient, salma: User, host: dict[str, str], totp_clock: list[int]
) -> None:
    switch_on(portal, salma)
    totp_clock[0] += 2  # a later code than the one that switched it on
    missing = sign_in(host)
    assert missing.status_code == 403
    assert missing.json()["code"] == "MFA_REQUIRED"
    wrong = sign_in(host, "999999")
    assert wrong.status_code == 400
    assert wrong.json()["code"] == "MFA_INVALID"
    good = sign_in(host, code_for(salma))
    assert good.status_code == 200, good.json()
    if host is API:
        assert good.json()["token_type"] == "Bearer"  # noqa: S105 (not a password)
        assert good.json()["user"]["mfa_enabled"] is True
    # A code works once: the same one again is refused.
    replayed = sign_in(host, code_for(salma))
    assert replayed.status_code == 400
    # The wrong ones counted (the Lockouts page reads this counter); success cleared them.
    key = f"{auth.USER_MFA_FAILURES_PREFIX}{salma.pk}"
    assert state_redis().get(key) == b"1"


def test_wrong_codes_lock_the_account(portal: APIClient, salma: User) -> None:
    switch_on(portal, salma)
    answers = [sign_in(PORTAL, "111111").json()["code"] for _ in range(6)]
    assert answers[:4] == ["MFA_INVALID"] * 4
    assert answers[-1] == "ACCOUNT_LOCKED"


def test_the_account_limit_holds_across_addresses(portal: APIClient, salma: User) -> None:
    switch_on(portal, salma)
    state_redis().set(f"{auth.USER_MFA_FAILURES_PREFIX}{salma.pk}", auth.USER_MFA_FAILURES)
    blocked = sign_in(API, code_for(salma, 1))
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "ACCOUNT_LOCKED"
    customer_mfa.clear_failures(salma)
    assert sign_in(API, code_for(salma, 1)).status_code == 200


def test_app_sign_ins_from_before_keep_refreshing(portal: APIClient, salma: User) -> None:
    before = sign_in(API, device_name="Phone").json()
    switch_on(portal, salma)
    refreshed = APIClient().post(
        "/api/v1/auth/refresh", {"refresh_token": before["refresh_token"]}, headers=API
    )
    assert refreshed.status_code == 200
    assert customer_tokens.authenticate(refreshed.json()["access_token"]) is not None
    # A new app sign-in needs the code.
    assert sign_in(API, device_name="Tablet").status_code == 403


def test_switching_it_off(portal: APIClient, salma: User) -> None:
    switch_on(portal, salma)
    assert portal.delete("/api/v1/me/mfa", headers=PORTAL).status_code == 204
    salma.refresh_from_db()
    assert not salma.mfa_enabled
    assert not MfaTotp.objects.filter(user=salma).exists()
    assert AuditLog.objects.filter(action="customer.mfa_disable", target_id=str(salma.pk)).exists()
    assert sign_in(PORTAL).status_code == 200


@pytest.mark.step_up
def test_changes_need_the_password_again(salma: User) -> None:
    client = APIClient()
    client.force_authenticate(salma)
    for method in ("post", "delete"):
        response = getattr(client, method)("/api/v1/me/mfa", headers=PORTAL)
        assert response.status_code == 403
        assert response.json()["code"] == "STEP_UP_REQUIRED"
    confirm = client.post("/api/v1/me/mfa/confirm", {"code": "123456"}, headers=PORTAL)
    assert confirm.json()["code"] == "STEP_UP_REQUIRED"


def test_support_resets_it(
    portal: APIClient, salma: User, make_admin: AdminFactory, owner_client: APIClient
) -> None:
    switch_on(portal, salma)
    detail = owner_client.get(f"/api/v1/admin/customers/{salma.pk}", headers=ADMIN).json()
    assert detail["mfa_enabled"] is True
    reset = owner_client.post(f"/api/v1/admin/customers/{salma.pk}/reset-mfa", headers=ADMIN)
    assert reset.status_code == 204
    log = AuditLog.objects.get(action="customer.mfa_reset", target_id=str(salma.pk))
    assert log.actor is not None
    assert sign_in(PORTAL).status_code == 200
    again = owner_client.post(f"/api/v1/admin/customers/{salma.pk}/reset-mfa", headers=ADMIN)
    assert again.status_code == 409
    # Support staff without customers.edit can't.
    viewer = APIClient()
    viewer.force_authenticate(make_admin(permissions=["customers.view"]))
    denied = viewer.post(f"/api/v1/admin/customers/{salma.pk}/reset-mfa", headers=ADMIN)
    assert denied.status_code == 403


@pytest.mark.step_up
def test_support_confirms_with_a_code_first(salma: User, owner_client: APIClient) -> None:
    response = owner_client.post(f"/api/v1/admin/customers/{salma.pk}/reset-mfa", headers=ADMIN)
    assert response.status_code == 403
    assert response.json()["code"] == "STEP_UP_REQUIRED"
