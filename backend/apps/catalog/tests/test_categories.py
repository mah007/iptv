"""catalog.Category (SPEC §6) and GET /api/v1/admin/categories."""

from collections.abc import Callable
from typing import Any

import pytest
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from rest_framework.test import APIClient

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
