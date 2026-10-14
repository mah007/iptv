"""Per-account limits on expensive endpoints (G-20; ADR-0021)."""

from typing import Any

import pytest
from django.conf import settings
from django.test import RequestFactory
from rest_framework.request import Request
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.conftest import CustomerFactory
from apps.core.services import set_setting
from apps.core.stores import cache_redis
from apps.core.throttling import SearchThrottle, SettingRateThrottle

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _fresh_counters() -> None:
    for key in cache_redis().scan_iter("rl:*"):
        cache_redis().delete(key)


def _request(user: Any, method: str = "GET", ip: str = "198.51.100.1") -> Request:
    raw = RequestFactory().generic(method, "/x", REMOTE_ADDR=ip)
    request = Request(raw)
    request.user = user
    return request


def test_counts_per_account_not_per_address(make_customer: CustomerFactory) -> None:
    set_setting("security.rate_search_per_minute", 5, actor=None)
    first, second = make_customer(), make_customer()
    throttle = SearchThrottle()
    for number in range(5):
        assert throttle.allow_request(_request(first, ip=f"198.51.100.{number}"), None)  # type: ignore[arg-type]
    assert not throttle.allow_request(_request(first), None)  # type: ignore[arg-type]
    wait = throttle.wait()
    assert wait is not None
    assert 0 < wait <= 60
    # Someone else behind the same address has their own budget (carrier-grade NAT).
    assert SearchThrottle().allow_request(_request(second), None)  # type: ignore[arg-type]


def test_only_the_named_methods_count(make_customer: CustomerFactory) -> None:
    class PostOnly(SettingRateThrottle):
        scope = "test_post_only"
        setting = "security.rate_search_per_minute"
        methods = frozenset({"POST"})

    set_setting("security.rate_search_per_minute", 5, actor=None)
    user = make_customer()
    for _ in range(20):
        assert PostOnly().allow_request(_request(user, "GET"), None)  # type: ignore[arg-type]


def test_a_customer_over_the_search_limit_gets_429(customer_user: User) -> None:
    set_setting("security.rate_search_per_minute", 5, actor=None)
    client = APIClient()
    client.force_login(customer_user)
    headers = {"host": settings.APP_HOST}
    for _ in range(5):
        assert client.get("/api/v1/search?q=matrix", headers=headers).status_code != 429
    refused = client.get("/api/v1/search?q=matrix", headers=headers)
    assert refused.status_code == 429
    assert refused.json()["code"] == "RATE_LIMITED"
    assert int(refused["Retry-After"]) > 0
