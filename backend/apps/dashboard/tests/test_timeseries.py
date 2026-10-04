"""GET /api/v1/admin/dashboard/timeseries: the dashboard charts, cached 30 s per time zone."""

from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from django.conf import settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import CustomerAccess, User
from apps.catalog.models import Category, CategoryKind, MediaImage
from apps.conftest import AdminFactory, CustomerFactory
from apps.dashboard import charts
from apps.dashboard.tests.conftest import make_episode, make_movie, record_play

pytestmark = pytest.mark.django_db

ADMIN = {"host": settings.ADMIN_HOST}
URL = "/api/v1/admin/dashboard/timeseries"
RIYADH = ZoneInfo("Asia/Riyadh")
# 12:07 UTC is 15:07 in Riyadh.
NOW = datetime(2026, 10, 4, 12, 7, tzinfo=UTC)


def category(slug: str, kind: str = CategoryKind.VOD) -> Category:
    return Category.objects.create(kind=kind, name_en=slug.title(), name_ar=f"{slug}-ar", slug=slug)


def test_streams_are_counted_per_quarter_hour(make_customer: CustomerFactory) -> None:
    user = make_customer()
    record_play(user, started=NOW - timedelta(minutes=50), minutes=40)  # 11:17-11:57
    record_play(user, started=NOW - timedelta(minutes=20), minutes=None)  # 11:47-now
    record_play(user, started=NOW - timedelta(hours=30), minutes=60)  # before the window
    series = charts.compute_timeseries(tz=RIYADH, now=NOW)
    assert series.bucket_minutes == 15
    assert len(series.streams) == 96
    assert series.streams[-1].at == datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
    by_time = {
        point.at.astimezone(UTC).strftime("%H:%M"): point.streams for point in series.streams
    }
    assert by_time["11:00"] == 0
    assert by_time["11:15"] == 1
    assert by_time["11:45"] == 2
    assert by_time["12:00"] == 1
    assert series.streams_peak == 2
    assert series.streams_peak_at is not None
    assert series.streams_peak_at.astimezone(UTC).strftime("%H:%M") == "11:45"


def test_days_follow_the_admin_time_zone(make_customer: CustomerFactory) -> None:
    user = make_customer()
    # 22:30 UTC on 3 October is 01:30 on 4 October in Riyadh.
    late = datetime(2026, 10, 3, 22, 30, tzinfo=UTC)
    User.objects.filter(pk=user.pk).update(created_at=late)
    CustomerAccess.objects.filter(user=user).update(expires_at=late + timedelta(hours=1))
    record_play(user, started=late, minutes=90)
    series = charts.compute_timeseries(tz=RIYADH, now=NOW)
    assert len(series.days) == 30
    assert series.days[-1].date == date(2026, 10, 4)
    today = series.days[-1]
    assert (today.signups, today.churned, today.plays, today.watch_hours) == (1, 1, 1, 1.5)
    in_utc = charts.compute_timeseries(tz=ZoneInfo("UTC"), now=NOW)
    assert in_utc.days[-2].date == date(2026, 10, 3)
    assert (in_utc.days[-2].signups, in_utc.days[-1].signups) == (1, 0)


def test_categories_and_top_titles(make_customer: CustomerFactory) -> None:
    user = make_customer()
    action, drama, kids = category("action"), category("drama"), category("kids", "series")
    matrix = make_movie("The Matrix", action, year=1999)
    MediaImage.objects.create(movie=matrix, kind="poster", is_primary=True, sizes={})
    wadjda = make_movie("Wadjda", drama)
    episode = make_episode("Bluey", kids)
    for minutes in (10, 20, 30):
        record_play(user, started=NOW - timedelta(days=1), minutes=minutes, movie=matrix)
    record_play(user, started=NOW - timedelta(days=2), minutes=60, episode=episode)
    record_play(user, started=NOW - timedelta(days=2), minutes=60, episode=episode)
    record_play(user, started=NOW - timedelta(days=40), minutes=60, movie=wadjda)  # too old
    series = charts.compute_timeseries(tz=RIYADH, now=NOW)
    assert [(item.name_en, item.plays) for item in series.categories] == [
        ("Action", 3),
        ("Kids", 2),
    ]
    assert [
        (item.kind, item.title, item.plays, item.watch_hours) for item in series.top_titles
    ] == [
        ("movie", "The Matrix", 3, 1.0),
        ("series", "Bluey", 2, 2.0),
    ]
    assert series.top_titles[0].poster is not None
    assert series.top_titles[0].year == 1999
    assert series.top_titles[1].title_ar == "Bluey (ar)"


def test_endpoint_uses_the_admins_time_zone_and_constant_queries(
    owner: User,
    owner_client: APIClient,
    make_customer: CustomerFactory,
    django_assert_num_queries: Any,
) -> None:
    user = make_customer()
    drama = category("drama")
    for index in range(5):
        movie = make_movie(f"Film {index}", drama)
        MediaImage.objects.create(movie=movie, kind="poster", is_primary=True, sizes={})
        record_play(user, started=timezone.now() - timedelta(hours=index + 1), movie=movie)
    record_play(user, started=timezone.now() - timedelta(hours=1), episode=make_episode("Show"))
    owner.timezone = "Europe/London"
    owner.save(update_fields=["timezone"])
    # Permissions; streams; sign-ups, churn, plays per day; categories; top titles with
    # their movies and series and the images of each.
    with django_assert_num_queries(11):
        body = owner_client.get(URL, headers=ADMIN).json()
    assert body["time_zone"] == "Europe/London"
    assert len(body["streams"]) == 96
    assert len(body["days"]) == 30
    assert body["categories"][0]["plays"] == 5
    assert len(body["top_titles"]) == 6
    assert {title["kind"] for title in body["top_titles"]} == {"movie", "series"}
    assert sum(title["poster"] is not None for title in body["top_titles"]) == 5
    # Cached for 30 s: only the permission lookup.
    with django_assert_num_queries(1):
        assert owner_client.get(URL, headers=ADMIN).json() == body


def test_an_unknown_time_zone_falls_back_to_riyadh(make_admin: AdminFactory) -> None:
    admin = make_admin(permissions=["dashboard.view"])
    User.objects.filter(pk=admin.pk).update(timezone="Mars/Base")
    admin.refresh_from_db()
    client = APIClient()
    client.force_authenticate(admin)
    assert client.get(URL, headers=ADMIN).json()["time_zone"] == "Asia/Riyadh"


def test_timeseries_need_dashboard_view(make_admin: AdminFactory) -> None:
    client = APIClient()
    client.force_authenticate(make_admin(permissions=["customers.view"]))
    assert client.get(URL, headers=ADMIN).status_code == 403
