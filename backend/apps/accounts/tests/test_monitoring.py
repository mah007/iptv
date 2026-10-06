"""Admin sign-in to the monitoring UIs on grafana.<domain> (ADR-0018).

The ticket exchange (one use, 60 s, unforgeable), the session behind Traefik's
forward-auth (a suspended admin or a removed permission loses access within 60 s),
the landing paths (relative only), and the doors on the right hosts only.
"""

import time
from collections.abc import Callable, Iterator
from typing import Any, cast
from unittest import mock
from urllib.parse import parse_qs, urlsplit

import pytest
import redis
from django.conf import settings
from django.test import Client
from rest_framework.test import APIClient

from apps.accounts import monitoring
from apps.accounts.models import Permission, Role, User, UserStatus
from apps.audit.models import AuditLog
from apps.conftest import AdminFactory, CustomerFactory
from apps.core.services import set_setting
from apps.core.stores import state_redis

pytestmark = pytest.mark.django_db

ADMIN = {"host": settings.ADMIN_HOST}
MONITOR = {"host": settings.GRAFANA_HOST}
INTERNAL = {"host": "web"}
TICKET_URL = "/api/v1/admin/monitoring/ticket"
NAVIGATION = {"accept": "text/html,application/xhtml+xml"}


@pytest.fixture(autouse=True)
def _clean_monitoring_keys() -> Iterator[None]:
    yield
    client = state_redis()
    for prefix in (monitoring.TICKET_PREFIX, monitoring.SESSION_PREFIX):
        for key in client.scan_iter(f"{prefix}*"):
            client.delete(key)


@pytest.fixture
def watcher(make_admin: AdminFactory) -> User:
    """An admin whose only grant is monitoring.view."""
    return make_admin(permissions=["monitoring.view"], username="watcher")


def ttl(key: bytes) -> int:
    return cast("int", state_redis().ttl(key))


def api(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user)
    return client


def ticket_of(url: str) -> str:
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}" == monitoring.monitoring_origin()
    assert parts.path == monitoring.CALLBACK_PATH
    (ticket,) = parse_qs(parts.query)["ticket"]
    return ticket


def sign_in(user: User, next_path: str = "/") -> Client:
    """Ticket from the admin API, then the callback: a browser with a monitoring cookie."""
    response = api(user).post(TICKET_URL, {"next": next_path}, format="json", headers=ADMIN)
    assert response.status_code == 200, response.content
    browser = Client()
    callback = browser.get(
        monitoring.CALLBACK_PATH, {"ticket": ticket_of(response.json()["url"])}, headers=MONITOR
    )
    assert callback.status_code == 302
    return browser


def ask(browser: Client, uri: str = "/d/iptv-edges", **headers: str) -> Any:
    """Traefik's forward-auth request for one request to grafana.<domain>."""
    return browser.get(
        "/internal/monitoring-auth",
        headers={**INTERNAL, "x-forwarded-uri": uri, "x-forwarded-method": "GET", **headers},
    )


# --- The admin API -----------------------------------------------------------------------


def test_the_ticket_needs_monitoring_view(make_admin: AdminFactory, watcher: User) -> None:
    support = make_admin("support")
    assert api(support).post(TICKET_URL, {}, format="json", headers=ADMIN).status_code == 403
    assert APIClient().post(TICKET_URL, {}, format="json", headers=ADMIN).status_code == 401
    response = api(watcher).post(TICKET_URL, {}, format="json", headers=ADMIN)
    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    assert ticket_of(response.json()["url"])


def test_customers_never_get_a_ticket(make_customer: CustomerFactory) -> None:
    customer = make_customer()
    assert api(customer).post(TICKET_URL, {}, format="json", headers=ADMIN).status_code == 403


def test_the_ticket_lives_on_the_admin_host_only(watcher: User) -> None:
    for host in (settings.API_HOST, settings.APP_HOST, settings.TV_HOST, settings.GRAFANA_HOST):
        response = api(watcher).post(TICKET_URL, {}, format="json", headers={"host": host})
        assert response.status_code == 404, host


def test_opening_monitoring_is_audited(watcher: User) -> None:
    api(watcher).post(TICKET_URL, {}, format="json", headers=ADMIN)
    entry = AuditLog.objects.get(action="monitoring.sign_in")
    assert entry.actor == watcher


