"""Fixtures for the Xtream API tests (customer factories: apps/conftest.py)."""

import io
import logging
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from django.conf import settings
from django.test import Client

from apps.accounts import services as account_services
from apps.accounts.models import Device, User
from apps.core.stores import cache_redis, state_redis
from apps.xtream_api import cache
from apps.xtream_api.playback import set_playback_starter
from apps.xtream_api.source import set_catalog_source
from apps.xtream_api.tests import contract as contract_module
from apps.xtream_api.tests.fakes import FakeStarter, InMemoryCatalogSource, sample_catalog


def _clear_catalog_cache() -> None:
    client = cache_redis()
    for key in list(client.scan_iter(f"{cache.PREFIX}:*")):
        client.delete(key)


@pytest.fixture(autouse=True)
def _clean_stores() -> Iterator[None]:
    """redis-state's test database (this lane's) starts empty, and so does the catalog cache."""
    state_redis().flushdb()
    _clear_catalog_cache()
    yield
    state_redis().flushdb()
    _clear_catalog_cache()


@pytest.fixture
def catalog() -> Iterator[InMemoryCatalogSource]:
    source = InMemoryCatalogSource(sample_catalog())
    previous = set_catalog_source(source)
    yield source
    set_catalog_source(previous)


@pytest.fixture
def starter() -> Iterator[FakeStarter]:
    fake = FakeStarter()
    previous = set_playback_starter(fake)
    yield fake
    set_playback_starter(previous)


@dataclass(frozen=True)
class Subscriber:
    user: User
    device: Device
    username: str
    password: str

    def params(self, **extra: str) -> dict[str, str]:
        return {"username": self.username, "password": self.password, **extra}

    def path(self, kind: str, xc_id: int, ext: str = "mp4") -> str:
        return f"/{kind}/{self.username}/{self.password}/{xc_id}.{ext}"


def subscribe(user: User) -> Subscriber:
    issued = account_services.create_device_credential(user, name="Living room", actor=None)
    return Subscriber(user, issued.device, issued.username, issued.password)


@pytest.fixture
def subscriber(db: None) -> Subscriber:
    """An English-speaking customer with every category, two streams and one device."""
    user, _ = account_services.create_customer(
        {"name": "Mahmoud Test", "email": "xtream@example.com", "locale": "en"},
        access={"max_streams": 2},
        actor=None,
    )
    return subscribe(user)


@pytest.fixture
def tv() -> Client:
    """A client on the Xtream host, as IPTV apps reach it."""
    return Client(headers={"host": settings.TV_HOST})


@pytest.fixture
def contract() -> contract_module.Contract:
    suite = contract_module.load()
    if suite is None:
        pytest.skip(contract_module.MISSING)
    return suite


@pytest.fixture
def log_output() -> Iterator[io.StringIO]:
    """Everything that reaches the root logger, rendered by the configured formatter."""
    root = logging.getLogger()
    console = next(handler for handler in root.handlers if handler.get_name() == "console")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(console.formatter)
    root.addHandler(handler)
    yield stream
    root.removeHandler(handler)
