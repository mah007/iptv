"""Step-up, the admin allowlist and the sign-in hardening of M14a (ADR-0021):
G-01, G-02, G-03, G-05, G-31 and G-40's allowlist."""

import time
from importlib import import_module
from typing import Any

import pytest
from django.conf import settings
from django.contrib.auth.hashers import MD5PasswordHasher
from django.urls import URLPattern, URLResolver, get_resolver
from rest_framework.test import APIClient

from apps.accounts import mfa, stepup
from apps.accounts.models import Device, DeviceKind, User
from apps.accounts.tests.test_auth_api import ADMIN, ME, VERIFY, code_for, enrol, login
from apps.audit.models import AuditLog
from apps.conftest import AdminFactory, CustomerFactory
from apps.core.services import set_setting
from apps.notifications.models import NotificationOutbox

pytestmark = [pytest.mark.django_db, pytest.mark.step_up]

PORTAL = {"host": settings.APP_HOST}
API = {"host": settings.API_HOST}
STEP_UP = "/api/v1/auth/step-up"
SETTING = "/api/v1/admin/settings/branding.service_name_en"
CUSTOMER_PASSWORD = "cust-pass-1234"  # noqa: S105 (a test password)


@pytest.fixture(autouse=True)
def _frozen_totp_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mfa, "current_step", lambda now=None: 60_000_000)


@pytest.fixture
def sam(make_admin: AdminFactory) -> User:
    return make_admin("owner", username="sam")


def signed_in(user: User) -> APIClient:
    client = APIClient()
    assert enrol(client, user).status_code == 200
    return client


def rename(client: APIClient, value: str = "Renamed") -> Any:
    return client.patch(SETTING, {"value": value}, format="json", headers=ADMIN)


# --- Admins ---------------------------------------------------------------------------------


def test_signing_in_counts_and_locking_asks_again(sam: User) -> None:
    client = signed_in(sam)
    assert client.get(ME, headers=ADMIN).json()["step_up_until"] is not None
    assert rename(client).status_code == 200
    assert client.delete(STEP_UP, headers=ADMIN).status_code == 204
    assert client.get(ME, headers=ADMIN).json()["step_up_until"] is None
    refused = rename(client, "Again")
    assert refused.status_code == 403
    assert refused.json()["code"] == "STEP_UP_REQUIRED"
    confirmed = client.post(STEP_UP, {"code": code_for(sam, step_offset=1)}, headers=ADMIN)
    assert confirmed.status_code == 200, confirmed.json()
    assert confirmed["Cache-Control"] == "no-store"
    assert rename(client, "Again").status_code == 200
    assert AuditLog.objects.filter(action="auth.step_up").count() == 1


def test_a_password_alone_never_confirms_an_admin(sam: User) -> None:
    client = signed_in(sam)
    client.delete(STEP_UP, headers=ADMIN)
    response = client.post(STEP_UP, {"password": "admin-pass-1234"}, headers=ADMIN)
    assert response.status_code == 400


def test_the_window_follows_the_setting(sam: User, monkeypatch: pytest.MonkeyPatch) -> None:
    set_setting("security.admin_step_up_window_min", 1, actor=None)
    client = signed_in(sam)
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 61)
    assert rename(client).json()["code"] == "STEP_UP_REQUIRED"


def test_wrong_confirmations_end_the_session(sam: User) -> None:
    client = signed_in(sam)
    client.delete(STEP_UP, headers=ADMIN)
    for _ in range(stepup.FAIL_LIMIT - 1):
        assert client.post(STEP_UP, {"code": "000000"}, headers=ADMIN).status_code == 400
    last = client.post(STEP_UP, {"code": "000000"}, headers=ADMIN)
    assert last.status_code == 401
    assert client.get(ME, headers=ADMIN).status_code == 401
    assert AuditLog.objects.filter(action="auth.step_up_locked").count() == 1
    # Signing in again (password and code) proves it's them: confirmations work again.
    again = APIClient()
    login(again, "sam")
    assert (
        again.post(VERIFY, {"code": code_for(sam, step_offset=1)}, headers=ADMIN).status_code == 200
    )
    assert rename(again).status_code == 200


