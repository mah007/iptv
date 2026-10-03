"""Local fixtures for the media tests."""

import pytest

from apps.media.profiles import Profiles, default_profiles


@pytest.fixture(autouse=True)
def _isolated_settings_cache() -> None:
    """Override the shared autouse fixture: nothing here reads the settings registry,
    so these tests also run on a host without the dev stack's redis-cache."""


@pytest.fixture
def profiles() -> Profiles:
    return default_profiles()
