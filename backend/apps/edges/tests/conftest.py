"""Edge tests use this lane's redis-state database and the public ADR-0007 test keys."""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from pytest_django import Settings

from apps.core.stores import state_redis
from apps.playback import tokens

VECTORS = Path(__file__).parents[2] / "playback" / "tests" / "data" / "token_vectors.json"
MEDIA_BASE_URL = "https://media.example.test"


@pytest.fixture(autouse=True)
def _clean_state_redis() -> Iterator[None]:
    state_redis().flushdb()
    yield
    state_redis().flushdb()


@pytest.fixture(autouse=True)
def media_keys(settings: Settings, tmp_path: Path) -> Iterator[tokens.KeySet]:
    keys = json.loads(VECTORS.read_text(encoding="utf-8"))["keys"]
    path = tmp_path / "media_token_keys.json"
    path.write_text(json.dumps(keys), encoding="utf-8")
    settings.MEDIA_TOKEN_KEYS_FILE = str(path)
    settings.MEDIA_BASE_URL = MEDIA_BASE_URL
    tokens.reset_keyring()
    yield tokens.parse_keyset(keys)
    tokens.reset_keyring()
