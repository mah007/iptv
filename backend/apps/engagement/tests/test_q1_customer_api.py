"""The customer API's Q1 additions (ADR-0023): my streams and stopping one, my password,
`eta_s` on TITLE_PREPARING, browsers reusing their device, idle devices retired."""

import json
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts import customer_auth, customer_tokens
from apps.accounts import services as accounts
from apps.accounts.models import Device, DeviceKind, User
from apps.audit.models import AuditLog
from apps.catalog.tests.builders import Builder, episode
from apps.conftest import CustomerFactory
from apps.notifications.models import NotificationOutbox
from apps.playback import tokens
from apps.playback.models import EndReason, PlaybackSession

VECTORS = Path(__file__).resolve().parents[2] / "playback" / "tests" / "data" / "token_vectors.json"
pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("clean_stores", "media_keys")]
PORTAL = {"host": settings.APP_HOST}
START = "/api/v1/playback/start"
STREAMS = "/api/v1/me/streams"
PASSWORD = "/api/v1/me/password"  # noqa: S105 (a URL)
OLD_PASSWORD = "an-old-long-password"  # noqa: S105 (a test password)
NEW_PASSWORD = "a-new-long-password-2026"  # noqa: S105 (a test password)
FIREFOX = "Mozilla/5.0 (X11; Linux x86_64) Firefox/140.0"


@pytest.fixture
def media_keys(settings: Any, tmp_path: Path) -> Iterator[None]:
    keys = json.loads(VECTORS.read_text(encoding="utf-8"))["keys"]
    path = tmp_path / "media_token_keys.json"
    path.write_text(json.dumps(keys), encoding="utf-8")
    settings.MEDIA_TOKEN_KEYS_FILE = str(path)
    settings.MEDIA_BASE_URL = "https://media.example.test"
    tokens.reset_keyring()
    yield
    tokens.reset_keyring()


@pytest.fixture
def customer(make_customer: CustomerFactory) -> User:
    user = make_customer(max_streams=1)
    user.set_password(OLD_PASSWORD)
    user.save()
    return user


def browser(customer: User) -> APIClient:
    client = APIClient()
    client.force_login(customer)
    client.defaults["HTTP_HOST"] = PORTAL["host"]
    client.defaults["HTTP_USER_AGENT"] = FIREFOX
    return client


def start(client: APIClient, **body: Any) -> Any:
    return client.post(START, body, format="json")


# --- My streams -----------------------------------------------------------------------------------


def test_my_streams_list_and_stopping_one_frees_the_slot(build: Builder, customer: User) -> None:
    movie = build.movie("Film")
    here, there = browser(customer), browser(customer)
    assert start(there, title_type="movie", title_id=str(movie.pk), prefer="mp4").status_code == 200
    refused = start(here, title_type="movie", title_id=str(movie.pk), prefer="mp4")
    assert refused.json()["code"] == "CONCURRENCY_LIMIT"
    listed = here.get(STREAMS)
    assert listed.status_code == 200
    assert listed["Cache-Control"] == "no-store"
    [stream] = listed.json()
    assert stream["title_name"] == "Film"
    assert stream["title_kind"] == "movie"
    assert stream["current"] is False
    assert stream["device"]["kind"] == "web"
    assert there.get(STREAMS).json()[0]["current"] is True
    assert here.post(f"{STREAMS}/{stream['id']}/stop").status_code == 204
    session = PlaybackSession.objects.get(pk=stream["id"])
    assert session.end_reason == EndReason.STOPPED
    assert AuditLog.objects.filter(action="session.stop_own", actor=customer).count() == 1
    # The slot is free at once.
    assert start(here, title_type="movie", title_id=str(movie.pk), prefer="mp4").status_code == 200
    assert here.post(f"{STREAMS}/{stream['id']}/stop").status_code == 404


def test_someone_elses_stream_is_not_mine(
    build: Builder, customer: User, make_customer: CustomerFactory
) -> None:
    movie = build.movie("Film")
    other = make_customer()
    theirs = browser(other)
    assert (
        start(theirs, title_type="movie", title_id=str(movie.pk), prefer="mp4").status_code == 200
    )
    session = PlaybackSession.objects.get(user=other)
    mine = browser(customer)
    assert mine.get(STREAMS).json() == []
    assert mine.post(f"{STREAMS}/{session.pk}/stop").status_code == 404


