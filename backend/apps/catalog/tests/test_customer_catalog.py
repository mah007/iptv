"""The customer catalogue API (SPEC §9, §10 Catalog; ADR-0013): visibility by
entitlement, languages, artwork, cursor pagination and the title pages."""

from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts import services as accounts
from apps.accounts.models import User
from apps.catalog.models import CategoryKind, CreditRole, Movie, TitleStatus
from apps.catalog.tests.builders import (
    API,
    PORTAL,
    Builder,
    episode,
    portal_client,
    refresh_entitlement,
)
from apps.conftest import CustomerFactory
from apps.engagement.models import Favorite, Rating, Thumb, WatchProgress

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("clean_stores")]


@pytest.fixture
def customer(make_customer: CustomerFactory) -> User:
    return make_customer()


@pytest.fixture
def client(customer: User) -> APIClient:
    return portal_client(customer)


def titles(response: Any) -> list[str]:
    return [item["title"] for item in response.json()["results"]]


# --- Visibility -------------------------------------------------------------------------------


def test_lists_need_a_signed_in_customer(build: Builder, staff_user: User) -> None:
    build.movie("Visible")
    assert APIClient().get("/api/v1/movies", headers=PORTAL).status_code == 401
    staff = APIClient()
    staff.force_authenticate(staff_user)
    response = staff.get("/api/v1/movies", headers=PORTAL)
    assert response.status_code == 403
    assert response.json()["code"] == "PERMISSION_DENIED"


def test_only_ready_licensed_titles_show(build: Builder, client: APIClient) -> None:
    build.movie("Ready")
    build.movie("Processing", status=TitleStatus.PROCESSING)
    build.movie("Hidden", status=TitleStatus.HIDDEN)
    build.movie("Expired", license_expires_at=timezone.now() - timedelta(days=1))
    build.movie("Licensed", license_expires_at=timezone.now() + timedelta(days=1))
    response = client.get("/api/v1/movies?sort=title")
    assert response.status_code == 200
    assert titles(response) == ["Licensed", "Ready"]


def test_adult_titles_need_an_explicit_grant(
    build: Builder, customer: User, client: APIClient
) -> None:
    drama = build.category("drama")
    late = build.category("late", adult=True)
    build.movie("Family", categories=[drama])
    build.movie("Late night", categories=[drama, late])
    build.movie("Uncategorised")
    assert titles(client.get("/api/v1/movies?sort=title")) == ["Family", "Uncategorised"]

    accounts.update_access(customer, {}, category_ids=[drama.pk, late.pk], actor=None)
    refresh_entitlement(customer)
    assert titles(client.get("/api/v1/movies?sort=title")) == ["Family", "Late night"]

    accounts.update_access(customer, {}, category_ids=[drama.pk], actor=None)
    refresh_entitlement(customer)
    assert titles(client.get("/api/v1/movies?sort=title")) == ["Family"]


def test_content_kinds_follow_the_access_profile(
    build: Builder, customer: User, client: APIClient
) -> None:
    build.movie("A movie")
    build.series("A series")
    accounts.update_access(customer, {"allow_movies": False}, actor=None)
    refresh_entitlement(customer)
    assert client.get("/api/v1/movies").json()["results"] == []
    assert titles(client.get("/api/v1/series")) == ["A series"]


def test_series_need_a_playable_episode(build: Builder, client: APIClient) -> None:
    build.series("Playable")
    build.series("Not yet", unplayable=[(1, 1), (1, 2), (1, 3)])
    assert titles(client.get("/api/v1/series")) == ["Playable"]


def test_a_customer_without_an_access_profile_sees_nothing(
    build: Builder, customer: User, client: APIClient
) -> None:
    build.movie("Anything")
    customer.access.delete()
    refresh_entitlement(customer)
    assert client.get("/api/v1/movies").json()["results"] == []


# --- Languages and artwork --------------------------------------------------------------------


def test_arabic_from_accept_language_falls_back_to_english(
    build: Builder, client: APIClient
) -> None:
    build.movie("The Matrix", title_ar="المصفوفة")
    build.movie("Untranslated")
    response = client.get(
        "/api/v1/movies?sort=title", headers={"Accept-Language": "ar-SA,en;q=0.5"}
    )
    assert titles(response) == ["المصفوفة", "Untranslated"]
    assert response["Content-Language"] == "ar"
    assert "Accept-Language" in response["Vary"]
    english = client.get("/api/v1/movies?sort=title", headers={"Accept-Language": "en-US"})
    assert titles(english) == ["The Matrix", "Untranslated"]


def test_the_saved_locale_applies_without_a_header(
    build: Builder, customer: User, client: APIClient
) -> None:
    build.movie("The Matrix", title_ar="المصفوفة")
    customer.locale = "ar"
    customer.save()
    assert titles(client.get("/api/v1/movies")) == ["المصفوفة"]
    customer.locale = "en"
    customer.save()
    assert titles(client.get("/api/v1/movies")) == ["The Matrix"]


