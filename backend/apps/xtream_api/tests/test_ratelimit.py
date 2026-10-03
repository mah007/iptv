"""Failed sign-in limits per IP and per username (SPEC §7.5, §11): past the limit the
answer is the usual failure, without verifying the password."""

from typing import Any, cast

import pytest
import redis
from django.test import Client

from apps.accounts import services as account_services
from apps.core.stores import state_redis
from apps.xtream_api import ratelimit
from apps.xtream_api.tests.conftest import Subscriber

pytestmark = pytest.mark.django_db
API = "/player_api.php"
AUTH_FAILURE = b'{"user_info":{"auth":0}}'


@pytest.fixture(autouse=True)
def small_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(ratelimit.DEFAULTS, "xtream.auth_failures_per_username", 3)
    monkeypatch.setitem(ratelimit.DEFAULTS, "xtream.auth_failures_per_ip", 5)
    monkeypatch.setitem(ratelimit.DEFAULTS, "xtream.auth_failure_window_s", 600)


@pytest.fixture
def verifications(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Usernames whose password was actually verified."""
    seen: list[str] = []
    real = account_services.authenticate_xtream

    def counting(username: str, password: str) -> Any:
        seen.append(username)
        return real(username, password)

    monkeypatch.setattr(account_services, "authenticate_xtream", counting)
    return seen


def _login(tv: Client, username: str, password: str, ip: str = "198.51.100.1") -> bytes:
    response = tv.get(API, {"username": username, "password": password}, HTTP_X_FORWARDED_FOR=ip)
    assert response.status_code == 200
    return bytes(response.content)


def test_username_limit_refuses_without_verifying(
    tv: Client, subscriber: Subscriber, verifications: list[str]
) -> None:
    for attempt in range(3):
        ip = f"198.51.100.{attempt + 10}"  # spread over IPs: the username limit applies
        assert _login(tv, subscriber.username, "wrong-password", ip) == AUTH_FAILURE
    assert len(verifications) == 3
    # The right password now gets the same answer, and is not even checked.
    assert _login(tv, subscriber.username, subscriber.password) == AUTH_FAILURE
    play = tv.get(subscriber.path("movie", 1001))
    assert play.status_code == 404
    playlist = tv.get("/get.php", subscriber.params(type="m3u_plus"))
    assert playlist.status_code == 403
    assert len(verifications) == 3
    # Counted case-insensitively, as usernames are unique regardless of case.
    assert _login(tv, subscriber.username.upper(), "x" * 10) == AUTH_FAILURE
    assert len(verifications) == 3
    # The window ends: sign-ins work again.
    for key in state_redis().scan_iter("xc:fail:*"):
        assert 0 < cast("int", state_redis().ttl(key)) <= 600
        state_redis().delete(key)
    assert b'"auth":1' in _login(tv, subscriber.username, subscriber.password)


def test_ip_limit_covers_every_username(
    tv: Client, subscriber: Subscriber, verifications: list[str]
) -> None:
    for number in range(5):
        assert _login(tv, f"guess{number}", "wrong-password") == AUTH_FAILURE
    assert _login(tv, subscriber.username, subscriber.password) == AUTH_FAILURE
    assert len(verifications) == 5
    # Another address is not affected.
    assert b'"auth":1' in _login(tv, subscriber.username, subscriber.password, "198.51.100.2")


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