def test_the_ticket_is_stored_hashed_and_expires_in_a_minute(watcher: User) -> None:
    url = monitoring.issue_ticket(watcher)
    raw = ticket_of(url)
    keys = list(state_redis().scan_iter(f"{monitoring.TICKET_PREFIX}*"))
    assert len(keys) == 1
    assert raw.encode() not in keys[0]
    assert 0 < ttl(keys[0]) <= monitoring.TICKET_TTL_S


def test_issuing_refuses_an_admin_without_the_permission(make_admin: AdminFactory) -> None:
    with pytest.raises(PermissionError):
        monitoring.issue_ticket(make_admin("support"))


# --- The ticket exchange -----------------------------------------------------------------


def test_a_ticket_opens_a_session_once(watcher: User) -> None:
    raw = ticket_of(monitoring.issue_ticket(watcher, "/d/iptv-edges?orgId=1"))
    browser = Client()
    response = browser.get(monitoring.CALLBACK_PATH, {"ticket": raw}, headers=MONITOR)
    assert response.status_code == 302
    assert response["Location"] == "/d/iptv-edges?orgId=1"
    assert response["Referrer-Policy"] == "no-referrer"
    assert "no-cache" in response["Cache-Control"]
    cookie = response.cookies[monitoring.cookie_name()]
    assert cookie["httponly"]
    assert cookie["samesite"] == "Lax"
    assert cookie["path"] == "/"
    assert not cookie["domain"]
    assert cookie["max-age"] == 8 * 3600
    # The same ticket a second time: nothing, and back to the admin.
    again = Client().get(monitoring.CALLBACK_PATH, {"ticket": raw}, headers=MONITOR)
    assert again["Location"] == f"{monitoring.admin_origin()}/monitoring?failed=1"
    assert monitoring.cookie_name() not in again.cookies


def test_an_expired_ticket_is_refused(watcher: User) -> None:
    raw = ticket_of(monitoring.issue_ticket(watcher))
    for key in state_redis().scan_iter(f"{monitoring.TICKET_PREFIX}*"):
        state_redis().delete(key)  # what Redis does after TICKET_TTL_S
    response = Client().get(monitoring.CALLBACK_PATH, {"ticket": raw}, headers=MONITOR)
    assert response["Location"].endswith("/monitoring?failed=1")
    assert monitoring.cookie_name() not in response.cookies


@pytest.mark.parametrize("forged", ["", "x", "a" * 43, "../../etc", "a" * 500])
def test_forged_tickets_are_refused(watcher: User, forged: str) -> None:
    monitoring.issue_ticket(watcher)  # a real ticket exists, but not this one
    response = Client().get(monitoring.CALLBACK_PATH, {"ticket": forged}, headers=MONITOR)
    assert response["Location"].endswith("/monitoring?failed=1")
    assert monitoring.cookie_name() not in response.cookies


def test_a_ticket_without_the_parameter_is_refused() -> None:
    response = Client().get(monitoring.CALLBACK_PATH, headers=MONITOR)
    assert response["Location"].endswith("/monitoring?failed=1")


def test_an_admin_suspended_between_ticket_and_callback_gets_nothing(watcher: User) -> None:
    raw = ticket_of(monitoring.issue_ticket(watcher))
    User.objects.filter(pk=watcher.pk).update(status=UserStatus.SUSPENDED)
    response = Client().get(monitoring.CALLBACK_PATH, {"ticket": raw}, headers=MONITOR)
    assert response["Location"].endswith("/monitoring?failed=1")


def test_the_session_length_is_a_setting(watcher: User) -> None:
    set_setting("security.monitoring_session_hours", 2, actor=None)
    raw = ticket_of(monitoring.issue_ticket(watcher))
    response = Client().get(monitoring.CALLBACK_PATH, {"ticket": raw}, headers=MONITOR)
    assert response.cookies[monitoring.cookie_name()]["max-age"] == 2 * 3600
    (key,) = state_redis().scan_iter(f"{monitoring.SESSION_PREFIX}*")
    assert 3600 < ttl(key) <= 2 * 3600


# --- Landing paths -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "landed"),
    [
        ("/d/iptv-edges", "/d/iptv-edges"),
        ("/prometheus/alerts?search=Edge", "/prometheus/alerts?search=Edge"),
        ("", "/"),
        (None, "/"),
        ("https://evil.example/", "/"),
        ("//evil.example/", "/"),
        ("/\\evil.example", "/"),
        ("javascript:alert(1)", "/"),
        ("d/relative", "/"),
        ("/ok\r\nSet-Cookie: x=1", "/"),
        ("/_sso/logout", "/"),
        ("/" + "a" * 600, "/"),
    ],
)
def test_landing_paths_stay_on_the_monitoring_host(given: str | None, landed: str) -> None:
    assert monitoring.safe_next(given) == landed