def test_cards_carry_webp_and_avif_artwork_with_blurhash(build: Builder, client: APIClient) -> None:
    build.movie("Poster")
    card = client.get("/api/v1/movies").json()["results"][0]
    poster = card["poster"]
    assert poster["url"].endswith(".webp")
    assert poster["url"].startswith("http")
    assert set(poster["sizes"]["w500"]) == {"webp", "avif"}
    assert poster["sizes"]["w185"]["avif"].endswith(".avif")
    assert poster["blurhash"]
    assert card["backdrop"]["url"].endswith(".webp")
    assert card["type"] == "movie"


# --- Lists ------------------------------------------------------------------------------------


def test_cursor_pagination_walks_every_title_once(
    build: Builder, customer: User, client: APIClient, django_assert_max_num_queries: Any
) -> None:
    for index in range(7):
        build.movie(f"Movie {index}", popularity=float(index))
    refresh_entitlement(customer)  # cached in redis-state, as in production
    seen: list[str] = []
    url: str | None = "/api/v1/movies?sort=popular&page_size=3"
    pages = 0
    while url:
        with django_assert_max_num_queries(2):
            body = client.get(url).json()
        seen += [item["title"] for item in body["results"]]
        url = body["next"]
        pages += 1
    assert pages == 3
    assert seen == [f"Movie {index}" for index in reversed(range(7))]


def test_sorting_by_rating_keeps_unrated_titles(build: Builder, client: APIClient) -> None:
    build.movie("Low", rating=3.0)
    build.movie("Unrated", rating=None)
    build.movie("High", rating=9.0)
    first = client.get("/api/v1/movies?sort=rating&page_size=2").json()
    rest = client.get(first["next"]).json()
    assert [item["title"] for item in first["results"] + rest["results"]] == [
        "High",
        "Low",
        "Unrated",
    ]


def test_filters_by_category_genre_and_year(build: Builder, client: APIClient) -> None:
    drama = build.category("drama")
    comedy = build.category("comedy")
    late = build.category("late", adult=True)
    funny = build.genre("Comedy", "كوميديا")
    build.movie("Old drama", categories=[drama], year=1990)
    build.movie("New drama", categories=[drama], year=2020)
    build.movie("Joke", categories=[comedy], genres=[funny], year=2020)
    assert titles(client.get(f"/api/v1/movies?category={drama.pk}&sort=title")) == [
        "New drama",
        "Old drama",
    ]
    assert titles(client.get(f"/api/v1/movies?genre={funny.pk}")) == ["Joke"]
    assert titles(client.get("/api/v1/movies?year=2020&sort=title")) == ["Joke", "New drama"]
    hidden = client.get(f"/api/v1/movies?category={late.pk}")
    assert hidden.status_code == 404
    bad = client.get("/api/v1/movies?year=soon")
    assert bad.status_code == 400
    assert bad.json()["code"] == "VALIDATION_ERROR"


def test_categories_and_genres_list_only_what_holds_titles(
    build: Builder, client: APIClient
) -> None:
    drama = build.category("drama", sort=2)
    action = build.category("action", sort=1)
    build.category("empty")
    build.category("dramas", kind=CategoryKind.SERIES)
    late = build.category("late", adult=True)
    funny = build.genre("Comedy", "كوميديا")
    build.genre("Unused")
    build.movie("Drama", categories=[drama], genres=[funny])
    build.movie("Action", categories=[action])
    build.movie("Adult", categories=[late])
    body = client.get("/api/v1/categories").json()
    assert [row["slug"] for row in body] == ["action", "drama"]
    assert client.get("/api/v1/categories?kind=series").json() == []
    assert client.get("/api/v1/categories?kind=live").status_code == 400
    genres = client.get("/api/v1/genres", headers={"Accept-Language": "ar"}).json()
    assert [row["name"] for row in genres] == ["كوميديا"]
    assert client.get("/api/v1/genres?kind=series").json() == []
    assert client.get("/api/v1/genres?kind=x").status_code == 400


# --- Title pages ------------------------------------------------------------------------------


def test_movie_page(build: Builder, customer: User, client: APIClient) -> None:
    drama = build.category("drama")
    late = build.category("late", adult=True)
    genre = build.genre("Drama", "دراما")
    movie = build.movie(
        "The Film",
        title_ar="الفيلم",
        categories=[drama, late],
        genres=[genre],
        cast=["Keanu Reeves", "Carrie-Anne Moss"],
        directors=["Lana Wachowski"],
    )
    accounts.update_access(customer, {}, category_ids=[drama.pk, late.pk], actor=None)
    refresh_entitlement(customer)
    other = build.movie("Similar", categories=[drama], genres=[genre])
    Favorite.objects.create(user=customer, movie=movie)
    Rating.objects.create(user=customer, movie=movie, value=Thumb.UP)
    WatchProgress.objects.create(
        user=customer, movie=movie, position_ms=60_000, duration_ms=5_400_000
    )
    body = client.get(f"/api/v1/movies/{movie.pk}", headers={"Accept-Language": "en"}).json()
    assert body["title"] == "The Film"
    assert [row["name"] for row in body["genres"]] == ["Drama"]
    assert {row["slug"] for row in body["categories"]} == {"drama", "late"}
    assert [row["name"] for row in body["cast"]] == ["Keanu Reeves", "Carrie-Anne Moss"]
    assert body["cast"][0]["character"] == "Hero"
    assert [row["name"] for row in body["directors"]] == ["Lana Wachowski"]
    assert body["quality"] == {"height": 1080, "badge": "FHD"}
    assert body["tracks"] == {"audio": ["eng"], "subtitles": ["ara"]}
    assert body["viewer"]["favorite"] is True
    assert body["viewer"]["rating"] == "up"
    assert body["viewer"]["progress"]["position_ms"] == 60_000
    assert [card["id"] for card in body["similar"]] == [str(other.pk)]
    assert "storage_key" not in str(body)