def _admin_views() -> list[tuple[str, type[Any]]]:
    found: list[tuple[str, type[Any]]] = []

    def walk(patterns: list[Any], prefix: str) -> None:
        for entry in patterns:
            if isinstance(entry, URLResolver):
                walk(entry.url_patterns, prefix + str(entry.pattern))
            elif isinstance(entry, URLPattern):
                view = getattr(entry.callback, "view_class", None)
                if view is not None:
                    found.append((prefix + str(entry.pattern), view))

    walk(get_resolver("config.urls_admin").url_patterns, "")
    return found


#: Every admin route whose methods need a confirmation (ADR-0021). Conditional ones
#: (a customer created with a login, a real rendition cleanup, a real revoke by app, key
#: registration and removal) check it in the view and are tested by behaviour.
EXPECTED_STEP_UP = {
    ("api/v1/admin/settings", "PATCH"),
    ("api/v1/admin/settings/<str:key>", "PATCH"),
    ("api/v1/admin/settings/<str:key>", "DELETE"),
    ("api/v1/admin/settings/<str:key>/adopt", "POST"),
    ("api/v1/admin/settings/<str:key>/image", "POST"),
    ("api/v1/admin/integrations/<slug:name>/test", "POST"),
    ("api/v1/admin/customers/<uuid:pk>/devices", "POST"),
    ("api/v1/admin/customers/<uuid:pk>/password-invite", "POST"),
    ("api/v1/admin/devices/<uuid:pk>/reset-credentials", "POST"),
    ("api/v1/admin/access-rules/<uuid:pk>", "DELETE"),
    ("api/v1/admin/roles", "POST"),
    ("api/v1/admin/roles/<uuid:pk>", "PATCH"),
    ("api/v1/admin/roles/<uuid:pk>", "DELETE"),
    ("api/v1/admin/admins", "POST"),
    ("api/v1/admin/admins/<uuid:pk>", "PATCH"),
    ("api/v1/admin/admins/<uuid:pk>/reset-mfa", "POST"),
    ("api/v1/admin/backups/run", "POST"),
    ("api/v1/admin/plans/<uuid:pk>", "DELETE"),
    ("api/v1/admin/plans/<uuid:pk>/migrate-subscriptions", "POST"),
    ("api/v1/admin/subscriptions", "POST"),
    ("api/v1/admin/subscriptions/trial", "POST"),
    ("api/v1/admin/subscriptions/<uuid:pk>/extend", "POST"),
    ("api/v1/admin/subscriptions/<uuid:pk>/change-plan", "POST"),
    ("api/v1/admin/subscriptions/<uuid:pk>/approve", "POST"),
    ("api/v1/admin/payments", "POST"),
    ("api/v1/admin/payments/<uuid:pk>/refund", "POST"),
    ("api/v1/admin/categories/<uuid:pk>", "DELETE"),
    ("api/v1/admin/collections/<uuid:pk>", "DELETE"),
    ("api/v1/admin/libraries/<uuid:pk>", "DELETE"),
    ("api/v1/admin/live/channels/<uuid:pk>", "DELETE"),
    ("api/v1/admin/live/epg/sources/<uuid:pk>", "DELETE"),
    ("api/v1/admin/live/integrations/<uuid:pk>", "DELETE"),
    ("api/v1/admin/titles/<uuid:pk>/renditions/<uuid:rendition>", "DELETE"),
    ("api/v1/admin/titles/<uuid:pk>/tracks/<uuid:track>", "DELETE"),
    ("api/v1/admin/titles/<uuid:pk>/images/<uuid:image>", "DELETE"),
    ("api/v1/admin/templates/<slug:key>/<slug:channel>/<slug:locale>", "DELETE"),
    ("api/v1/admin/customers/<uuid:pk>/risk/reset-credentials", "POST"),
    ("api/v1/admin/geoip/update", "POST"),
}


def test_every_sensitive_admin_route_needs_a_confirmation() -> None:
    declared = {
        (route, method) for route, view in _admin_views() for method in stepup.methods_of(view)
    }
    # Other milestones (M15) may protect more of their own routes; ours must all be there.
    missing = EXPECTED_STEP_UP - declared
    assert not missing, f"routes without step-up: {sorted(missing)}"
    # Protective actions stay fast for incidents: no confirmation.
    for route in (
        "api/v1/admin/devices/<uuid:pk>/block",
        "api/v1/admin/devices/<uuid:pk>/revoke",
        "api/v1/admin/customers/<uuid:pk>/suspend",
        "api/v1/admin/sessions/<uuid:pk>/kill",
        "api/v1/admin/subscriptions/<uuid:pk>/suspend",
        "api/v1/admin/customers/<uuid:pk>/risk/suspend",
    ):
        assert (route, "POST") not in declared, route


