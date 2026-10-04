"""Collections: admin CRUD and the customer page (SPEC §8.3, §10 collections/{slug})."""

import pytest
from django.conf import settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.catalog.models import Collection, CollectionItem
from apps.catalog.tests.builders import Builder, portal_client
from apps.conftest import AdminFactory, CustomerFactory

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("clean_stores")]

ADMIN = {"host": settings.ADMIN_HOST}
URL = "/api/v1/admin/collections"


def test_create_reorder_and_delete_a_collection(build: Builder, owner_client: APIClient) -> None:
    movie = build.movie("Film")
    series = build.series("Show")
    created = owner_client.post(
        URL,
        {
            "name_en": "Ramadan Picks",
            "name_ar": "مختارات رمضان",
            "items": [
                {"type": "series", "id": str(series.pk)},
                {"type": "movie", "id": str(movie.pk)},
            ],
        },
        format="json",
        headers=ADMIN,
    )
    assert created.status_code == 201, created.json()
    body = created.json()
    assert body["slug"] == "ramadan-picks"
    assert [item["title"] for item in body["items"]] == ["Show", "Film"]
    assert body["items"][0]["poster"]["url"].endswith(".webp")
    assert AuditLog.objects.filter(action="collection.create").exists()

    pk = body["id"]
    patched = owner_client.patch(
        f"{URL}/{pk}",
        {"items": [{"type": "movie", "id": str(movie.pk)}], "published": False},
        format="json",
        headers=ADMIN,
    )
    assert patched.status_code == 200
    assert [item["title"] for item in patched.json()["items"]] == ["Film"]
    assert patched.json()["published"] is False
    assert owner_client.get(URL, headers=ADMIN).json()["results"][0]["id"] == pk

    assert owner_client.delete(f"{URL}/{pk}", headers=ADMIN).status_code == 204
    assert not Collection.objects.exists()
    assert not CollectionItem.objects.exists()


def test_collection_validation(build: Builder, owner_client: APIClient) -> None:
    Collection.objects.create(slug="taken", name_en="Taken", name_ar="م")
    clash = owner_client.post(
        URL, {"slug": "taken", "name_en": "Other", "name_ar": "آخر"}, format="json", headers=ADMIN
    )
    assert clash.status_code == 409
    unknown = owner_client.post(
        URL,
        {
            "name_en": "Ghosts",
            "name_ar": "أشباح",
            "items": [{"type": "movie", "id": "01a10681-0000-7000-8000-000000000000"}],
        },
        format="json",
        headers=ADMIN,
    )
    assert unknown.status_code == 400
    assert unknown.json()["field_error_codes"]["items"] == ["does_not_exist"]


def test_collection_permissions(make_admin: AdminFactory) -> None:
    viewer = APIClient()
    viewer.force_authenticate(make_admin("viewer"))
    assert viewer.get(URL, headers=ADMIN).status_code == 200
    denied = viewer.post(URL, {"name_en": "X", "name_ar": "Y"}, format="json", headers=ADMIN)
    assert denied.status_code == 403


def test_the_customer_page_shows_visible_items_in_order(
    build: Builder, make_customer: CustomerFactory
) -> None:
    late = build.category("late", adult=True)
    first = build.movie("First")
    second = build.series("Second")
    hidden = build.movie("Hidden", categories=[late])
    collection = Collection.objects.create(
        slug="picks", name_en="Picks", name_ar="مختارات", description_ar="وصف"
    )
    CollectionItem.objects.create(collection=collection, movie=hidden, sort=1)
    CollectionItem.objects.create(collection=collection, series=second, sort=2)
    CollectionItem.objects.create(collection=collection, movie=first, sort=3)
    customer: User = make_customer()
    client = portal_client(customer)
    body = client.get("/api/v1/collections/picks", headers={"Accept-Language": "ar"}).json()
    assert body["name"] == "مختارات"
    assert body["description"] == "وصف"
    assert [card["title"] for card in body["items"]] == ["Second", "First"]
    collection.published = False
    collection.save()
    assert client.get("/api/v1/collections/picks").status_code == 404
