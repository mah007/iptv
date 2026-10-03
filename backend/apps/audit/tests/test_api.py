"""GET /api/v1/admin/audit: newest first, filterable, paginated, no N+1."""

import datetime as dt
from typing import Any

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.audit.services import AuditTarget, record

pytestmark = pytest.mark.django_db

ADMIN = {"host": settings.ADMIN_HOST}
URL = "/api/v1/admin/audit"


@pytest.fixture
def staff_client(staff_user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(staff_user)
    return client


@pytest.fixture
def history(staff_user: User) -> list[AuditLog]:
    other = get_user_model().objects.create_user(username="other", is_staff=True)
    return [
        record("setting.update", actor=staff_user, target=AuditTarget("core.setting", "a.b")),
        record("customer.create", actor=other, target=AuditTarget("accounts.user", "u1")),
        record("customer.suspend", actor=staff_user, target=AuditTarget("accounts.user", "u1")),
        record("system.expire", actor=None),
    ]


def results(response: Any) -> list[str]:
    return [item["action"] for item in response.json()["results"]]


def test_anonymous_and_non_staff_are_refused(api_client: APIClient, customer_user: User) -> None:
    assert api_client.get(URL, headers=ADMIN).status_code == 401
    api_client.force_authenticate(customer_user)
    assert api_client.get(URL, headers=ADMIN).json()["code"] == "PERMISSION_DENIED"


def test_staff_page_through_newest_first(
    staff_client: APIClient, history: list[AuditLog], staff_user: User
) -> None:
    response = staff_client.get(URL, headers=ADMIN)
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 4
    assert body["next"] is None
    assert body["previous"] is None
    assert results(response) == [
        "system.expire",
        "customer.suspend",
        "customer.create",
        "setting.update",
    ]
    newest_by_staff = body["results"][1]
    assert newest_by_staff["actor"] == {"id": str(staff_user.pk), "username": "staff"}
    assert body["results"][0]["actor"] is None
    assert set(newest_by_staff) == {
        "id",
        "at",
        "actor",
        "actor_ip",
        "action",
        "target_type",
        "target_id",
        "before",
        "after",
    }


def test_page_size_is_capped(staff_client: APIClient, staff_user: User) -> None:
    for index in range(3):
        record(f"bulk.{index}", actor=staff_user)
    page = staff_client.get(URL, {"page_size": 2}, headers=ADMIN).json()
    assert len(page["results"]) == 2
    assert page["next"] is not None
    huge = staff_client.get(URL, {"page_size": 1000}, headers=ADMIN).json()
    assert len(huge["results"]) == 3


def test_filters(staff_client: APIClient, history: list[AuditLog], staff_user: User) -> None:
    def actions(**params: str) -> list[str]:
        return results(staff_client.get(URL, params, headers=ADMIN))

    assert actions(actor=str(staff_user.pk)) == ["customer.suspend", "setting.update"]
    assert actions(action="customer.create") == ["customer.create"]
    assert actions(target_type="accounts.user", target_id="u1") == [
        "customer.suspend",
        "customer.create",
    ]
    future = (timezone.now() + dt.timedelta(minutes=5)).isoformat()
    past = (timezone.now() - dt.timedelta(minutes=5)).isoformat()
    assert actions(at_after=future) == []
    assert len(actions(at_after=past, at_before=future)) == 4


def test_invalid_filters_are_validation_problems(staff_client: APIClient) -> None:
    response = staff_client.get(URL, {"actor": "not-a-uuid"}, headers=ADMIN)
    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert "actor" in response.json()["field_errors"]


def test_ordering_can_be_reversed(staff_client: APIClient, history: list[AuditLog]) -> None:
    response = staff_client.get(URL, {"ordering": "at"}, headers=ADMIN)
    assert results(response)[0] == "setting.update"


def test_listing_costs_three_queries_whatever_the_page(
    staff_client: APIClient, history: list[AuditLog], django_assert_num_queries: Any
) -> None:
    # Permission codes, COUNT(*) and one SELECT joining the actors: no query per row.
    with django_assert_num_queries(3):
        assert staff_client.get(URL, headers=ADMIN).status_code == 200
