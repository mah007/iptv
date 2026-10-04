"""Where "started" begins: 30 s in, or a tenth of the way through a short title (ADR-0016)."""

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.catalog.tests.builders import Builder, portal_client
from apps.conftest import CustomerFactory
from apps.engagement import services
from apps.engagement.models import WatchProgress
from apps.playback.models import TitleKind

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("clean_stores")]


@pytest.mark.parametrize(
    ("position_ms", "duration_ms", "expected"),
    [
        (29_999, 5_400_000, False),
        (30_000, 5_400_000, True),
        (2_999, 30_000, False),
        (3_000, 30_000, True),
        (30_000, 600_000, True),
        (29_999, 600_000, False),
        (29_999, None, False),
        (30_000, 0, True),
    ],
)
def test_started(position_ms: int, duration_ms: int | None, expected: bool) -> None:
    assert services.started(position_ms, duration_ms) is expected


@pytest.fixture
def customer(make_customer: CustomerFactory) -> User:
    return make_customer()


def test_a_short_clip_resumes_and_continues(build: Builder, customer: User) -> None:
    clip = build.movie("Clip")
    long_film = build.movie("Long film")
    WatchProgress.objects.create(user=customer, movie=clip, position_ms=6_000, duration_ms=30_000)
    WatchProgress.objects.create(
        user=customer, movie=long_film, position_ms=6_000, duration_ms=5_400_000
    )
    client: APIClient = portal_client(customer)

    body = client.get("/api/v1/continue-watching").json()

    assert [item["title"]["title"] for item in body] == ["Clip"]
    assert services.resume_position(customer, TitleKind.MOVIE, clip.pk) == 6_000
    assert services.resume_position(customer, TitleKind.MOVIE, long_film.pk) == 0
