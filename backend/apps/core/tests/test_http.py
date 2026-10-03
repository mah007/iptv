from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory

from apps.accounts.models import User
from apps.core.http import acting_user, client_ip


@pytest.mark.parametrize(
    ("forwarded", "remote", "expected"),
    [
        (None, "10.0.0.2", "10.0.0.2"),
        ("203.0.113.7", "172.18.0.3", "203.0.113.7"),
        # Traefik appends the address it saw; anything earlier came from the client.
        ("1.2.3.4, 203.0.113.7", "172.18.0.3", "203.0.113.7"),
        ("2001:DB8::1", "172.18.0.3", "2001:db8::1"),
        ("not-an-ip", "172.18.0.3", None),
        (None, "", None),
    ],
)
def test_client_ip(forwarded: str | None, remote: str, expected: str | None) -> None:
    headers = {"x-forwarded-for": forwarded} if forwarded is not None else {}
    request = RequestFactory().get("/", headers=headers, REMOTE_ADDR=remote)
    assert client_ip(request) == expected


@pytest.mark.django_db
def test_acting_user(staff_user: User) -> None:
    request = RequestFactory().get("/")
    assert acting_user(request) is None
    request.user = AnonymousUser()
    assert acting_user(request) is None
    request.user = staff_user
    assert acting_user(request) is staff_user
    assert acting_user(SimpleNamespace(user="not a user")) is None  # type: ignore[arg-type]