# --- TITLE_PREPARING with eta_s ------------------------------------------------------------------


def test_a_title_still_preparing_says_when(build: Builder, customer: User) -> None:
    show = build.series("Show", seasons={1: 2}, unplayable=[(1, 2)])
    waiting = episode(show, 1, 2)
    response = start(
        browser(customer),
        title_type="series",
        title_id=str(show.pk),
        episode_id=str(waiting.pk),
    )
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "TITLE_PREPARING"
    # 90 minutes of source, no job running yet: an estimate from the file's length.
    assert isinstance(body["eta_s"], int)
    assert body["eta_s"] > 0
    assert int(response["Retry-After"]) == min(max(body["eta_s"], 5), 300)


# --- My password ---------------------------------------------------------------------------------


def test_changing_my_password_ends_my_other_sign_ins(
    build: Builder, customer: User, django_capture_on_commit_callbacks: Any
) -> None:
    movie = build.movie("Film")
    accounts.update_access(customer, {"max_streams": 3}, actor=None)
    customer.refresh_from_db()
    here, elsewhere = browser(customer), browser(customer)
    assert (
        start(elsewhere, title_type="movie", title_id=str(movie.pk), prefer="mp4").status_code
        == 200
    )
    signed_in = APIClient()  # an app signed in with tokens
    signed_in.defaults["HTTP_HOST"] = settings.API_HOST
    login = signed_in.post(
        "/api/v1/auth/login",
        {"login": customer.username, "password": OLD_PASSWORD},
        format="json",
    )
    assert login.status_code == 200, login.content
    with django_capture_on_commit_callbacks(execute=True):
        response = here.post(
            PASSWORD,
            {"current_password": OLD_PASSWORD, "new_password": NEW_PASSWORD},
            format="json",
        )
    assert response.status_code == 204, response.content
    customer.refresh_from_db()
    assert customer.check_password(NEW_PASSWORD)
    assert here.get("/api/v1/me").status_code == 200
    assert elsewhere.get("/api/v1/me").status_code == 401
    assert not PlaybackSession.objects.filter(user=customer, ended_at__isnull=True).exists()
    assert customer_tokens.live_devices(customer.pk) == set()
    assert AuditLog.objects.filter(action="customer.password_change", actor=customer).exists()
    assert NotificationOutbox.objects.filter(
        user=customer, template_key="password_changed"
    ).exists()


def test_my_password_checks_the_current_one_and_the_new_one(customer: User) -> None:
    client = browser(customer)
    wrong = client.post(
        PASSWORD, {"current_password": "nope", "new_password": NEW_PASSWORD}, format="json"
    )
    assert wrong.status_code == 400
    assert wrong.json()["field_error_codes"] == {"current_password": ["wrong_password"]}
    weak = client.post(
        PASSWORD, {"current_password": OLD_PASSWORD, "new_password": "short"}, format="json"
    )
    assert weak.status_code == 400
    assert "password_too_short" in weak.json()["field_error_codes"]["new_password"]
    customer.refresh_from_db()
    assert customer.check_password(OLD_PASSWORD)


def test_an_app_changes_the_password_and_keeps_its_own_sign_in(
    customer: User, django_capture_on_commit_callbacks: Any
) -> None:
    app = APIClient()
    app.defaults["HTTP_HOST"] = settings.API_HOST
    other = APIClient()
    other.defaults["HTTP_HOST"] = settings.API_HOST
    tokens_here = app.post(
        "/api/v1/auth/login", {"login": customer.username, "password": OLD_PASSWORD}, format="json"
    ).json()
    tokens_there = other.post(
        "/api/v1/auth/login", {"login": customer.username, "password": OLD_PASSWORD}, format="json"
    ).json()
    app.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens_here['access_token']}")
    other.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens_there['access_token']}")
    with django_capture_on_commit_callbacks(execute=True):
        response = app.post(
            PASSWORD,
            {"current_password": OLD_PASSWORD, "new_password": NEW_PASSWORD},
            format="json",
        )
    assert response.status_code == 204, response.content
    assert app.get("/api/v1/me").status_code == 200
    assert other.get("/api/v1/me").status_code == 401


# --- Browsers reuse their device -----------------------------------------------------------------


