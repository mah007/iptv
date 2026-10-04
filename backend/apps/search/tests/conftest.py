"""Fixtures of the search tests: the catalogue builder, and a Meilisearch index of this
test run's own (the dev stack's `titles` index is never touched)."""

from collections.abc import Iterator

import pytest

from apps.catalog.tests.builders import build, clean_stores
from apps.core.ids import uuid7
from apps.search import index, services
from apps.search.meili import client

__all__ = ["build", "clean_stores", "meili_index"]


@pytest.fixture
def meili_index(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A fresh, uniquely named index in the stack's Meilisearch, deleted afterwards."""
    name = f"test_titles_{uuid7().hex}"
    monkeypatch.setattr(index, "INDEX", name)
    services.reset_breaker()
    yield name
    services.reset_breaker()
    meili = client()
    for uid in (name, *meili.index_uids(prefix=f"{name}_")):
        meili.wait(meili.delete_index(uid))
