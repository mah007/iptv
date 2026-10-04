"""Continue watching, history, favourites, ratings, recommendations and the home screen
(SPEC §7.9, §9, §10 Engagement; ADR-0013)."""

from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.catalog import home
from apps.catalog.models import Collection, CollectionItem, Movie, Series
from apps.catalog.signals import catalog_changed
from apps.catalog.tests.builders import Builder, episode, portal_client
from apps.conftest import CustomerFactory
from apps.engagement import recommend
from apps.engagement.models import Favorite, Rating, SimilarTitle, Thumb, WatchProgress
from apps.playback.models import PlaybackSession, TitleKind

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("clean_stores")]


@pytest.fixture
def customer(make_customer: CustomerFactory) -> User:
    return make_customer()


@pytest.fixture
def client(customer: User) -> APIClient:
    return portal_client(customer)


def watched(user: User, title: Movie, *, position: int = 600_000, completed: bool = False) -> None:
    WatchProgress.objects.create(
        user=user, movie=title, position_ms=position, duration_ms=5_400_000, completed=completed
    )


# --- Continue watching and history ------------------------------------------------------------


def test_continue_watching_resumes_movies_and_series(
    build: Builder, customer: User, client: APIClient
) -> None:
    started = build.movie("Started")
    finished = build.movie("Finished")
    barely = build.movie("Barely")
    series = build.series("Show", seasons={1: 3})
    watched(customer, started)
    watched(customer, finished, position=5_300_000, completed=True)
    watched(customer, barely, position=5_000)
    WatchProgress.objects.create(
        user=customer,
        episode=episode(series, 1, 1),
        series=series,
        position_ms=2_700_000,
        duration_ms=2_700_000,
        completed=True,
    )
    items = client.get("/api/v1/continue-watching").json()
    assert [(item["type"], item["title"]["title"]) for item in items] == [
        ("episode", "Show"),
        ("movie", "Started"),
    ]
    up_next = items[0]
    assert up_next["id"] is None
    assert up_next["episode"]["number"] == 2
    assert up_next["position_ms"] == 0
    assert items[1]["position_ms"] == 600_000


def test_an_unfinished_episode_is_resumed(
    build: Builder, customer: User, client: APIClient
) -> None:
    series = build.series("Show")
    WatchProgress.objects.create(
        user=customer,
        episode=episode(series, 1, 2),
        series=series,
        position_ms=900_000,
        duration_ms=2_700_000,
    )
    item = client.get("/api/v1/continue-watching").json()[0]
    assert item["episode"]["number"] == 2
    assert item["position_ms"] == 900_000


def test_watch_history_pages_and_forgets(build: Builder, customer: User, client: APIClient) -> None:
    movies = [build.movie(f"Movie {index}") for index in range(3)]
    for movie in movies:
        watched(customer, movie, completed=True)
    first = client.get("/api/v1/watch-history?page_size=2").json()
    assert len(first["results"]) == 2
    rest = client.get(first["next"]).json()
    assert len(rest["results"]) == 1
    entry = first["results"][0]
    assert entry["completed"] is True
    assert client.delete(f"/api/v1/watch-history/{entry['id']}").status_code == 204
    assert client.delete(f"/api/v1/watch-history/{entry['id']}").status_code == 404
    assert WatchProgress.objects.filter(user=customer).count() == 2


# --- Favourites and ratings -------------------------------------------------------------------


def test_favorites(build: Builder, customer: User, client: APIClient) -> None:
    movie = build.movie("Film")
    series = build.series("Show")
    late = build.category("late", adult=True)
    hidden = build.movie("Adult", categories=[late])
    added = client.post(
        "/api/v1/favorites", {"title_type": "movie", "title_id": str(movie.pk)}, format="json"
    )
    assert added.status_code == 201
    assert added.json()["added"] is True
    again = client.post(
        "/api/v1/favorites", {"title_type": "movie", "title_id": str(movie.pk)}, format="json"
    )
    assert again.status_code == 200
    client.post(
        "/api/v1/favorites", {"title_type": "series", "title_id": str(series.pk)}, format="json"
    )
    refused = client.post(
        "/api/v1/favorites", {"title_type": "movie", "title_id": str(hidden.pk)}, format="json"
    )
    assert refused.status_code == 404
    assert [card["title"] for card in client.get("/api/v1/favorites").json()] == ["Show", "Film"]
    assert client.delete(f"/api/v1/favorites/movie/{movie.pk}").status_code == 204
    assert client.delete(f"/api/v1/favorites/other/{movie.pk}").status_code == 404
    assert list(Favorite.objects.filter(user=customer).values_list("series_id", flat=True)) == [
        series.pk
    ]


