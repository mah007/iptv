"""The catalog response cache: shared per scope and locale, retired by a version bump."""

from dataclasses import replace
from typing import Any, cast

import pytest
import redis
from django.test import Client

from apps.core.stores import cache_redis
from apps.xtream_api import cache
from apps.xtream_api.dto import Text
from apps.xtream_api.source import CatalogScope
from apps.xtream_api.tests.conftest import Subscriber, subscribe
from apps.xtream_api.tests.contract import Contract
from apps.xtream_api.tests.fakes import InMemoryCatalogSource

pytestmark = pytest.mark.django_db
API = "/player_api.php"


def movies(tv: Client, subscriber: Subscriber) -> Any:
    return tv.get(API, subscriber.params(action="get_vod_streams")).json()


def test_responses_are_cached_per_scope_and_locale(
    tv: Client, subscriber: Subscriber, catalog: InMemoryCatalogSource, make_customer: Any
) -> None:
    first = movies(tv, subscriber)
    calls = catalog.calls.total()
    assert movies(tv, subscriber) == first
    assert catalog.calls.total() == calls  # served from redis-cache

    # Another customer with the same access and locale shares the entry.
    other = subscribe(make_customer())
    other.user.locale = "en"
    other.user.save(update_fields=["locale"])
    assert movies(tv, other) == first
    assert catalog.calls.total() == calls

    keys = sorted(key.decode() for key in cache_redis().scan_iter("xc:*"))
    version = cast("bytes", cache_redis().get(cache.VERSION_KEY)).decode()
    expected = cache.key(CatalogScope(), "en", "get_vod_streams", version=version)
    assert expected in keys
    assert all(key == cache.VERSION_KEY or key.count(":") >= 3 for key in keys)


def test_invalidation_retires_every_entry(
    tv: Client, subscriber: Subscriber, catalog: InMemoryCatalogSource, contract: Contract
) -> None:
    assert movies(tv, subscriber)[0]["name"] == "The Matrix"
    matrix = catalog.catalog.movies[0]
    catalog.catalog.movies[0] = replace(
        matrix, movie=replace(matrix.movie, title=Text("Matrix 4K"))
    )
    assert movies(tv, subscriber)[0]["name"] == "The Matrix"  # still cached

    cache.invalidate()
    fresh = tv.get(API, subscriber.params(action="get_vod_streams"))
    contract.check("get_vod_streams", fresh.content)
    assert fresh.json()[0]["name"] == "Matrix 4K"


def test_invalidation_waits_for_the_commit(
    catalog: InMemoryCatalogSource, django_capture_on_commit_callbacks: Any
) -> None:
    before = cache_redis().get(cache.VERSION_KEY)
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        cache.invalidate_on_commit()
    assert cache_redis().get(cache.VERSION_KEY) == before
    for callback in callbacks:
        callback()
    assert cache_redis().get(cache.VERSION_KEY) not in (None, before)


def test_category_filters_are_cached_separately(
    tv: Client, subscriber: Subscriber, catalog: InMemoryCatalogSource
) -> None:
    tv.get(API, subscriber.params(action="get_vod_streams", category_id="14"))
    tv.get(API, subscriber.params(action="get_vod_streams", category_id="999"))
    keys = [key.decode() for key in cache_redis().scan_iter("xc:*:get_vod_streams:*")]
    # Unknown categories never reach the source or the cache.
    assert [key.rsplit(":", 1)[-1] for key in keys] == ["14"]


def test_not_found_is_not_cached(
    tv: Client, subscriber: Subscriber, catalog: InMemoryCatalogSource
) -> None:
    for _ in range(2):
        tv.get(API, subscriber.params(action="get_vod_info", vod_id="424242"))
    assert catalog.calls["movie"] == 2
    assert not list(cache_redis().scan_iter("xc:*:get_vod_info:*"))


def test_cache_outage_falls_back_to_the_source(
    tv: Client,
    subscriber: Subscriber,
    catalog: InMemoryCatalogSource,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Down:
        def __getattr__(self, name: str) -> Any:
            def fail(*args: object, **kwargs: object) -> None:
                raise redis.ConnectionError("cache down")

            return fail

    monkeypatch.setattr(cache, "cache_redis", Down)
    assert [item["stream_id"] for item in movies(tv, subscriber)] == [1001, 1002]
    cache.invalidate()  # logged, never raised
