"""Customer search (SPEC §7.8): Meilisearch with the Arabic normaliser and the customer's
filters, incremental indexing, the atomic rebuild and the PostgreSQL fallback."""

from typing import Any
from uuid import uuid4

import pytest
from rest_framework.test import APIClient

from apps.accounts import services as accounts
from apps.accounts.models import User
from apps.catalog.tests.builders import Builder, episode, portal_client, refresh_entitlement
from apps.conftest import CustomerFactory
from apps.search import index, services, tasks
from apps.search.meili import MeiliClient, MeiliError, client
from apps.search.services import Filters

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("clean_stores")]


@pytest.fixture
def customer(make_customer: CustomerFactory) -> User:
    return make_customer()


@pytest.fixture
def api(customer: User) -> APIClient:
    return portal_client(customer)


@pytest.fixture
def catalogue(build: Builder) -> Builder:
    drama = build.genre("Drama", "دراما")
    build.movie(
        "The Matrix",
        title_ar="المصفوفة",
        genres=[drama],
        year=1999,
        cast=["Keanu Reeves"],
        overview="A hacker learns the truth.",
        overview_ar="مبرمج يكتشف الحقيقة.",
        popularity=50,
    )
    build.movie("Inception", title_ar="استهلال", year=2010, popularity=40)
    build.movie("Wadjda", title_ar="وجدة", year=2012)
    late = build.category("late", adult=True)
    build.movie("Matrix Nights", categories=[late])
    build.series("Breaking Bad", title_ar="بريكنج باد", seasons={1: 2})
    return build


def search(api: APIClient, query: str, **params: Any) -> dict[str, Any]:
    response = api.get("/api/v1/search", {"q": query, **params}, headers={"Accept-Language": "en"})
    assert response.status_code == 200, response.json()
    return dict(response.json())


def names(body: dict[str, Any]) -> list[str]:
    return [
        f"{hit['title']['title']}/{hit['episode']['number']}"
        if hit["episode"]
        else hit["title"]["title"]
        for hit in body["results"]
    ]


def test_arabic_and_english_queries_find_the_matrix(
    catalogue: Builder, api: APIClient, meili_index: str
) -> None:
    index.rebuild()
    for query in ("المصفوفة", "مصفوفه", "المَصْفُوفَة", "matrix", "The Matrx", "keanu"):
        body = search(api, query)
        assert body["engine"] == "meilisearch"
        assert names(body)[0] == "The Matrix", (query, names(body))
    assert "Matrix Nights" not in names(search(api, "matrix"))  # adult, not granted


def test_filters_type_genre_and_year(catalogue: Builder, api: APIClient, meili_index: str) -> None:
    index.rebuild()
    assert names(search(api, "breaking", type="series")) == ["Breaking Bad"]
    episodes = search(api, "breaking episode", type="episode")
    assert sorted(names(episodes)) == ["Breaking Bad/1", "Breaking Bad/2"]
    assert names(search(api, "matrix", year=1998)) == []
    genre = search(api, "matrix", type="movie")["results"][0]
    assert genre["type"] == "movie"


def test_results_follow_the_entitlement(
    catalogue: Builder, customer: User, api: APIClient, meili_index: str
) -> None:
    drama = catalogue.category("drama-cat")
    index.rebuild()
    accounts.update_access(customer, {"allow_series": False}, actor=None)
    refresh_entitlement(customer)
    assert search(api, "breaking")["results"] == []
    accounts.update_access(customer, {"allow_series": True}, category_ids=[drama.pk], actor=None)
    refresh_entitlement(customer)
    assert search(api, "matrix")["results"] == []


def test_people_are_found_by_name(catalogue: Builder, api: APIClient, meili_index: str) -> None:
    index.rebuild()
    assert [person["name"] for person in search(api, "keanu")["people"]] == ["Keanu Reeves"]


def test_incremental_indexing_follows_catalogue_changes(
    build: Builder, api: APIClient, meili_index: str
) -> None:
    movie = build.movie("Old Name")
    index.rebuild()
    movie.title = "Brand New Name"
    movie.save()
    tasks.index_titles("movie", [str(movie.pk)])
    assert names(search(api, "brand new")) == ["Brand New Name"]
    movie_id = movie.pk
    movie.delete()
    assert index.index_titles("movie", [movie_id]) == 0
    assert search(api, "brand new")["results"] == []


