"""Fixtures shared by every app's tests."""

from collections.abc import Iterator

import pytest
import structlog
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.core.services import reset_settings_cache


@pytest.fixture(autouse=True)
def _isolated_settings_cache() -> Iterator[None]:
    """Settings overrides are cached in redis-cache under a version key; each test
    rolls its database back, so it must also start (and end) with an empty cache."""
    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture(autouse=True)
def _clean_log_context() -> Iterator[None]:
    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()


@pytest.fixture
def staff_user(db: None) -> User:
    return get_user_model().objects.create_user(
        username="staff",
        email="staff@example.com",
        password="staff-pass-123",  # noqa: S106
        is_staff=True,
    )


@pytest.fixture
def customer_user(db: None) -> User:
    return get_user_model().objects.create_user(
        username="customer",
        email="customer@example.com",
        password="customer-pass-123",  # noqa: S106
    )


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()