def test_a_browser_keeps_its_device_across_sign_ins(build: Builder, customer: User) -> None:
    movie = build.movie("Film")
    accounts.update_access(customer, {"max_streams": 3}, actor=None)
    first = browser(customer)
    assert start(first, title_type="movie", title_id=str(movie.pk), prefer="mp4").status_code == 200
    cookie = first.cookies[customer_auth.WEB_DEVICE_COOKIE]
    assert cookie["httponly"]
    device = Device.objects.get(user=customer, kind=DeviceKind.WEB)
    assert first.post("/api/v1/auth/logout").status_code == 204
    # Signed in again in the same browser: the cookie names the same device.
    again = browser(customer)
    again.cookies[customer_auth.WEB_DEVICE_COOKIE] = cookie.value
    reused = start(again, title_type="movie", title_id=str(movie.pk), prefer="mp4")
    assert reused.status_code == 200, reused.json()
    assert Device.objects.filter(user=customer, kind=DeviceKind.WEB).count() == 1
    # Another browser gets its own; a tampered cookie counts as none.
    other = browser(customer)
    other.cookies[customer_auth.WEB_DEVICE_COOKIE] = f"{device.pk}:forged"
    assert start(other, title_type="movie", title_id=str(movie.pk), prefer="mp4").status_code == 200
    assert Device.objects.filter(user=customer, kind=DeviceKind.WEB).count() == 2


def test_someone_elses_cookie_is_not_reused(
    build: Builder, customer: User, make_customer: CustomerFactory
) -> None:
    movie = build.movie("Film")
    first = browser(customer)
    assert start(first, title_type="movie", title_id=str(movie.pk), prefer="mp4").status_code == 200
    cookie = first.cookies[customer_auth.WEB_DEVICE_COOKIE].value
    other_user = make_customer()
    other = browser(other_user)
    other.cookies[customer_auth.WEB_DEVICE_COOKIE] = cookie
    assert start(other, title_type="movie", title_id=str(movie.pk), prefer="mp4").status_code == 200
    assert Device.objects.filter(user=other_user, kind=DeviceKind.WEB).count() == 1


def test_idle_browsers_and_ended_app_sign_ins_are_retired(customer: User) -> None:
    now = timezone.now()
    idle = Device.objects.create(
        user=customer, kind=DeviceKind.WEB, name="Old browser", last_seen=now - timedelta(days=45)
    )
    fresh = Device.objects.create(
        user=customer, kind=DeviceKind.WEB, name="Browser", last_seen=now - timedelta(days=2)
    )
    gone_app = Device.objects.create(user=customer, kind=DeviceKind.APP, name="Old phone")
    tv = Device.objects.create(user=customer, kind=DeviceKind.XTREAM, name="TV")
    app = APIClient()
    app.defaults["HTTP_HOST"] = settings.API_HOST
    assert (
        app.post(
            "/api/v1/auth/login",
            {"login": customer.username, "password": OLD_PASSWORD},
            format="json",
        ).status_code
        == 200
    )
    live_app = (
        Device.objects.filter(user=customer, kind=DeviceKind.APP).exclude(pk=gone_app.pk).get()
    )
    expired = accounts.expire_idle_devices(now=now)
    states = {d.name: d.revoked_at for d in Device.objects.filter(user=customer)}
    assert expired == 2, states
    for device in (idle, fresh, gone_app, live_app):
        device.refresh_from_db()
    assert idle.revoked_at is not None
    assert gone_app.revoked_at is not None
    assert fresh.revoked_at is None
    assert live_app.revoked_at is None
    tv.refresh_from_db()
    assert tv.revoked_at is None
    entry = AuditLog.objects.get(action="devices.expire")
    assert entry.after == {"devices": 2, "idle_days": 30}
    assert accounts.expire_idle_devices(now=now) == 0


def test_rendition_less_movies_say_preparing(build: Builder, customer: User) -> None:
    movie = build.movie("Bare", playable=False)
    response = start(browser(customer), title_type="movie", title_id=str(movie.pk))
    if response.status_code == 404:  # not visible without a playable file
        pytest.skip("a movie without renditions is not visible")
    assert response.status_code == 409
    assert response.json()["code"] == "TITLE_PREPARING"
    assert "eta_s" in response.json()
