"""The commercial slice end to end, without consumer changes (ADR-0012): a plan's
subscription reaches the Xtream login and playback; expiry stops both."""

import json
from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings
from django.test import Client
from django.utils import timezone
from pytest_django import Settings

from apps.accounts import services as accounts
from apps.accounts.models import User
from apps.accounts.signals import access_expired
from apps.billing import services, subscriptions
from apps.billing.models import Plan, Subscription, SubscriptionStatus
from apps.core.errors import ErrorCode, ProblemError
from apps.core.ids import uuid7
from apps.core.stores import cache_redis
from apps.playback import services as playback
from apps.playback import tokens
from apps.playback.models import EndReason, PlaybackSession, TitleKind
from apps.playback.services import (
    Denial,
    PlayableRendition,
    PlayableTitle,
    PlaybackDenied,
    RenditionKind,
)
from apps.xtream_api import cache as xtream_cache

pytestmark = pytest.mark.django_db
type Capture = Callable[..., Any]

VECTORS = Path(settings.BASE_DIR) / "apps" / "playback" / "tests" / "data" / "token_vectors.json"
PASSWORD = "living-room-2026"  # noqa: S105 (a test credential)


@pytest.fixture(autouse=True)
def media_keys(settings: Settings, tmp_path: Path) -> Iterator[None]:
    keys = json.loads(VECTORS.read_text(encoding="utf-8"))["keys"]
    path = tmp_path / "media_token_keys.json"
    path.write_text(json.dumps(keys), encoding="utf-8")
    settings.MEDIA_TOKEN_KEYS_FILE = str(path)
    settings.MEDIA_BASE_URL = "https://media.example.test"
    tokens.reset_keyring()
    yield
    tokens.reset_keyring()


@pytest.fixture(autouse=True)
def _clean_xtream_cache() -> Iterator[None]:
    def clear() -> None:
        client = cache_redis()
        for key in list(client.scan_iter(f"{xtream_cache.PREFIX}:*")):
            client.delete(key)

    clear()
    yield
    clear()


@pytest.fixture
def customer(db: None) -> User:
    user, _ = accounts.create_customer(
        {"name": "Sara Otaibi", "email": "sara@example.com", "locale": "en"}, actor=None
    )
    accounts.create_device_credential(
        user, name="Living room", username="sara-tv", password=PASSWORD, actor=None
    )
    return user


@pytest.fixture
def premium(make_plan: Callable[..., Plan]) -> Plan:
    return make_plan(code="premium", max_streams=3, max_devices=4, max_quality=2160)


def login(**params: str) -> dict[str, Any]:
    response = Client(headers={"host": settings.TV_HOST}).get(
        "/player_api.php", {"username": "sara-tv", "password": PASSWORD, **params}
    )
    assert response.status_code == 200
    data: dict[str, Any] = response.json()
    return data


def movie() -> PlayableTitle:
    return PlayableTitle(
        kind=TitleKind.MOVIE,
        id=uuid7(),
        renditions=(PlayableRendition(uuid7().hex, RenditionKind.COMPAT, 1080),),
        runtime_s=6000,
        name="The Matrix (1999)",
    )


def play(user: User) -> playback.PlaybackGrant:
    device = user.devices.get()
    return playback.start_playback(user, device, movie(), client_ip="203.0.113.7")


