"""Fixtures for the live TV tests (customers and admins: apps/conftest.py)."""

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from pytest_django import Settings

from apps.catalog.models import Category, CategoryKind
from apps.core.stores import cache_redis, state_redis
from apps.live import sources
from apps.live.models import EpgSource, EpgSourceKind, LiveChannel
from apps.xtream_api import cache

type ChannelFactory = Callable[..., LiveChannel]
SOURCE_URL = "rtsp://encoder.example.net:8554/studio?token=s3cr3t-source-token"


def _clear_catalog_cache() -> None:
    client = cache_redis()
    for key in list(client.scan_iter(f"{cache.PREFIX}:*")):
        client.delete(key)


@pytest.fixture(autouse=True)
def _clean_stores() -> Iterator[None]:
    """This lane's redis-state database starts empty, and so does the Xtream cache."""
    state_redis().flushdb()
    _clear_catalog_cache()
    yield
    state_redis().flushdb()
    _clear_catalog_cache()


@pytest.fixture
def live_root(settings: Settings, tmp_path: Path) -> Path:
    root = tmp_path / "live"
    root.mkdir()
    settings.LIVE_ROOT = str(root)
    return root


@pytest.fixture
def live_group(db: None) -> Category:
    return Category.objects.create(
        kind=CategoryKind.LIVE, slug="showcase", name_en="Showcase", name_ar="عروض", sort=10
    )


@pytest.fixture
def make_channel(live_group: Category) -> ChannelFactory:
    counter = iter(range(1, 10_000))

    def factory(**overrides: Any) -> LiveChannel:
        number = next(counter)
        source_url = overrides.pop("source_url", SOURCE_URL)
        values: dict[str, Any] = {
            "name": f"Channel {number}",
            "group": live_group,
            "sort": number * 10,
            "enabled": True,
            "rights_holder": "Smart IPTV",
            "license_ref": "own-production",
            "source_encrypted": sources.encrypt(source_url),
        }
        values.update(overrides)
        return LiveChannel.objects.create(**values)

    return factory


@pytest.fixture
def upload_source(db: None) -> Callable[[bytes], EpgSource]:
    def factory(document: bytes, **overrides: Any) -> EpgSource:
        from apps.live import epg  # noqa: PLC0415

        return EpgSource.objects.create(
            name=overrides.pop("name", "Guide"),
            kind=EpgSourceKind.UPLOAD,
            upload=epg.compress_upload(document),
            **overrides,
        )

    return factory