def test_series_reindex_drops_removed_episodes(
    build: Builder, api: APIClient, meili_index: str
) -> None:
    series = build.series("Show", seasons={1: 2})
    index.index_titles("series", [series.pk])
    episode(series, 1, 2).delete()
    index.index_titles("series", [series.pk])
    assert names(search(api, "show episode", type="episode")) == ["Show/1"]


def test_catalog_changes_queue_index_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    queued: list[tuple[str, list[str]]] = []
    rebuilds: list[int] = []
    monkeypatch.setattr(tasks.index_titles, "delay", lambda kind, ids: queued.append((kind, ids)))
    monkeypatch.setattr(tasks.rebuild_index, "apply_async", lambda **kw: rebuilds.append(1))
    pk = uuid4()
    tasks.on_catalog_changed(kind="movie", ids=(pk,))
    tasks.on_catalog_changed(kind="category", ids=(pk,))
    tasks.on_catalog_changed(kind="series", ids=())
    assert queued == [("movie", [str(pk)])]
    assert rebuilds == [1]  # debounced


def test_the_database_answers_while_meilisearch_is_down(
    catalogue: Builder, api: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(self: MeiliClient, uid: str, body: dict[str, Any]) -> dict[str, Any]:
        raise MeiliError("down")

    monkeypatch.setattr(MeiliClient, "search", broken)
    services.reset_breaker()
    body = search(api, "المصفوفة")
    assert body["engine"] == "database"
    assert names(body) == ["The Matrix"]
    assert names(search(api, "matrx"))[0] == "The Matrix"
    assert "Matrix Nights" not in names(search(api, "matrix"))
    assert search(api, "episode", type="episode")["results"] == []
    # The breaker keeps the database in use without retrying Meilisearch.
    monkeypatch.undo()
    assert search(api, "matrix")["engine"] == "database"
    services.reset_breaker()


def test_a_missing_index_falls_back_and_schedules_a_build(
    catalogue: Builder, customer: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(index, "INDEX", f"missing_{uuid4().hex}")
    scheduled: list[int] = []
    monkeypatch.setattr(tasks, "schedule_rebuild", lambda: scheduled.append(1))
    services.reset_breaker()
    refresh_entitlement(customer)
    from apps.catalog.browse import scope_for  # noqa: PLC0415

    result = services.search(scope_for(customer), "inception", Filters())
    assert result.engine == "database"
    assert [hit.title.title for hit in result.hits if hit.title] == ["Inception"]
    assert scheduled == [1]
    services.reset_breaker()


def test_queries_are_validated(api: APIClient) -> None:
    assert api.get("/api/v1/search").status_code == 400
    assert api.get("/api/v1/search", {"q": "x", "type": "person"}).status_code == 400
    empty = api.get("/api/v1/search", {"q": "!!!"}).json()
    assert empty["results"] == []


def test_the_scope_filter() -> None:
    from apps.catalog.browse import CustomerScope  # noqa: PLC0415

    everything = services.scope_filter(CustomerScope(), Filters())
    assert 'status = "ready"' in everything
    assert "is_adult = false" in everything
    assert 'type IN ["movie", "series", "episode"]' in everything
    granted = uuid4()
    narrow = services.scope_filter(
        CustomerScope(categories=frozenset({granted}), allow_series=False),
        Filters(type="series", year=2000),
    )
    assert f'category_ids IN ["{granted}"]' in narrow
    assert "type IN []" in narrow
    assert "year = 2000" in narrow


def test_meilisearch_errors_are_wrapped() -> None:
    broken = MeiliClient("http://127.0.0.1:9", "key", timeout=0.2)
    assert broken.healthy() is False
    with pytest.raises(MeiliError):
        broken.search("titles", {"q": "x"})
    assert client().healthy() is True


def test_the_reindex_command(catalogue: Builder, meili_index: str) -> None:
    from io import StringIO  # noqa: PLC0415

    from django.core.management import call_command  # noqa: PLC0415

    out = StringIO()
    call_command("search_reindex", stdout=out)
    assert "Search index rebuilt" in out.getvalue()
    assert "movie" in out.getvalue()


def test_the_rebuild_task_and_its_debounce(
    catalogue: Builder, meili_index: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert tasks.rebuild_index() == {"movie": 4, "series": 1, "episode": 2}
    queued: list[dict[str, Any]] = []
    monkeypatch.setattr(tasks.rebuild_index, "apply_async", lambda **kw: queued.append(kw))
    tasks.schedule_rebuild()
    tasks.schedule_rebuild()
    assert queued == [{"countdown": 10}]
