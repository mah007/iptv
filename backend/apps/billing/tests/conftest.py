"""Fixtures for the billing tests (customer and admin factories: apps/conftest.py).

Provider HTTP never reaches the network: every test runs behind a transport that
fails on any request, and tests that talk to Stripe or Moyasar install `Recorded`.

fixtures/ holds provider responses and webhook events shaped field for field like
the providers' API reference (Stripe API, Moyasar API v1). No sandbox keys were
available when they were written, so they are transcribed from the documented
objects rather than captured. Tests fill in `__INVOICE__`, `__PAYMENT__` and
`__CHECKOUT__`.
"""

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from pytest_django import Settings

from apps.billing import kpis, services
from apps.billing.models import Plan
from apps.billing.providers.base import use_transport
from apps.core.services import reset_settings_cache, set_setting
from apps.core.stores import state_redis

FIXTURES = Path(__file__).parent / "fixtures"
type PlanFactory = Callable[..., Plan]


def configure(key: str, value: object) -> None:
    """Change a setting and let this process see it now (the cache bump waits for a
    commit that tests never make)."""
    set_setting(key, value, actor=None)
    reset_settings_cache()


def fixture_json(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data


@dataclass
class Recorded:
    """An httpx transport answering from recorded responses, keeping every request."""

    routes: dict[tuple[str, str], tuple[int, dict[str, Any]]] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)

    def add(self, method: str, url: str, status: int, body: dict[str, Any]) -> None:
        self.routes[(method, url)] = (status, body)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = (request.method, str(request.url))
        if key not in self.routes:
            msg = f"unexpected provider request {key}"
            raise AssertionError(msg)
        status, body = self.routes[key]
        return httpx.Response(status, json=body)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


def _no_network(request: httpx.Request) -> httpx.Response:
    msg = f"tests must not call providers: {request.method} {request.url}"
    raise AssertionError(msg)


@pytest.fixture(autouse=True)
def _offline_providers() -> Iterator[None]:
    with use_transport(httpx.MockTransport(_no_network)):
        yield


@pytest.fixture
def recorded() -> Iterator[Recorded]:
    transport = Recorded()
    with use_transport(transport.transport):
        yield transport


@pytest.fixture(autouse=True)
def _clean_state_redis() -> Iterator[None]:
    """redis-state's test database (this lane's) starts empty: entitlements, slots; and
    no billing KPIs are cached."""
    state_redis().flushdb()
    kpis.invalidate()
    yield
    state_redis().flushdb()
    kpis.invalidate()


@pytest.fixture
def provider_keys(settings: Settings) -> Settings:
    settings.STRIPE_SECRET_KEY = "sk_test_fixture"  # noqa: S105 (not a real key)
    settings.STRIPE_WEBHOOK_SECRET = "whsec_fixture"  # noqa: S105
    settings.MOYASAR_SECRET_KEY = "sk_test_moyasar_fixture"  # noqa: S105
    settings.MOYASAR_WEBHOOK_SECRET = "moyasar-webhook-fixture"  # noqa: S105
    return settings


@pytest.fixture
def make_plan(db: None) -> PlanFactory:
    counter = iter(range(1, 10_000))

    def factory(**values: Any) -> Plan:
        number = next(counter)
        category_ids = values.pop("category_ids", [])
        defaults: dict[str, Any] = {
            "code": f"plan-{number}",
            "name_en": f"Plan {number}",
            "name_ar": f"باقة {number}",
            "price": 4900,
            "currency": "SAR",
            "duration_days": 30,
            "max_streams": 2,
            "max_devices": 3,
            "max_quality": 1080,
        }
        defaults.update(values)
        return services.create_plan(defaults, category_ids=category_ids, actor=None)

    return factory


@pytest.fixture
def plan(make_plan: PlanFactory) -> Plan:
    return make_plan(code="standard", name_en="Standard", name_ar="القياسية")


@pytest.fixture
def trial_plan(make_plan: PlanFactory) -> Plan:
    return make_plan(
        code="trial", name_en="Trial", name_ar="تجربة", price=0, is_trial=True, max_streams=1
    )
