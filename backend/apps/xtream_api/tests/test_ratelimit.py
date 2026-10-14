"""Failed sign-in limits per IP and per username (SPEC §7.5, §11): past the limit the
answer is the usual failure, without verifying the password."""

from typing import Any, cast

import pytest
import redis
from django.test import Client

from apps.accounts import services as account_services
from apps.core.services import set_setting
from apps.core.stores import state_redis
from apps.xtream_api import ratelimit
from apps.xtream_api.tests.conftest import Subscriber

pytestmark = pytest.mark.django_db
API = "/player_api.php"
AUTH_FAILURE = b'{"user_info":{"auth":0}}'


@pytest.fixture(autouse=True)
def small_limits(db: None) -> None:
    set_setting("xtream.auth_failures_per_username", 3, actor=None)
    set_setting("xtream.auth_failures_per_ip", 5, actor=None)
    set_setting("xtream.auth_failure_window_s", 600, actor=None)


@pytest.fixture
def verifications(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Usernames whose password was actually verified (Argon2), not just looked up in
    the success cache."""
    seen: list[str] = []
    real = account_services.authenticate_xtream

    def counting(username: str, password: str, *, cached_only: bool = False) -> Any:
        if not cached_only:
            seen.append(username)
        return real(username, password, cached_only=cached_only)

    monkeypatch.setattr(account_services, "authenticate_xtream", counting)
    return seen


def _login(tv: Client, username: str, password: str, ip: str = "198.51.100.1") -> bytes:
    response = tv.get(API, {"username": username, "password": password}, HTTP_X_FORWARDED_FOR=ip)
    assert response.status_code == 200
    return bytes(response.content)


def _clear() -> None:
    for pattern in ("xc:fail:*", "xc:block:*", "xc:strike:*"):
        for key in state_redis().scan_iter(pattern):
            state_redis().delete(key)


def test_username_limit_refuses_without_verifying(
    tv: Client, subscriber: Subscriber, verifications: list[str]
) -> None:
    for attempt in range(3):
        ip = f"198.51.100.{attempt + 10}"  # one /24: the username's count for that network
        assert _login(tv, subscriber.username, "wrong-password", ip) == AUTH_FAILURE
    assert len(verifications) == 3
    # The right password from that network now gets the same answer, unchecked.
    assert _login(tv, subscriber.username, subscriber.password) == AUTH_FAILURE
    play = tv.get(subscriber.path("movie", 1001), HTTP_X_FORWARDED_FOR="198.51.100.1")
    assert play.status_code == 404
    playlist = tv.get(
        "/get.php", subscriber.params(type="m3u_plus"), HTTP_X_FORWARDED_FOR="198.51.100.1"
    )
    assert playlist.status_code == 403
    assert len(verifications) == 3
    # Counted case-insensitively, as usernames are unique regardless of case.
    assert _login(tv, subscriber.username.upper(), "x" * 10) == AUTH_FAILURE
    assert len(verifications) == 3
    # A guesser elsewhere doesn't lock the customer out of their own network (G-20).
    assert b'"auth":1' in _login(tv, subscriber.username, subscriber.password, "192.0.2.7")
    # The window ends: sign-ins work again.
    for key in state_redis().scan_iter("xc:fail:*"):
        assert 0 < cast("int", state_redis().ttl(key)) <= 600
    _clear()
    assert b'"auth":1' in _login(tv, subscriber.username, subscriber.password)


def test_a_spread_guess_hits_the_all_networks_limit(
    tv: Client, subscriber: Subscriber, verifications: list[str]
) -> None:
    for attempt in range(3 * ratelimit.USERNAME_SPREAD):
        ip = f"10.{attempt}.0.1"  # a different /24 each time
        assert _login(tv, subscriber.username, "wrong-password", ip) == AUTH_FAILURE
    assert len(verifications) == 3 * ratelimit.USERNAME_SPREAD
    assert _login(tv, subscriber.username, subscriber.password, "192.0.2.8") == AUTH_FAILURE
    assert len(verifications) == 3 * ratelimit.USERNAME_SPREAD


def test_ip_limit_covers_every_username_and_grows(
    tv: Client, subscriber: Subscriber, verifications: list[str]
) -> None:
    for number in range(5):
        assert _login(tv, f"guess{number}", "wrong-password") == AUTH_FAILURE
    assert _login(tv, subscriber.username, subscriber.password) == AUTH_FAILURE
    assert len(verifications) == 5
    first = cast("int", state_redis().ttl("xc:block:ip:198.51.100.1"))
    assert 0 < first <= 600
    # Another address is not affected.
    assert b'"auth":1' in _login(tv, subscriber.username, subscriber.password, "198.51.100.2")
    # The same address reaching the limit again the same day pauses twice as long.
    for key in ("xc:fail:ip:198.51.100.1", "xc:block:ip:198.51.100.1"):
        state_redis().delete(key)
    for number in range(5):
        _login(tv, f"again{number}", "wrong-password")
    second = cast("int", state_redis().ttl("xc:block:ip:198.51.100.1"))
    assert 600 < second <= 1200


def test_a_recent_success_still_signs_in_while_paused(
    tv: Client, subscriber: Subscriber, verifications: list[str]
) -> None:
    assert b'"auth":1' in _login(tv, subscriber.username, subscriber.password)
    for number in range(5):
        _login(tv, f"noise{number}", "wrong-password")  # a busy NAT pauses the address
    before = len(verifications)
    # The household's app, signed in minutes ago, keeps working (from the cache)...
    assert b'"auth":1' in _login(tv, subscriber.username, subscriber.password)
    assert len(verifications) == before
    # ...but nothing new is verified from that address.
    assert _login(tv, subscriber.username, "wrong-password") == AUTH_FAILURE
    assert len(verifications) == before


def test_success_clears_the_username_count(tv: Client, subscriber: Subscriber) -> None:
    for _ in range(2):
        _login(tv, subscriber.username, "wrong-password")
    assert b'"auth":1' in _login(tv, subscriber.username, subscriber.password)
    for _ in range(2):
        _login(tv, subscriber.username, "wrong-password", "198.51.100.3")
    assert b'"auth":1' in _login(tv, subscriber.username, subscriber.password, "198.51.100.4")


def test_redis_outage_does_not_lock_anyone_out(
    tv: Client, subscriber: Subscriber, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Down:
        def __getattr__(self, name: str) -> Any:
            def fail(*args: Any, **kwargs: Any) -> Any:
                raise redis.ConnectionError

            return fail

    monkeypatch.setattr(ratelimit, "state_redis", Down)
    assert ratelimit.failures("someone", "198.51.100.1") == ratelimit.Failures()
    ratelimit.record_failure("someone", "198.51.100.1")
    ratelimit.clear_username("someone")
    assert b'"auth":1' in _login(tv, subscriber.username, subscriber.password)


def test_a_sign_in_is_seen_by_the_anti_sharing_score(tv: Client, subscriber: Subscriber) -> None:
    from apps.antisharing.models import Sighting, SightingSource  # noqa: PLC0415

    assert b'"auth":1' in _login(tv, subscriber.username, subscriber.password, "81.2.69.142")
    sighting = Sighting.objects.get(source=SightingSource.XTREAM)
    assert sighting.network == "81.2.69.0/24"
