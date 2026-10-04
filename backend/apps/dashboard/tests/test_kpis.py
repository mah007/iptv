"""GET /api/v1/admin/dashboard/kpis: customer, device, stream and queue KPIs, cached 30 s."""

from collections.abc import Callable, Iterator
from datetime import timedelta
from typing import Any

import pytest
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts import services as accounts
from apps.accounts.models import Device
from apps.conftest import AdminFactory, CustomerFactory
from apps.dashboard import services
from apps.dashboard.tests.conftest import open_review, record_play, transcode_job
from apps.library.models import Library

pytestmark = pytest.mark.django_db

ADMIN = {"host": settings.ADMIN_HOST}
URL = "/api/v1/admin/dashboard/kpis"
type Capture = Callable[..., Any]


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    services.invalidate()
    yield
    services.invalidate()


def test_kpis(
    owner_client: APIClient,
    make_customer: CustomerFactory,
    library: Library,
    django_assert_num_queries: Capture,
) -> None:
    now = timezone.now()
    make_customer(expires_at=now + timedelta(days=30), devices=2)
    make_customer(expires_at=now + timedelta(days=2), devices=1)
    make_customer(expires_at=now - timedelta(days=1))
    suspended = make_customer(devices=1)
    accounts.suspend_customer(suspended, actor=None)
    accounts.block_device(Device.objects.filter(user=suspended).get(), actor=None)
    sara = make_customer(name="Sara")
    record_play(sara, started=now - timedelta(minutes=5), minutes=None)
    record_play(sara, started=now - timedelta(minutes=3), minutes=None)
    record_play(sara, started=now - timedelta(hours=2))
    open_review(library)
    transcode_job(library, "queued")
    transcode_job(library, "running")
    transcode_job(library, "failed")
    # Permission codes; customers, devices, streams, reviews, jobs.
    with django_assert_num_queries(6):
        response = owner_client.get(URL, headers=ADMIN)
    body = response.json()
    assert {key: value for key, value in body.items() if key != "as_of"} == {
        "customers_total": 5,
        "customers_active": 3,
        "customers_expired": 1,
        "customers_suspended": 1,
        "expiring_7d": 1,
        "devices_total": 4,
        "devices_blocked": 1,
        "streams_now": 2,
        "stream_users_now": 1,
        "reviews_open": 1,
        "transcode_queued": 1,
        "transcode_running": 1,
        "transcode_failed_24h": 1,
    }
    # Cached: a new customer shows up only once the 30 s are over.
    make_customer()
    with django_assert_num_queries(1):
        assert owner_client.get(URL, headers=ADMIN).json()["customers_total"] == 5
    services.invalidate()
    assert owner_client.get(URL, headers=ADMIN).json()["customers_total"] == 6


def test_kpis_survive_a_cache_outage(
    owner_client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_args: object, **_kwargs: object) -> None:
        raise ConnectionError

    monkeypatch.setattr(cache, "get", broken)
    monkeypatch.setattr(cache, "set", broken)
    assert owner_client.get(URL, headers=ADMIN).json()["customers_total"] == 0


def test_kpis_need_dashboard_view(make_admin: AdminFactory) -> None:
    client = APIClient()
    client.force_authenticate(make_admin(permissions=["customers.view"]))
    assert client.get(URL, headers=ADMIN).status_code == 403
    client.force_authenticate(make_admin("viewer"))
    assert client.get(URL, headers=ADMIN).status_code == 200