def test_ratings(build: Builder, customer: User, client: APIClient) -> None:
    movie = build.movie("Film")
    body = {"title_type": "movie", "title_id": str(movie.pk)}
    assert (
        client.post("/api/v1/ratings", {**body, "value": "up"}, format="json").json()["value"]
        == "up"
    )
    assert (
        client.post("/api/v1/ratings", {**body, "value": "down"}, format="json").status_code == 200
    )
    assert Rating.objects.get(user=customer, movie=movie).value == Thumb.DOWN
    cleared = client.post("/api/v1/ratings", {**body, "value": None}, format="json")
    assert cleared.json()["value"] is None
    assert not Rating.objects.filter(user=customer).exists()


# --- Recommendations --------------------------------------------------------------------------


def test_similar_titles_weight_genres_cast_and_directors(build: Builder) -> None:
    drama = build.genre("Drama")
    crime = build.genre("Crime")
    source = build.movie("Source", genres=[drama, crime], cast=["A", "B"], directors=["D"])
    close = build.movie("Close", genres=[drama, crime], cast=["A"], directors=["D"])
    loose = build.movie("Loose", genres=[drama])
    build.movie("Unrelated", year=1950)
    build.series("A series", genres=[drama])
    written = recommend.compute_similar_titles()
    assert written["movie"] > 0
    rows = list(
        SimilarTitle.objects.filter(movie=source).values_list("similar_movie__title", "score")
    )
    assert [title for title, _score in rows] == ["Close", "Loose"]
    scores = dict(rows)
    expected_close = 0.35 * 1 + 0.25 * 0.5 + 0.15 * 1 + 0.05 + 0.05
    assert scores["Close"] == pytest.approx(expected_close, abs=1e-3)
    assert scores["Loose"] == pytest.approx(0.35 * 0.5 + 0.05 + 0.05, abs=1e-3)
    assert not SimilarTitle.objects.filter(movie=source, similar_movie=source).exists()
    assert recommend.compute_similar_titles() == written  # idempotent
    del close, loose


def test_more_like_this_falls_back_to_genres_before_the_first_run(
    build: Builder, client: APIClient
) -> None:
    drama = build.genre("Drama")
    movie = build.movie("Film", genres=[drama])
    build.movie("Same genre", genres=[drama], popularity=5)
    build.movie("Other genre", genres=[build.genre("Comedy")])
    body = client.get(f"/api/v1/movies/{movie.pk}").json()
    assert [card["title"] for card in body["similar"]] == ["Same genre"]


def test_recommendation_rows(build: Builder, customer: User, client: APIClient) -> None:
    drama = build.genre("Drama")
    seen = build.movie("Seen", genres=[drama], cast=["A"])
    build.movie("Liked", genres=[drama], cast=["A"])
    disliked = build.movie("Disliked", genres=[drama], cast=["A"])
    popular = build.movie("Popular", genres=[build.genre("Comedy")])
    recommend.compute_similar_titles()
    watched(customer, seen, position=5_300_000, completed=True)
    Rating.objects.create(user=customer, movie=disliked, value=Thumb.DOWN)
    PlaybackSession.objects.create(
        session_key="a" * 32,
        user=customer,
        title_kind=TitleKind.MOVIE,
        title_id=popular.pk,
        rendition="compat",
        started_at=timezone.now() - timedelta(days=1),
        last_heartbeat_at=timezone.now(),
    )
    body = client.get("/api/v1/recommendations").json()
    because = body["because_you_watched"]
    assert [row["because_of"]["title"] for row in because] == ["Seen"]
    assert [card["title"] for card in because[0]["items"]] == ["Liked"]
    picks = [card["title"] for card in body["top_picks"]]
    assert picks[0] == "Liked"
    assert "Disliked" not in picks
    assert "Seen" not in picks
    assert [card["title"] for card in body["popular"]] == ["Popular"]