def test_conditional_confirmations(sam: User, make_customer: CustomerFactory) -> None:
    client = signed_in(sam)
    client.delete(STEP_UP, headers=ADMIN)
    plain = client.post(
        "/api/v1/admin/customers", {"name": "No login"}, format="json", headers=ADMIN
    )
    assert plain.status_code == 201
    with_login = client.post(
        "/api/v1/admin/customers",
        {"name": "With login", "device": {"name": "TV"}},
        format="json",
        headers=ADMIN,
    )
    assert with_login.json()["code"] == "STEP_UP_REQUIRED"
    dry = client.post(
        "/api/v1/admin/devices/revoke-by-app", {"app_hint": "ibo"}, format="json", headers=ADMIN
    )
    assert dry.status_code == 200
    assert dry.json() == {"devices": 0, "dry_run": True}
    real = client.post(
        "/api/v1/admin/devices/revoke-by-app",
        {"app_hint": "ibo", "dry_run": False},
        format="json",
        headers=ADMIN,
    )
    assert real.json()["code"] == "STEP_UP_REQUIRED"
    cleanup = client.post(
        "/api/v1/admin/renditions/cleanup", {"dry_run": False}, format="json", headers=ADMIN
    )
    assert cleanup.json()["code"] == "STEP_UP_REQUIRED"
    keys = client.post("/api/v1/auth/webauthn/credentials/options", headers=ADMIN)
    assert keys.json()["code"] == "STEP_UP_REQUIRED"


def test_idle_timeout_is_on_me_for_every_admin(make_admin: AdminFactory) -> None:
    viewer = make_admin(permissions=["customers.view"], username="vee")
    set_setting("security.admin_idle_timeout_min", 12, actor=None)
    body = signed_in(viewer).get(ME, headers=ADMIN).json()
    assert body["idle_timeout_min"] == 12
    assert body["step_up_window_min"] == 5
    assert body["mfa_methods"] == ["totp"]


# --- G-03: second-factor guessing ----------------------------------------------------------


def test_a_pending_sign_in_allows_a_few_wrong_codes(sam: User, settings: Any) -> None:
    settings.AXES_FAILURE_LIMIT = 50  # the per-address lockout is tested elsewhere
    signed_in(sam)
    client = APIClient()
    login(client, "sam")
    for _ in range(5):
        assert client.post(VERIFY, {"code": "000000"}, headers=ADMIN).status_code == 400
    # The password is needed again, even with the right code.
    response = client.post(VERIFY, {"code": code_for(sam, step_offset=1)}, headers=ADMIN)
    assert response.status_code == 401


def test_wrong_codes_from_many_addresses_lock_the_account(sam: User) -> None:
    signed_in(sam)
    for number in range(10):
        client = APIClient(REMOTE_ADDR=f"198.51.100.{number}")
        login(client, "sam")
        client.post(VERIFY, {"code": "000000"}, headers=ADMIN)
    client = APIClient(REMOTE_ADDR="192.0.2.99")
    response = login(client, "sam")
    assert response.status_code == 429
    assert response.json()["code"] == "ACCOUNT_LOCKED"


def test_the_session_key_changes_at_each_step(sam: User) -> None:
    signed_in(sam)
    client = APIClient()
    client.get("/api/v1/auth/csrf", headers=ADMIN)
    login(client, "sam")
    after_password = client.session.session_key
    client.post(VERIFY, {"code": code_for(sam, step_offset=1)}, headers=ADMIN)
    assert client.session.session_key not in {None, after_password}


# --- G-01: the same work for unknown users ------------------------------------------------


