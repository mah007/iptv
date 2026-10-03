"""Local fixtures for this app's tests."""

import pytest


@pytest.fixture(autouse=True)
def _isolated_settings_cache() -> None:
    """Override the shared autouse fixture: nothing here reads the settings registry,
    so these pure tests also run on a host without the dev stack's redis-cache."""