def test_movie_page_hides_what_the_customer_may_not_see(build: Builder, client: APIClient) -> None:
    late = build.category("late", adult=True)
    movie = build.movie("Adult", categories=[late])
    response = client.get(f"/api/v1/movies/{movie.pk}")
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


def test_quality_is_capped_by_the_plan(build: Builder, customer: User, client: APIClient) -> None:
    movie = build.movie("Film")
    accounts.update_access(customer, {"max_quality": 720}, actor=None)
    refresh_entitlement(customer)
    assert client.get(f"/api/v1/movies/{movie.pk}").json()["quality"] is None


def test_series_page_season_and_episode(
    build: Builder, customer: User, client: APIClient, django_assert_max_num_queries: Any
) -> None:
    series = build.series(
        "Show", title_ar="المسلسل", seasons={0: 1, 1: 2, 2: 2}, unplayable=[(2, 2)], cast=["Bryan"]
    )
    first = episode(series, 1, 1)
    WatchProgress.objects.create(
        user=customer,
        episode=first,
        series=series,
        position_ms=2_700_000,
        duration_ms=2_700_000,
        completed=True,
    )
    with django_assert_max_num_queries(25):
        body = client.get(f"/api/v1/series/{series.pk}").json()
    assert [(row["number"], row["episode_count"]) for row in body["seasons"]] == [
        (0, 1),
        (1, 2),
        (2, 1),
    ]
    assert body["next_episode"]["season_number"] == 1
    assert body["next_episode"]["number"] == 2
    assert body["seasons"][1]["poster"] is None

    season = client.get(f"/api/v1/series/{series.pk}/seasons/1", headers={"Accept-Language": "ar"})
    data = season.json()
    assert data["series"]["title"] == "المسلسل"
    assert [row["title"] for row in data["episodes"]] == ["الحلقة 1", "الحلقة 2"]
    assert data["episodes"][0]["progress"]["completed"] is True
    assert data["episodes"][0]["still"]["url"].endswith(".webp")
    assert client.get(f"/api/v1/series/{series.pk}/seasons/9").status_code == 404

    second = episode(series, 1, 2)
    detail = client.get(f"/api/v1/episodes/{second.pk}").json()
    assert detail["previous_id"] == str(first.pk)
    assert detail["next_id"] == str(episode(series, 2, 1).pk)
    assert detail["series"]["id"] == str(series.pk)
    special = client.get(f"/api/v1/episodes/{episode(series, 0, 1).pk}").json()
    assert special["previous_id"] is None
    assert client.get(f"/api/v1/episodes/{episode(series, 2, 2).pk}").status_code == 404


def test_a_new_viewer_starts_a_series_at_season_one(build: Builder, client: APIClient) -> None:
    series = build.series("Show", seasons={0: 1, 1: 1})
    body = client.get(f"/api/v1/series/{series.pk}").json()
    assert body["next_episode"]["season_number"] == 1


def test_people_pages_list_visible_credits(build: Builder, client: APIClient) -> None:
    late = build.category("late", adult=True)
    build.movie("Public", cast=["Actor"], popularity=1)
    build.movie("Popular", cast=["Actor"], popularity=5)
    build.movie("Adult", cast=["Actor", "Adult star"], categories=[late])
    actor = build.people["Actor"]
    body = client.get(f"/api/v1/people/{actor.pk}").json()
    assert body["name"] == "Actor"
    assert [row["title"]["title"] for row in body["known_for"]] == ["Popular", "Public"]
    assert body["known_for"][0]["role"] == CreditRole.CAST
    hidden = build.people["Adult star"]
    assert client.get(f"/api/v1/people/{hidden.pk}").status_code == 404


def test_the_api_host_needs_a_bearer_token(build: Builder, customer: User) -> None:
    build.movie("Film")
    client = APIClient()
    client.force_login(customer)
    # A session cookie is no credential on api.<domain>.
    assert client.get("/api/v1/movies", headers=API).status_code == 401
    assert client.get("/api/v1/movies", headers=PORTAL).status_code == 200


def test_search_text_is_kept_normalised(build: Builder) -> None:
    movie = build.movie("The Matrix!", title_ar="المصفوفة")
    assert movie.search_text == "matrix | مصفوفه"
    movie.title_ar = "مَصْفُوفَة"
    movie.save(update_fields=["title_ar"])
    movie.refresh_from_db()
    assert movie.search_text == "matrix | مصفوفه"
    assert Movie.objects.filter(search_text__contains="مصفوفه").exists()
