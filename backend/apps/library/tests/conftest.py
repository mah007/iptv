"""Local fixtures for this app's tests."""

from collections.abc import Iterator

import pytest

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