def test_a_hostile_next_lands_on_the_home_page(watcher: User) -> None:
    raw = ticket_of(monitoring.issue_ticket(watcher, "https://evil.example/steal"))
    response = Client().get(monitoring.CALLBACK_PATH, {"ticket": raw}, headers=MONITOR)
    assert response["Location"] == "/"


def test_the_admin_sign_in_url_carries_a_safe_next() -> None:
    assert monitoring.admin_sign_in_url("/d/iptv-edges") == (
        f"{monitoring.admin_origin()}/monitoring?next=%2Fd%2Fiptv-edges"
    )
    assert monitoring.admin_sign_in_url("//evil") == f"{monitoring.admin_origin()}/monitoring"


# --- Traefik's forward-auth ----------------------------------------------------------------


def test_a_session_answers_with_the_identity_grafana_trusts(watcher: User) -> None:
    response = ask(sign_in(watcher))
    assert response.status_code == 200
    assert response["X-WEBAUTH-USER"] == "watcher"
    assert response["X-WEBAUTH-NAME"] == "watcher"
    assert response["X-WEBAUTH-ROLE"] == "Viewer"
    assert "no-cache" in response["Cache-Control"]


def test_owners_are_grafana_admins(owner: User) -> None:
    owner.name = "Owner Person"
    owner.save()
    response = ask(sign_in(owner))
    assert (response["X-WEBAUTH-ROLE"], response["X-WEBAUTH-NAME"]) == ("Admin", "Owner Person")


def test_a_name_that_is_not_latin_1_falls_back_to_the_login(watcher: User) -> None:
    watcher.name = "مراقب"
    watcher.save()
    assert ask(sign_in(watcher))["X-WEBAUTH-NAME"] == "watcher"


def test_without_a_session_a_page_goes_to_the_admin_and_an_api_call_gets_401() -> None:
    page = ask(Client(), "/d/iptv-edges", **NAVIGATION)
    assert page.status_code == 302
    assert page["Location"] == f"{monitoring.admin_origin()}/monitoring?next=%2Fd%2Fiptv-edges"
    assert ask(Client(), "/api/search", accept="application/json").status_code == 401
    post = ask(Client(), "/api/ds/query", **NAVIGATION, **{"x-forwarded-method": "POST"})
    assert post.status_code == 401


@pytest.mark.parametrize("cookie", ["", "made-up", "x" * 500])
def test_unknown_cookies_are_refused(cookie: str) -> None:
    browser = Client()
    browser.cookies[monitoring.cookie_name()] = cookie
    assert ask(browser, accept="application/json").status_code == 401


def test_a_suspended_admin_loses_access_within_a_minute(watcher: User) -> None:
    browser = sign_in(watcher)
    User.objects.filter(pk=watcher.pk).update(status=UserStatus.SUSPENDED)
    cookie = browser.cookies[monitoring.cookie_name()].value
    # Within the minute the cached answer stands; after it, the database decides.
    assert monitoring.authorize(cookie) is not None
    assert monitoring.authorize(cookie, now=time.time() + monitoring.RECHECK_S) is None
    # The session is gone for good, even for a request inside the next minute.
    assert monitoring.authorize(cookie) is None
    assert ask(browser, accept="application/json").status_code == 401


def test_a_removed_permission_ends_access_within_a_minute(watcher: User) -> None:
    browser = sign_in(watcher)
    cookie = browser.cookies[monitoring.cookie_name()].value
    role = Role.objects.get(name=f"custom_{watcher.username}")
    role.permissions.remove(Permission.objects.get(code="monitoring.view"))
    assert monitoring.authorize(cookie, now=time.time() + monitoring.RECHECK_S) is None
    assert ask(browser, accept="application/json").status_code == 401


def test_a_deactivated_admin_loses_access(watcher: User) -> None:
    browser = sign_in(watcher)
    cookie = browser.cookies[monitoring.cookie_name()].value
    User.objects.filter(pk=watcher.pk).update(is_active=False)
    assert monitoring.authorize(cookie, now=time.time() + monitoring.RECHECK_S) is None


