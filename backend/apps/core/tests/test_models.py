import pytest
from django.contrib.auth import get_user_model
from django.db import connection

pytestmark = pytest.mark.django_db


def test_user_gets_a_uuid7_primary_key_and_timestamps() -> None:
    user = get_user_model().objects.create_user(username="mahmoud", password="x")  # noqa: S106
    assert user.pk.version == 7
    assert user.created_at is not None
    assert user.updated_at >= user.created_at
    assert str(user) == "mahmoud"


def test_required_postgres_extensions_are_installed() -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT extname FROM pg_extension")
        installed = {row[0] for row in cursor.fetchall()}
    assert {"pg_trgm", "unaccent", "btree_gist"} <= installed