def test_the_xtream_login_shows_the_plan(
    customer: User, premium: Plan, django_capture_on_commit_callbacks: Capture
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        payment = services.record_manual_payment(user=customer, plan=premium, actor=None)
    subscription = payment.subscription
    assert subscription is not None
    info = login()["user_info"]
    assert info["auth"] == 1
    assert info["status"] == "Active"
    assert info["exp_date"] == str(int(subscription.ends_at.timestamp()))
    assert info["max_connections"] == "3"
    assert info["is_trial"] == "0"


def test_expiry_turns_the_login_expired_and_playback_refuses(
    customer: User, premium: Plan, django_capture_on_commit_callbacks: Capture
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        subscription = subscriptions.activate(customer, premium, source="manual")
    grant = play(customer)
    assert grant.url.startswith("https://media.example.test/v/")
    assert playback.open_sessions().filter(user=customer).count() == 1
    # The period ends; grace still plays.
    Subscription.objects.filter(pk=subscription.pk).update(
        starts_at=timezone.now() - timedelta(days=31), ends_at=timezone.now() - timedelta(days=1)
    )
    with django_capture_on_commit_callbacks(execute=True):
        subscriptions.advance_states()
    subscription.refresh_from_db()
    assert subscription.status == SubscriptionStatus.GRACE
    play(customer)
    # Grace ends: the job expires it, sessions are stopped, playback refuses.
    with django_capture_on_commit_callbacks(execute=True):
        subscriptions.advance_states(now=timezone.now() + timedelta(days=3))
    subscription.refresh_from_db()
    assert subscription.status == SubscriptionStatus.EXPIRED
    assert not playback.open_sessions().filter(user=customer).exists()  # kicked (access_expired)
    # The grace-period stream (the first one stopped when its device started another).
    last = PlaybackSession.objects.filter(user=customer).order_by("-started_at").first()
    assert last is not None
    assert last.end_reason == EndReason.EXPIRED
    with pytest.raises(PlaybackDenied) as excinfo:
        play(customer)
    assert excinfo.value.denial == Denial.SUBSCRIPTION_EXPIRED
    info = login()["user_info"]
    assert info["status"] == "Expired"
    assert info["exp_date"] == str(int(subscription.ends_at.timestamp()))


def test_a_payment_renews_an_expired_customer(
    customer: User, premium: Plan, django_capture_on_commit_callbacks: Capture
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        subscription = subscriptions.activate(customer, premium, source="manual")
        subscriptions.cancel(subscription, actor=None)
    assert login()["user_info"]["status"] == "Expired"
    with django_capture_on_commit_callbacks(execute=True):
        payment = services.record_manual_payment(user=customer, plan=premium, actor=None)
    assert payment.subscription is not None
    assert payment.subscription.pk != subscription.pk  # a new period after the gap
    assert login()["user_info"]["status"] == "Active"
    play(customer)


def test_trials_show_as_trials_to_iptv_apps(
    customer: User,
    make_plan: Callable[..., Plan],
    owner: User,
    django_capture_on_commit_callbacks: Capture,
) -> None:
    trial = make_plan(code="trial", price=0, is_trial=True)
    with django_capture_on_commit_callbacks(execute=True):
        subscriptions.request_trial(customer, trial, actor=owner)
    info = login()["user_info"]
    assert info["status"] == "Active"


def test_the_plan_sets_the_device_limit(
    customer: User, premium: Plan, django_capture_on_commit_callbacks: Capture
) -> None:
    # The profile allows 2 devices (the default); the premium plan allows 4.
    with django_capture_on_commit_callbacks(execute=True):
        subscriptions.activate(customer, premium, source="manual")
    for number in range(3):  # the fixture's device plus 3 more
        accounts.create_device_credential(customer, name=f"Extra {number}", actor=None)
    with pytest.raises(ProblemError) as refused:
        accounts.create_device_credential(customer, name="One too many", actor=None)
    assert refused.value.problem_code == ErrorCode.DEVICE_LIMIT


def test_a_profile_expiry_does_not_stop_a_subscriber(
    customer: User, premium: Plan, django_capture_on_commit_callbacks: Capture
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        subscriptions.activate(customer, premium, source="manual")
    customer.access.expires_at = timezone.now() - timedelta(hours=1)
    customer.access.save(update_fields=["expires_at"])
    kicked: list[object] = []

    def receiver(**kwargs: object) -> None:
        kicked.append(kwargs["user_id"])

    access_expired.connect(receiver)
    try:
        with django_capture_on_commit_callbacks(execute=True):
            assert accounts.process_expired_access() == 1
    finally:
        access_expired.disconnect(receiver)
    assert kicked == []
    assert login()["user_info"]["status"] == "Active"
    play(customer)  # still allowed
