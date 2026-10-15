"""Stable codes on the settings, library and catalog errors, so the admin shows them in
Arabic too (ADR-0015 follow-up; ADR-0023)."""

from collections.abc import Callable
from typing import Any

import pytest
from django.conf import settings
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.core import registry
from apps.library.models import Library

pytestmark = pytest.mark.django_db
ADMIN = {"host": settings.ADMIN_HOST}


@pytest.mark.parametrize(
    ("key", "value", "code"),
    [
        ("branding.accent_color", "teal", "invalid_color"),
        ("billing.currency", "riyal", "invalid_currency"),
        ("metadata.certification_country", "KSA", "invalid_country"),
        ("metadata.tmdb_language", "english", "invalid_language_tag"),
        ("metadata.tvdb_rate_limit_per_s", 0, "min_value"),
        ("metadata.tvdb_rate_limit_per_s", 51, "max_value"),
        ("metadata.tvdb_rate_limit_per_s", "ten", "invalid_type"),
        ("security.admin_ip_allowlist", "not-an-ip", "invalid_network"),
        ("security.csp_extra_media_origins", "ftp://x", "invalid_origin"),
        ("branding.time_zone", "Mars/Olympus", "invalid_timezone"),
        ("metadata.genre_category_map", "{", "invalid_genre_map"),
    ],
)
def test_registry_validators_raise_their_own_codes(key: str, value: Any, code: str) -> None:
    with pytest.raises(ValidationError) as raised:
        registry.get_definition(key).clean(value)
    assert raised.value.error_list[0].code == code


def test_a_bulk_save_keeps_every_code(owner_client: APIClient) -> None:
    response = owner_client.patch(
        "/api/v1/admin/settings",
        {"values": {"branding.accent_color": "teal", "metadata.tvdb_rate_limit_per_s": 99}},
        format="json",
        headers=ADMIN,
    )
    assert response.status_code == 400
    assert response.json()["field_error_codes"] == {
        "values.branding.accent_color": ["invalid_color"],
        "values.metadata.tvdb_rate_limit_per_s": ["max_value"],
    }


def test_library_and_category_errors_have_codes(
    owner_client: APIClient, make_library: Callable[..., Library]
) -> None:
    library = make_library()
    missing = owner_client.post(
        "/api/v1/admin/libraries",
        {"name": "Other", "kind": "movies", "path": "no-such-folder"},
        format="json",
        headers=ADMIN,
    )
    assert missing.status_code == 400
    assert missing.json()["field_error_codes"] == {"path": ["path_missing"]}
    taken = owner_client.post(
        "/api/v1/admin/libraries",
        {"name": library.name, "kind": "movies", "path": library.path},
        format="json",
        headers=ADMIN,
    )
    assert taken.status_code == 409
    codes = taken.json()["field_error_codes"]
    assert codes in ({"path": ["path_overlaps"]}, {"name": ["name_taken"]})
    first = owner_client.post(
        "/api/v1/admin/categories",
        {"kind": "vod", "name_en": "Drama", "name_ar": "دراما", "slug": "drama"},
        format="json",
        headers=ADMIN,
    )
    assert first.status_code == 201
    again = owner_client.post(
        "/api/v1/admin/categories",
        {"kind": "vod", "name_en": "Drama 2", "name_ar": "دراما ٢", "slug": "drama"},
        format="json",
        headers=ADMIN,
    )
    assert again.status_code == 409
    assert again.json()["field_error_codes"] == {"slug": ["slug_taken"]}
    kind = owner_client.patch(
        f"/api/v1/admin/categories/{first.json()['id']}",
        {"kind": "series"},
        format="json",
        headers=ADMIN,
    )
    assert kind.status_code == 400
    assert kind.json()["field_error_codes"] == {"kind": ["kind_immutable"]}
