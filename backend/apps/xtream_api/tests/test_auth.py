"""Xtream authentication: equal work for every refusal, statuses and activity (SPEC §7.5, §11)."""

from datetime import timedelta
from typing import Any, cast

import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts import credentials
from apps.accounts.models import Device, XtreamCredential
from apps.core.stores import state_redis
from apps.playback import entitlements
from apps.playback.entitlements import Entitlement
from apps.xtream_api import auth
from apps.xtream_api.source import CatalogScope
from apps.xtream_api.tests.conftest import Subscriber

pytestmark = pytest.mark.django_db
API = "/player_api.php"


@pytest.fixture
def verifications(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every Argon2id verification, real or spent on an unknown user."""
    calls: list[str] = []
    real = credentials.verify_password

    def counting(password_hash: str, password: str) -> bool:
        calls.append(password)
        return real(password_hash, password)

    monkeypatch.setattr(credentials, "verify_password", counting)
    return calls


def test_unknown_users_and_wrong_passwords_cost_the_same(
    tv: Client, subscriber: Subscriber, verifications: list[str]
) -> None:
    unknown = tv.get(API, {"username": "nobody-k3p9qa", "password": "wrong-password-1"})
    assert len(verifications) == 1
    wrong = tv.get(API, {"username": subscriber.username, "password": "wrong-password-1"})
    assert len(verifications) == 2
    assert unknown.status_code == wrong.status_code == 200
    assert unknown.content == wrong.content
    assert set(unknown.headers) == set(wrong.headers)


def test_a_success_is_cached_and_never_stores_the_password(
    tv: Client, subscriber: Subscriber, verifications: list[str]
) -> None:
    for _ in range(3):
        tv.get(API, subscriber.params())
    assert len(verifications) == 1
    keys = [key.decode() for key in state_redis().scan_iter("*")]
    assert keys, "the success and the entitlement are cached"
    for key in keys:
        assert subscriber.password not in key
        assert subscriber.username not in key
        dumped = cast("bytes | None", state_redis().dump(key)) or b""
        assert subscriber.password.encode() not in dumped


def test_activity_is_recorded_at_most_once_a_minute(tv: Client, subscriber: Subscriber) -> None:
    tv.get(API, subscriber.params(), REMOTE_ADDR="203.0.113.7")
    device = Device.objects.get(pk=subscriber.device.pk)
    credential = XtreamCredential.objects.get(device=device)
    assert device.last_ip == "203.0.113.7"
    assert device.first_seen == device.last_seen
    assert device.last_seen is not None
    assert credential.last_used_at is not None
    first_seen = device.last_seen

    tv.get(API, subscriber.params(), REMOTE_ADDR="203.0.113.8")
    device.refresh_from_db()
    assert (device.last_seen, device.last_ip) == (first_seen, "203.0.113.7")

    state_redis().delete(f"xc:seen:{device.pk}")  # a minute later
    tv.get(API, subscriber.params(), REMOTE_ADDR="203.0.113.8")
    device.refresh_from_db()
    assert device.last_ip == "203.0.113.8"
    assert device.last_seen is not None
    assert device.last_seen > first_seen
    assert device.first_seen == first_seen


def entitlement(**changes: Any) -> Entitlement:
    base: dict[str, Any] = {
        "v": 1,
        "user_id": "u",
        "source": "access_profile",
        "status": "active",
        "ends_at": None,
        "grace_until": None,
        "max_streams": 1,
        "max_devices": 1,
        "max_quality": 1080,
        "policy": "reject",
        "categories": None,
        "allow_movies": True,
        "allow_series": True,
        "allow_live": True,
        "allow_download": False,
        "country_rules": [],
        "ip_rules": [],
    }
    base.update(changes)
    return cast("Entitlement", base)


@pytest.mark.parametrize(
    ("changes", "device", "expected"),
    [
        ({}, {}, "Active"),
        ({"status": "expired"}, {}, "Expired"),
        ({"status": "suspended"}, {}, "Disabled"),
        ({"status": "disabled"}, {}, "Disabled"),
        ({}, {"blocked": True}, "Disabled"),
        ({}, {"approved": False}, "Disabled"),
        ({"ends_at": "2020-01-01T00:00:00+00:00"}, {}, "Expired"),  # cached past its end
        # In grace: playback still allows it, so the app shows Active.
        (
            {"ends_at": "2020-01-01T00:00:00+00:00", "grace_until": "2999-01-01T00:00:00+00:00"},
            {},
            "Active",
        ),
        (
            {"ends_at": "2020-01-01T00:00:00+00:00", "grace_until": "2020-01-04T00:00:00+00:00"},
            {},
            "Expired",
        ),
    ],
)
def test_status(changes: dict[str, Any], device: dict[str, Any], expected: str) -> None:
    assert auth.status_of(entitlement(**changes), Device(**device)) == expected


def test_scope_follows_the_entitlement(subscriber: Subscriber) -> None:
    ends = timezone.now() + timedelta(days=30)
    account = auth.account(
        subscriber.user,
        subscriber.device,
        entitlement(
            categories=["b", "a"], allow_series=False, ends_at=ends.isoformat(), max_streams=3
        ),
    )
    assert account.scope.categories == frozenset({"a", "b"})
    assert (account.scope.allow_movies, account.scope.allow_series) == (True, False)
    assert (account.expires_at, account.max_connections, account.locale) == (ends, 3, "en")


def test_users_without_access_profile_are_disabled_and_see_nothing(
    subscriber: Subscriber,
) -> None:
    account = auth.account(subscriber.user, subscriber.device, None)
    assert account.status == "Disabled"
    assert not account.active
    assert account.scope.categories == frozenset()


def test_the_scope_fingerprint_ignores_category_order() -> None:
    first = CatalogScope(categories=frozenset({"a", "b"}))
    assert first.fingerprint() == CatalogScope(categories=frozenset({"b", "a"})).fingerprint()
    assert first.fingerprint() != CatalogScope().fingerprint()
    assert (
        first.fingerprint() != CatalogScope(frozenset({"a", "b"}), allow_live=False).fingerprint()
    )


def test_entitlement_is_read_from_the_cache(subscriber: Subscriber, tv: Client) -> None:
    entitlements.refresh(subscriber.user.pk)
    key = entitlements.entitlement_key(subscriber.user.pk)
    assert state_redis().exists(key)
    response = tv.get(API, subscriber.params())
    assert response.json()["user_info"]["status"] == "Active"
