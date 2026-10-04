"""The signed-in customer and their devices (SPEC §9 Account, §10 Me; ADR-0013)."""

from typing import Any

import pytest
from django.conf import settings
from rest_framework.test import APIClient

from apps.accounts import customer_auth, services
from apps.accounts.credentials import verify_password
from apps.accounts.models import Device, DeviceKind, User, XtreamCredential
from apps.audit.models import AuditLog
from apps.conftest import CustomerFactory

pytestmark = pytest.mark.django_db

PORTAL = {"host": settings.APP_HOST}


@pytest.fixture
def customer(make_customer: CustomerFactory) -> User:
    return make_customer(name="Omar", max_devices=2)


@pytest.fixture
def client(customer: User) -> APIClient:
    client = APIClient()
    client.force_login(customer)
    client.defaults["HTTP_HOST"] = PORTAL["host"]
    return client


def test_me_and_its_access_summary(customer: User, client: APIClient) -> None:
    body = client.get("/api/v1/me").json()
    assert body["username"] == customer.username
    assert body["name"] == "Omar"
    assert body["access"] == {
        "status": "active",
        "expires_at": None,
        "max_streams": 1,
        "max_devices": 2,
        "max_quality": 1080,
        "allow_movies": True,
        "allow_series": True,
        "allow_live": True,
    }


def test_customers_change_their_profile_but_not_their_email(
    customer: User, client: APIClient
) -> None:
    response = client.patch(
        "/api/v1/me",
        {"name": "Omar K", "locale": "en", "timezone": "Europe/London", "email": "x@evil.test"},
    )
    assert response.status_code == 200, response.json()
    customer.refresh_from_db()
    assert (customer.name, customer.locale, customer.timezone) == ("Omar K", "en", "Europe/London")
    assert customer.email != "x@evil.test"
    entry = AuditLog.objects.get(action="customer.update")
    assert entry.actor == customer
    bad = client.patch("/api/v1/me", {"timezone": "Mars/Base"})
    assert bad.status_code == 400
    assert "timezone" in bad.json()["field_errors"]


def test_add_a_tv_app_shows_the_credentials_once(customer: User, client: APIClient) -> None:
    response = client.post(
        "/api/v1/me/devices", {"name": "Bedroom TV", "app_hint": "tivimate"}, format="json"
    )
    assert response.status_code == 201, response.json()
    assert response["Cache-Control"] == "no-store"
    body = response.json()
    assert body["device"]["name"] == "Bedroom TV"
    assert body["device"]["kind"] == "xtream"
    assert body["server_url"].startswith("http")
    credential = XtreamCredential.objects.get(username=body["username"])
    assert verify_password(credential.password_hash, body["password"])
    listed = client.get("/api/v1/me/devices").json()
    assert [row["name"] for row in listed] == ["Bedroom TV"]
    assert "password" not in listed[0]


def test_customers_may_choose_their_iptv_login(customer: User, client: APIClient) -> None:
    response = client.post(
        "/api/v1/me/devices",
        {"name": "TV", "username": "omar.tv", "password": "s3cret-pass"},
        format="json",
    )
    assert response.status_code == 201, response.json()
    assert response.json()["username"] == "omar.tv"
    taken = client.post("/api/v1/me/devices", {"username": "OMAR.TV"}, format="json")
    assert taken.status_code == 400
    assert taken.json()["field_error_codes"]["username"] == ["username_taken"]


def test_only_iptv_devices_count_towards_the_limit(customer: User, client: APIClient) -> None:
    Device.objects.create(user=customer, kind=DeviceKind.WEB, name="Firefox")
    Device.objects.create(user=customer, kind=DeviceKind.APP, name="Phone")
    assert client.post("/api/v1/me/devices", {}, format="json").status_code == 201
    assert client.post("/api/v1/me/devices", {}, format="json").status_code == 201
    third = client.post("/api/v1/me/devices", {}, format="json")
    assert third.status_code == 409
    assert third.json()["code"] == "DEVICE_LIMIT"


def test_rename_reset_and_remove_a_device(customer: User, client: APIClient) -> None:
    issued = services.create_device_credential(customer, name="Old", actor=None)
    url = f"/api/v1/me/devices/{issued.device.pk}"
    renamed = client.patch(url, {"name": "Kitchen", "app_hint": "smarters"}, format="json")
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Kitchen"
    assert AuditLog.objects.filter(action="device.update", actor=customer).exists()
    assert client.patch(url, {}, format="json").json()["name"] == "Kitchen"

    reset = client.post(f"{url}/xtream-credentials", {}, format="json")
    assert reset.status_code == 200
    assert reset["Cache-Control"] == "no-store"
    credential = XtreamCredential.objects.get(device=issued.device)
    assert verify_password(credential.password_hash, reset.json()["password"])
    assert not verify_password(credential.password_hash, issued.password)

    assert client.delete(url).status_code == 204
    issued.device.refresh_from_db()
    assert issued.device.revoked_at is not None
    assert client.get("/api/v1/me/devices").json() == []
    assert client.patch(url, {"name": "Gone"}, format="json").status_code == 404


def test_browser_devices_have_no_xtream_credentials(customer: User, client: APIClient) -> None:
    web = Device.objects.create(user=customer, kind=DeviceKind.WEB, name="Firefox")
    response = client.post(f"/api/v1/me/devices/{web.pk}/xtream-credentials", {}, format="json")
    assert response.status_code == 409


def test_the_current_browser_is_flagged(customer: User, client: APIClient) -> None:
    web = Device.objects.create(user=customer, kind=DeviceKind.WEB, name="Firefox")
    other = Device.objects.create(user=customer, kind=DeviceKind.WEB, name="Chrome")
    session = client.session
    session[customer_auth.SESSION_DEVICE_KEY] = str(web.pk)
    session.save()
    rows = {row["id"]: row["current"] for row in client.get("/api/v1/me/devices").json()}
    assert rows == {str(web.pk): True, str(other.pk): False}


def test_other_customers_devices_are_invisible(
    customer: User, client: APIClient, make_customer: CustomerFactory
) -> None:
    stranger = make_customer()
    issued = services.create_device_credential(stranger, name="Theirs", actor=None)
    url = f"/api/v1/me/devices/{issued.device.pk}"
    for call in (
        lambda: client.patch(url, {"name": "Mine"}, format="json"),
        lambda: client.delete(url),
        lambda: client.post(f"{url}/xtream-credentials", {}, format="json"),
    ):
        response: Any = call()
        assert response.status_code == 404
