"""Entitlement tests use redis-state's test database (factories: apps/conftest.py)."""

from collections.abc import Iterator

import pytest

from apps.core.stores import state_redis


@pytest.fixture(autouse=True)
def _clean_state_redis() -> Iterator[None]:
    """redis-state's test database (13, see config.settings.test) starts empty."""
    state_redis().flushdb()
    yield
    state_redis().flushdb()