def test_an_admin_turned_customer_loses_access(watcher: User) -> None:
    browser = sign_in(watcher)
    cookie = browser.cookies[monitoring.cookie_name()].value
    User.objects.filter(pk=watcher.pk).update(is_staff=False)
    assert monitoring.authorize(cookie, now=time.time() + monitoring.RECHECK_S) is None


def test_a_recheck_refreshes_the_role_and_keeps_the_session(make_admin: AdminFactory) -> None:
    admin = make_admin(permissions=["monitoring.view"], username="climber")
    browser = sign_in(admin)
    cookie = browser.cookies[monitoring.cookie_name()].value
    admin.roles.add(Role.objects.get(name="owner"))
    later = time.time() + monitoring.RECHECK_S
    identity = monitoring.authorize(cookie, now=later)
    assert identity is not None
    assert identity.role == "Admin"
    (key,) = state_redis().scan_iter(f"{monitoring.SESSION_PREFIX}*")
    assert ttl(key) > 7 * 3600


def test_the_answer_comes_from_redis_between_rechecks(
    watcher: User, django_assert_num_queries: Callable[..., Any]
) -> None:
    browser = sign_in(watcher)
    cookie = browser.cookies[monitoring.cookie_name()].value
    with django_assert_num_queries(0):
        assert monitoring.authorize(cookie) is not None


def test_redis_down_refuses_access(watcher: User) -> None:
    browser = sign_in(watcher)
    cookie = browser.cookies[monitoring.cookie_name()].value
    broken = mock.Mock(
        get=mock.Mock(side_effect=redis.ConnectionError),
        getdel=mock.Mock(side_effect=redis.ConnectionError),
        delete=mock.Mock(side_effect=redis.ConnectionError),
    )
    with mock.patch.object(monitoring, "state_redis", return_value=broken):
        assert monitoring.authorize(cookie) is None
        assert monitoring.redeem_ticket("anything") is None
        monitoring.end_session(cookie)  # logged, never raised


def test_a_corrupt_session_is_dropped(watcher: User) -> None:
    browser = sign_in(watcher)
    cookie = browser.cookies[monitoring.cookie_name()].value
    (key,) = state_redis().scan_iter(f"{monitoring.SESSION_PREFIX}*")
    state_redis().set(key, "{not json")
    assert monitoring.authorize(cookie) is None
    assert not state_redis().exists(key)


def test_sign_out_ends_the_session(watcher: User) -> None:
    browser = sign_in(watcher)
    response = browser.get("/_sso/logout", headers=MONITOR)
    assert response.status_code == 302
    assert response["Location"] == f"{monitoring.admin_origin()}/"
    assert response.cookies[monitoring.cookie_name()]["max-age"] == 0
    assert list(state_redis().scan_iter(f"{monitoring.SESSION_PREFIX}*")) == []
    assert ask(browser, accept="application/json").status_code == 401


# --- Hosts ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host",
    [
        settings.API_HOST,
        settings.TV_HOST,
        settings.ADMIN_HOST,
        settings.APP_HOST,
        settings.GRAFANA_HOST,
    ],
)
def test_the_forward_auth_is_internal_only(host: str) -> None:
    response = Client().get("/internal/monitoring-auth", headers={"host": host})
    assert response.status_code == 404


@pytest.mark.parametrize("host", [settings.API_HOST, settings.ADMIN_HOST, "web"])
def test_the_exchange_lives_on_the_monitoring_host_only(host: str) -> None:
    assert Client().get(monitoring.CALLBACK_PATH, headers={"host": host}).status_code == 404


def test_the_monitoring_host_serves_nothing_else() -> None:
    assert Client().get("/api/v1/health", headers=MONITOR).status_code == 404
    assert Client().get("/metrics", headers=MONITOR).status_code == 404


def test_https_uses_a_host_only_secure_cookie(settings: Any, watcher: User) -> None:
    settings.SESSION_COOKIE_SECURE = True
    assert monitoring.cookie_name() == "__Host-iptv_monitor"
    raw = ticket_of(monitoring.issue_ticket(watcher))
    response = Client().get(monitoring.CALLBACK_PATH, {"ticket": raw}, headers=MONITOR, secure=True)
    cookie = response.cookies["__Host-iptv_monitor"]
    assert cookie["secure"]
    assert cookie["path"] == "/"
    assert not cookie["domain"]
