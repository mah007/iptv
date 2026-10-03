"""Local fixtures for this app's tests."""

import os
import uuid
from collections.abc import Iterator

import pytest
import redis
from django.conf import settings

from apps.core.services import reset_settings_cache


@pytest.fixture(autouse=True)
def _isolated_settings_cache(request: pytest.FixtureRequest) -> Iterator[None]:
    """Override the shared autouse fixture for pure tests, so they also run on a host
    without the dev stack's redis-cache; tests with a database read settings and get
    the shared reset."""
    uses_db = request.node.get_closest_marker("django_db") is not None or bool(
        {"db", "transactional_db"} & set(request.fixturenames)
    )
    if uses_db:
        reset_settings_cache()
    yield
    if uses_db:
        reset_settings_cache()


@pytest.fixture
def redis_client() -> Iterator[redis.Redis]:
    """A real Redis/Valkey: `TEST_REDIS_URL`, else the test settings' redis-cache.

    Skips when none is reachable, e.g. on a laptop without the dev stack:
    `docker run --rm -p 127.0.0.1:6390:6379 valkey/valkey` and
    `TEST_REDIS_URL=redis://127.0.0.1:6390/0` run these tests anywhere.
    """
    url = os.environ.get("TEST_REDIS_URL") or settings.REDIS_CACHE_URL
    client = redis.Redis.from_url(url, socket_timeout=2, socket_connect_timeout=2)
    try:
        client.ping()
    except redis.RedisError:
        client.close()
        pytest.skip("no Redis reachable; set TEST_REDIS_URL")
    yield client
    client.close()


@pytest.fixture
def redis_key(redis_client: redis.Redis) -> Iterator[str]:
    """A key (and key prefix) no other test uses; deleted afterwards."""
    key = f"test:metadata:{uuid.uuid4().hex}"
    yield key
    stale = [key, *redis_client.scan_iter(match=f"{key}*")]
    redis_client.delete(*stale)
