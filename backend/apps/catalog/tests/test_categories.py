"""catalog.Category (SPEC §6) and GET /api/v1/admin/categories."""

from collections.abc import Callable
from typing import Any

import pytest
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.catalog.models import Category, CategoryKind
from apps.conftest import AdminFactory

pytestmark = pytest.mark.django_db

ADMIN = {"host": settings.ADMIN_HOST}
URL = "/api/v1/admin/categories"
type Capture = Callable[..., Any]


def make(kind: str, slug: str, sort: int = 0, **extra: Any) -> Category:
    return Category.objects.create(
        kind=kind, slug=slug, name_en=slug.title(), name_ar=slug, sort=sort, **extra
    )


def test_xtream_ids_come_from_a_sequence() -> None:
    first = make(CategoryKind.VOD, "action")
    second = make(CategoryKind.LIVE, "news")
    assert isinstance(first.xc_id, int)
    assert second.xc_id > first.xc_id
    assert str(first) == "vod:action"


def test_slugs_are_unique_per_kind() -> None:
    make(CategoryKind.VOD, "drama")
    make(CategoryKind.SERIES, "drama")
    with transaction.atomic(), pytest.raises(IntegrityError):
        make(CategoryKind.VOD, "drama")


def test_parents_share_the_kind() -> None:
    parent = make(CategoryKind.VOD, "movies")
    child = Category(kind=CategoryKind.LIVE, slug="x", name_en="X", name_ar="X", parent=parent)
    with pytest.raises(ValidationError):
        child.clean()
    parent.parent = parent
    with pytest.raises(ValidationError):
        parent.clean()
    Category(kind=CategoryKind.VOD, slug="y", name_en="Y", name_ar="Y", parent=parent).clean()


def test_list_filters_and_orders(
    owner_client: APIClient, django_assert_num_queries: Capture
) -> None:
    make(CategoryKind.VOD, "comedy", sort=20)
    make(CategoryKind.VOD, "action", sort=10)
    make(CategoryKind.LIVE, "news")
    with django_assert_num_queries(3):
        response = owner_client.get(URL, headers=ADMIN)
    assert [row["slug"] for row in response.json()["results"]] == ["news", "action", "comedy"]
    response = owner_client.get(f"{URL}?kind=vod&search=com", headers=ADMIN)
    assert [row["slug"] for row in response.json()["results"]] == ["comedy"]


def test_list_needs_customers_or_library_access(make_admin: AdminFactory) -> None:
    client = APIClient()
    client.force_authenticate(make_admin("content_manager"))
    assert client.get(URL, headers=ADMIN).status_code == 200
    client.force_authenticate(make_admin(permissions=["audit.view"]))
    assert client.get(URL, headers=ADMIN).status_code == 403


# --- Create, change, delete and reorder (library.manage) -----------------------------------------


def test_create_update_delete(owner_client: APIClient, owner: Any) -> None:
    response = owner_client.post(
        URL, {"kind": "vod", "name_en": "Science Fiction", "name_ar": "خيال علمي"}, headers=ADMIN
    )
    assert response.status_code == 201, response.json()
    body = response.json()
    assert (body["slug"], body["sort"]) == ("science-fiction", 10)
    pk = body["id"]
    second = owner_client.post(
        URL, {"kind": "vod", "name_en": "Drama", "name_ar": "دراما"}, headers=ADMIN
    )
    assert second.json()["sort"] == 20
    duplicate = owner_client.post(
        URL,
        {"kind": "vod", "name_en": "Other", "name_ar": "x", "slug": "science-fiction"},
        headers=ADMIN,
    )
    assert duplicate.status_code == 409
    response = owner_client.patch(f"{URL}/{pk}", {"name_en": "Sci-Fi"}, headers=ADMIN)
    assert response.json()["name_en"] == "Sci-Fi"
    assert owner_client.patch(f"{URL}/{pk}", {"kind": "live"}, headers=ADMIN).status_code == 400
    clash = owner_client.patch(f"{URL}/{pk}", {"slug": "drama"}, headers=ADMIN)
    assert clash.status_code == 409
    assert owner_client.get(f"{URL}/{pk}", headers=ADMIN).json()["name_en"] == "Sci-Fi"
    child = owner_client.post(
        URL, {"kind": "vod", "name_en": "Space", "name_ar": "فضاء", "parent": pk}, headers=ADMIN
    )
    assert child.status_code == 201
    assert owner_client.delete(f"{URL}/{pk}", headers=ADMIN).status_code == 409
    assert owner_client.delete(f"{URL}/{child.json()['id']}", headers=ADMIN).status_code == 204
    assert owner_client.delete(f"{URL}/{pk}", headers=ADMIN).status_code == 204
    actions = list(
        AuditLog.objects.filter(action__startswith="category.").values_list("action", flat=True)
    )
    assert actions.count("category.create") == 3
    assert "category.update" in actions
    assert actions.count("category.delete") == 2


def test_reorder(owner_client: APIClient) -> None:
    a = make(CategoryKind.VOD, "a", sort=10)
    b = make(CategoryKind.VOD, "b", sort=20)
    c = make(CategoryKind.VOD, "c", sort=30)
    live = make(CategoryKind.LIVE, "news")
    response = owner_client.post(
        f"{URL}/reorder", {"kind": "vod", "ids": [str(c.pk), str(a.pk)]}, headers=ADMIN
    )
    assert response.status_code == 200
    assert [row["slug"] for row in response.json()] == ["c", "a", "b"]
    assert [row["sort"] for row in response.json()] == [10, 20, 30]
    wrong = owner_client.post(
        f"{URL}/reorder", {"kind": "vod", "ids": [str(live.pk)]}, headers=ADMIN
    )
    assert wrong.status_code == 400
    assert AuditLog.objects.filter(action="category.reorder").count() == 1
    del b


def test_changes_need_library_manage(make_admin: AdminFactory) -> None:
    client = APIClient()
    client.force_authenticate(make_admin("viewer"))
    body = {"kind": "vod", "name_en": "X", "name_ar": "X"}
    assert client.post(URL, body, headers=ADMIN).status_code == 403
    category = make(CategoryKind.VOD, "x")
    assert client.get(f"{URL}/{category.pk}", headers=ADMIN).status_code == 200
    assert client.delete(f"{URL}/{category.pk}", headers=ADMIN).status_code == 403
    assert (
        client.post(f"{URL}/reorder", {"kind": "vod", "ids": []}, headers=ADMIN).status_code == 403
    )