@pytest.mark.parametrize("host", [settings.ADMIN_HOST, settings.APP_HOST])
def test_unknown_users_cost_one_hash_like_wrong_passwords(
    host: str, sam: User, make_customer: CustomerFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    customer = make_customer(username="cus-known")
    customer.set_password(CUSTOMER_PASSWORD)
    customer.save()
    # MD5 (the tests' hasher) verifies by hashing again, so one hash per attempt means
    # the same Argon2 work in production whether or not the account exists.
    calls: list[str] = []
    real_encode = MD5PasswordHasher.encode

    def encode(self: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append("hash")
        return real_encode(self, *args, **kwargs)

    monkeypatch.setattr(MD5PasswordHasher, "encode", encode)
    known = "sam" if host == settings.ADMIN_HOST else "cus-known"
    for name in (known, "nobody-at-all"):
        calls.clear()
        APIClient().post(
            "/api/v1/auth/login", {"login": name, "password": "wrong"}, headers={"host": host}
        )
        assert len(calls) == 1, (name, calls)


# --- G-05: no self-escalation --------------------------------------------------------------


def test_non_owners_grant_only_what_they_hold(make_admin: AdminFactory) -> None:
    manager = make_admin(permissions=["admins.manage", "roles.manage"], username="manny")
    owner = make_admin("owner", username="olga")
    client = APIClient()
    client.force_authenticate(manager)
    role = client.post(
        "/api/v1/admin/roles",
        {"name": "too-much", "permissions": ["settings.edit"]},
        format="json",
        headers=ADMIN,
    )
    assert role.status_code == 403
    from apps.accounts.models import Role  # noqa: PLC0415

    admin_role = Role.objects.get(name="admin")
    created = client.post(
        "/api/v1/admin/admins",
        {"username": "newbie", "role_ids": [str(admin_role.pk)]},
        format="json",
        headers=ADMIN,
    )
    assert created.status_code == 403
    own = client.patch(
        f"/api/v1/admin/admins/{manager.pk}",
        {"role_ids": [str(admin_role.pk)]},
        format="json",
        headers=ADMIN,
    )
    assert own.status_code in {403, 409}
    owner_change = client.patch(
        f"/api/v1/admin/admins/{owner.pk}", {"name": "Not yours"}, format="json", headers=ADMIN
    )
    assert owner_change.status_code == 403


# --- G-40: the admin IP allowlist ------------------------------------------------------------


def test_the_admin_allowlist(sam: User) -> None:
    client = signed_in(sam)
    # Saving a list without your own address is refused (no self-lockout).
    refused = client.patch(
        "/api/v1/admin/settings/security.admin_ip_allowlist",
        {"value": "198.51.100.0/24"},
        format="json",
        headers=ADMIN,
    )
    assert refused.status_code == 400
    assert refused.json()["field_error_codes"]["value"] == ["allowlist_excludes_you"]
    set_setting("security.admin_ip_allowlist", "198.51.100.0/24", actor=None)
    blocked = client.get(ME, headers=ADMIN)
    assert blocked.status_code == 403
    assert blocked.json()["code"] == "IP_NOT_ALLOWED"
    assert client.get("/api/v1/branding", headers=ADMIN).status_code == 200
    allowed = APIClient(REMOTE_ADDR="198.51.100.7")
    assert allowed.get("/api/v1/auth/csrf", headers=ADMIN).status_code == 204
    # The monitoring host's forward-auth applies it too.
    forward = APIClient(REMOTE_ADDR="203.0.113.9").get(
        "/internal/monitoring-auth", headers={"host": "web"}
    )
    assert forward.status_code == 403
    # Other hosts don't care.
    assert APIClient().get("/api/v1/health", headers=PORTAL).status_code == 200


# --- G-31 and G-02: customers --------------------------------------------------------------


@pytest.fixture
def customer(make_customer: CustomerFactory) -> User:
    user = make_customer(name="Omar", max_devices=3, email="omar@example.com")
    user.set_password(CUSTOMER_PASSWORD)
    user.save()
    return user


def portal(customer: User) -> APIClient:
    client = APIClient()
    response = client.post(
        "/api/v1/auth/login",
        {"login": customer.username, "password": CUSTOMER_PASSWORD},
        headers=PORTAL,
    )
    assert response.status_code == 200
    return client


def add_tv(client: APIClient, headers: dict[str, str] = PORTAL) -> Any:
    return client.post(
        "/api/v1/me/devices", {"name": "Bedroom TV", "app_hint": "tivimate"}, headers=headers
    )


def test_adding_a_tv_app_needs_the_password_again(customer: User) -> None:
    client = portal(customer)
    assert add_tv(client).status_code == 201  # signing in counts
    assert client.delete(STEP_UP, headers=PORTAL).status_code == 204
    refused = add_tv(client)
    assert refused.status_code == 403
    assert refused.json()["code"] == "STEP_UP_REQUIRED"
    wrong = client.post(STEP_UP, {"password": "nope"}, headers=PORTAL)
    assert wrong.json()["code"] == "INVALID_CREDENTIALS"
    right = client.post(STEP_UP, {"password": CUSTOMER_PASSWORD}, headers=PORTAL)
    assert right.status_code == 200
    created = add_tv(client)
    assert created.status_code == 201
    # The customer hears about it; the message never holds the password (G-28, G-31).
    notice = NotificationOutbox.objects.filter(template_key="new_device").latest("created_at")
    assert notice.payload == {"device_name": "Bedroom TV", "app": "TiviMate"}
    assert created.json()["password"] not in str(notice.payload)


def test_app_tokens_confirm_too(customer: User) -> None:
    client = APIClient()
    tokens = client.post(
        "/api/v1/auth/login",
        {"login": customer.username, "password": CUSTOMER_PASSWORD, "device_name": "Phone"},
        headers=API,
    ).json()
    bearer = {**API, "Authorization": f"Bearer {tokens['access_token']}"}
    assert add_tv(client, bearer).status_code == 201
    client.delete(STEP_UP, headers=bearer)
    assert add_tv(client, bearer).json()["code"] == "STEP_UP_REQUIRED"
    assert client.post(STEP_UP, {"password": CUSTOMER_PASSWORD}, headers=bearer).status_code == 200
    assert add_tv(client, bearer).status_code == 201


def test_a_lock_holds_against_a_request_that_was_in_flight(customer: User) -> None:
    """Every request saves its session (SESSION_SAVE_EVERY_REQUEST). A page fires several
    requests at once, so one that loaded the session before the lock can save it after:
    the confirmation must not come back with it (it lives in redis-state, not the session)."""
    client = portal(customer)
    session_key = client.session.session_key
    in_flight = import_module(settings.SESSION_ENGINE).SessionStore(session_key=session_key)
    assert in_flight.items()  # read now, as the earlier request did
    assert client.delete(STEP_UP, headers=PORTAL).status_code == 204
    in_flight.modified = True
    in_flight.save()  # the slower request ends and writes back what it loaded
    refused = add_tv(client)
    assert refused.status_code == 403
    assert refused.json()["code"] == "STEP_UP_REQUIRED"
    assert client.get("/api/v1/me", headers=PORTAL).status_code == 200  # still signed in


def test_a_lock_holds_for_app_tokens_through_a_refresh(customer: User) -> None:
    client = APIClient()
    tokens = client.post(
        "/api/v1/auth/login",
        {"login": customer.username, "password": CUSTOMER_PASSWORD, "device_name": "Phone"},
        headers=API,
    ).json()
    bearer = {**API, "Authorization": f"Bearer {tokens['access_token']}"}
    assert client.delete(STEP_UP, headers=bearer).status_code == 204
    fresh = client.post(
        "/api/v1/auth/refresh", {"refresh_token": tokens["refresh_token"]}, headers=API
    ).json()
    renewed = {**API, "Authorization": f"Bearer {fresh['access_token']}"}
    refused = add_tv(client, renewed)
    assert refused.status_code == 403
    assert refused.json()["code"] == "STEP_UP_REQUIRED"


def test_a_new_login_stops_old_streams_and_tells_the_customer(
    customer: User, monkeypatch: pytest.MonkeyPatch, django_capture_on_commit_callbacks: Any
) -> None:
    client = portal(customer)
    device_id = add_tv(client).json()["device"]["id"]
    stopped: list[Any] = []
    from apps.playback import services as playback  # noqa: PLC0415

    monkeypatch.setattr(
        playback, "stop_device_sessions", lambda device, reason: stopped.append(device)
    )
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(f"/api/v1/me/devices/{device_id}/xtream-credentials", headers=PORTAL)
    assert response.status_code == 200
    assert [str(item) for item in stopped] == [device_id]  # G-02
    notice = NotificationOutbox.objects.get(template_key="credentials_reset")
    assert "password" not in notice.payload
    assert response.json()["password"] not in str(notice.payload)
    assert Device.objects.get(pk=device_id).kind == DeviceKind.XTREAM
