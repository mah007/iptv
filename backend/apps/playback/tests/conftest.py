"""Playback tests use this lane's redis-state test database, the public ADR-0007 test
keys and a fixed media origin (factories: apps/conftest.py)."""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from pytest_django import Settings

from apps.core.stores import state_redis
from apps.playback import tokens

VECTORS = Path(__file__).parent / "data" / "token_vectors.json"
MEDIA_BASE_URL = "https://media.example.test"


@pytest.fixture(autouse=True)
def _clean_state_redis() -> Iterator[None]:
    """redis-state's test database (see config.settings.test) starts empty."""
    state_redis().flushdb()
    yield
    state_redis().flushdb()


@pytest.fixture(autouse=True)
def media_keys(settings: Settings, tmp_path: Path) -> Iterator[tokens.KeySet]:
    """Token keys from the shared vectors (public test data) and a fixed media origin."""
    keys = json.loads(VECTORS.read_text(encoding="utf-8"))["keys"]
    path = tmp_path / "media_token_keys.json"
    path.write_text(json.dumps(keys), encoding="utf-8")
    settings.MEDIA_TOKEN_KEYS_FILE = str(path)
    settings.MEDIA_BASE_URL = MEDIA_BASE_URL
    tokens.reset_keyring()
    yield tokens.parse_keyset(keys)
    tokens.reset_keyring()
