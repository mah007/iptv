"""Valkey users (G-38, ADR-0020), against the real redis-state and redis-cache.

The app services sign in as `app`: every command but the administrative and destructive
ones. Tests themselves use `default` (they flush their own databases), so these checks
open their own connections, on this lane's database index. They skip when the dev
stack's .env lacks the users' passwords.
"""

import os
from collections.abc import Iterator
from typing import Any, cast
from urllib.parse import urlsplit

import pytest
import redis
from django.conf import settings

STORES = (("REDIS_STATE", settings.REDIS_STATE_URL), ("REDIS_CACHE", settings.REDIS_CACHE_URL))


def client(store: str, url: str, user: str) -> redis.Redis:
    password = os.environ.get(f"{store}_{user.upper()}_PASSWORD", "")
    if not password:
        pytest.skip(f"{store}_{user.upper()}_PASSWORD is not set")
    parts = urlsplit(url)
    return redis.Redis(
        host=parts.hostname or "localhost",
        port=parts.port or 6379,
        db=int(parts.path.strip("/") or 0),
        username=user,
        password=password,
        socket_timeout=3,
    )


@pytest.fixture(params=STORES, ids=["redis-state", "redis-cache"])
def app(request: pytest.FixtureRequest) -> Iterator[redis.Redis]:
    store, url = request.param
    connection = client(store, url, "app")
    yield connection
    connection.delete("acl-test:key")
    connection.close()


@pytest.mark.parametrize(
    "command",
    [
        ("FLUSHALL",),
        ("FLUSHDB",),
        ("CONFIG", "GET", "maxmemory"),
        ("DEBUG", "SLEEP", "0"),
        ("MODULE", "LIST"),
        ("KEYS", "*"),
        ("SHUTDOWN",),
        ("ACL", "LIST"),
        ("MONITOR",),
        ("BGSAVE",),
        ("REPLICAOF", "NO", "ONE"),
    ],
)
def test_the_app_user_cannot_run_admin_commands(app: redis.Redis, command: tuple[str, ...]) -> None:
    # NOPERM from the ACL; DEBUG is also refused by Valkey's own default
    # (enable-debug-command no) before the ACL is consulted.
    expected = (
        redis.exceptions.ResponseError
        if command[0] == "DEBUG"
        else redis.exceptions.NoPermissionError
    )
    with pytest.raises(expected):
        app.execute_command(*command)


def test_the_app_user_does_what_the_app_needs(app: redis.Redis) -> None:
    assert app.ping()
    app.set("acl-test:key", "1", ex=60)
    assert app.get("acl-test:key") == b"1"
    assert cast("int", app.eval("return redis.call('INCR', KEYS[1])", 1, "acl-test:key")) == 2
    assert list(app.scan_iter("acl-test:*"))
    app.publish("acl-test:channel", "hello")
    assert "redis_version" in cast("dict[str, Any]", app.info("server"))
    app.client_setname("acl-test")


def test_the_exporter_user_only_reads_metrics() -> None:
    for store, url in STORES:
        exporter = client(store, url, "exporter")
        assert "used_memory" in cast("dict[str, Any]", exporter.info("memory"))
        with pytest.raises(redis.exceptions.NoPermissionError):
            exporter.set("acl-test:key", "1")
        with pytest.raises(redis.exceptions.NoPermissionError):
            exporter.execute_command("FLUSHDB")
        exporter.close()


def test_redis_state_has_a_memory_ceiling() -> None:
    admin = redis.Redis.from_url(settings.REDIS_STATE_URL, socket_timeout=3)
    config = cast("dict[str, str]", admin.config_get("maxmemory*"))
    assert int(config["maxmemory"]) > 0
    assert config["maxmemory-policy"] == "noeviction"