def test_popular_this_week_decays_and_counts_episodes_for_their_series(
    build: Builder, customer: User
) -> None:
    movie = build.movie("Movie")
    series = build.series("Show")
    now = timezone.now()
    for days, kind, title_id in (
        (6, TitleKind.MOVIE, movie.pk),
        (6, TitleKind.MOVIE, movie.pk),
        (0, TitleKind.EPISODE, episode(series, 1, 1).pk),
        (9, TitleKind.EPISODE, episode(series, 1, 2).pk),
    ):
        PlaybackSession.objects.create(
            session_key=f"{days}{kind}{title_id}"[:32],
            user=customer,
            title_kind=kind,
            title_id=title_id,
            rendition="compat",
            started_at=now - timedelta(days=days),
            last_heartbeat_at=now,
            ended_at=now,
            end_reason="stopped",
        )
    assert recommend.popular_refs(now=now) == [("series", series.pk), ("movie", movie.pk)]


# --- Home -------------------------------------------------------------------------------------


def test_home_rows(
    build: Builder, customer: User, client: APIClient, django_assert_max_num_queries: Any
) -> None:
    drama = build.category("drama")
    featured = build.movie("Featured", categories=[drama], featured=True)
    build.movie("Plain", categories=[drama])
    series = build.series("Show")
    collection = Collection.objects.create(slug="picks", name_en="Picks", name_ar="مختارات")
    CollectionItem.objects.create(collection=collection, series=series, sort=1)
    watched(customer, featured)
    body = client.get("/api/v1/home").json()
    assert body["hero"][0]["title"] == "Featured"
    assert body["hero"][0]["backdrop"]["url"].endswith(".webp")
    assert [item["title"]["title"] for item in body["continue_watching"]] == ["Featured"]
    kinds = [row["kind"] for row in body["rows"]]
    assert kinds[0] == "recently_added"
    assert "collection" in kinds
    assert "category" in kinds
    category_row = next(row for row in body["rows"] if row["kind"] == "category")
    assert category_row["category"]["slug"] == "drama"
    assert {item["title"] for item in category_row["items"]} == {"Featured", "Plain"}

    arabic = client.get("/api/v1/home", headers={"Accept-Language": "ar"}).json()
    assert next(row for row in arabic["rows"] if row["kind"] == "collection")["title"] == "مختارات"
    assert arabic["rows"][0]["title"] == "أضيف حديثًا"


def test_home_rows_are_cached_until_the_catalogue_changes(
    build: Builder, client: APIClient, customer: User
) -> None:
    build.movie("First")
    first = client.get("/api/v1/home").json()
    build.movie("Second")
    cached = client.get("/api/v1/home").json()
    assert cached["rows"][0] == first["rows"][0]
    catalog_changed.send(sender=None, kind="movie", ids=())
    home.invalidate()
    fresh = client.get("/api/v1/home").json()
    assert {item["title"] for item in fresh["rows"][0]["items"]} == {"First", "Second"}


def test_series_rows_and_categories(build: Builder, client: APIClient) -> None:
    from apps.catalog.models import CategoryKind  # noqa: PLC0415

    shows = build.category("shows", kind=CategoryKind.SERIES)
    build.series("Show", categories=[shows])
    rows = client.get("/api/v1/home").json()["rows"]
    row = next(row for row in rows if row["kind"] == "category")
    assert [item["type"] for item in row["items"]] == ["series"]
    assert Series.objects.count() == 1


def test_the_nightly_similar_titles_task(build: Builder) -> None:
    from apps.engagement import tasks  # noqa: PLC0415

    drama = build.genre("Drama")
    build.movie("One", genres=[drama])
    build.movie("Two", genres=[drama])
    assert tasks.compute_similar_titles() == {"movie": 2, "series": 0}
