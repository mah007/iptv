"""GET /api/v1/admin/customers/{id}/history: a customer's watch history for the admin."""

from datetime import timedelta
from typing import Any

import pytest
from django.conf import settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.conftest import AdminFactory, CustomerFactory
from apps.dashboard.tests.conftest import make_episode, make_movie
from apps.engagement.models import WatchProgress

pytestmark = pytest.mark.django_db

ADMIN = {"host": settings.ADMIN_HOST}


def url(customer_id: object) -> str:
    return f"/api/v1/admin/customers/{customer_id}/history"


def test_history_lists_movies_and_episodes_newest_first(
    owner_client: APIClient, make_customer: CustomerFactory, django_assert_num_queries: Any
) -> None:
    sara = make_customer(name="Sara")
    other = make_customer(name="Other")
    movie = make_movie("Wadjda")
    episode = make_episode("Bluey")
    older = WatchProgress.objects.create(
        user=sara, movie=movie, position_ms=60_000, duration_ms=5_400_000
    )
    WatchProgress.objects.filter(pk=older.pk).update(updated_at=timezone.now() - timedelta(days=1))
    WatchProgress.objects.create(
        user=sara,
        episode=episode,
        series=episode.season.series,
        position_ms=1_200_000,
        duration_ms=1_200_000,
        completed=True,
    )
    WatchProgress.objects.create(user=other, movie=movie)
    # Permissions; the customer; the count; the page with its titles.
    with django_assert_num_queries(4):
        body = owner_client.get(url(sara.pk), headers=ADMIN).json()
    assert body["count"] == 2
    first, second = body["results"]
    assert first["title"] == {
        "kind": "series",
        "id": str(episode.season.series.pk),
        "title": "Bluey",
        "title_ar": "Bluey (ar)",
        "season": 1,
        "episode": 1,
        "episode_title": "Pilot",
    }
    assert first["completed"] is True
    assert second["title"]["kind"] == "movie"
    assert second["title"]["title"] == "Wadjda"
    assert (second["position_ms"], second["duration_ms"]) == (60_000, 5_400_000)


def test_history_is_for_customers_with_customers_view(
    owner: Any, make_admin: AdminFactory, make_customer: CustomerFactory
) -> None:
    client = APIClient()
    client.force_authenticate(make_admin(permissions=["dashboard.view"]))
    sara = make_customer()
    assert client.get(url(sara.pk), headers=ADMIN).status_code == 403
    owner_client = APIClient()
    owner_client.force_authenticate(owner)
    assert owner_client.get(url(owner.pk), headers=ADMIN).status_code == 404
