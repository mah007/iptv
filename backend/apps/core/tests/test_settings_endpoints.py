"""The settings endpoints CFG added (ADR-0019): reset, moving a `.env` value in, bulk
changes, logo uploads and the public branding."""

import io
from typing import Any
from unittest import mock

import pytest
from django.conf import settings
from PIL import Image
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.core.models import Setting
from apps.core.services import get_secret, get_setting, set_setting

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("no_server_config")]

ADMIN = {"host": settings.ADMIN_HOST}
LIST_URL = "/api/v1/admin/settings"
PASSWORD = "smtp-endpoint-Pa55!"  # noqa: S105 (a fake secret)


def url(key: str, action: str = "") -> str:
    return f"{LIST_URL}/{key}" + (f"/{action}" if action else "")


@pytest.fixture
def staff_client(staff_user: User) -> APIClient:
    client = APIClient()
    client.force_login(staff_user)
    return client


def png(size: tuple[int, int] = (40, 20)) -> bytes:
    out = io.BytesIO()
    Image.new("RGBA", size, (15, 118, 110, 255)).save(out, format="PNG")
    return out.getvalue()


def test_delete_forgets_the_stored_value(staff_client: APIClient, staff_user: User) -> None:
    set_setting("xtream.port", 8080, actor=staff_user)
    response = staff_client.delete(url("xtream.port"), headers=ADMIN)
    assert response.status_code == 200
    body = response.json()
    assert (body["value"], body["source"], body["is_default"]) == (80, "default", True)
    assert not Setting.objects.exists()
    assert AuditLog.objects.filter(action="setting.reset").count() == 1
    assert staff_client.delete(url("xtream.nope"), headers=ADMIN).status_code == 404


def test_adopt_moves_a_server_config_value_in(staff_client: APIClient, settings: Any) -> None:
    settings.EMAIL_HOST_PASSWORD = PASSWORD
    response = staff_client.post(url("email.smtp_password", "adopt"), headers=ADMIN)
    assert response.status_code == 200
    assert response.json()["source"] == "database"
    assert PASSWORD not in response.content.decode()
    settings.EMAIL_HOST_PASSWORD = None
    assert get_secret("email.smtp_password") == PASSWORD


def test_adopt_without_a_server_config_value_is_a_validation_problem(
    staff_client: APIClient,
) -> None:
    response = staff_client.post(url("email.smtp_password", "adopt"), headers=ADMIN)
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["field_error_codes"] == {"value": ["no_server_config"]}


def test_bulk_patch_changes_several_settings(staff_client: APIClient) -> None:
    response = staff_client.patch(
        LIST_URL,
        {
            "values": {
                "email.smtp_host": "mailpit",
                "email.smtp_port": 1025,
                "email.smtp_security": "none",
                "email.smtp_password": PASSWORD,
            }
        },
        headers=ADMIN,
    )
    assert response.status_code == 200
    entries = {entry["key"]: entry for entry in response.json()}
    assert entries["email.smtp_port"]["value"] == 1025
    assert entries["email.smtp_password"]["value"] is None
    assert entries["email.smtp_password"]["is_set"] is True
    assert PASSWORD not in response.content.decode()
    assert get_setting("email.smtp_host") == "mailpit"
    reset = staff_client.patch(LIST_URL, {"values": {"email.smtp_password": None}}, headers=ADMIN)
    assert reset.json()[0]["is_set"] is False


def test_bulk_patch_reports_errors_per_key_and_stores_nothing(staff_client: APIClient) -> None:
    response = staff_client.patch(
        LIST_URL,
        {"values": {"email.smtp_host": "mailpit", "email.smtp_port": 0}},
        headers=ADMIN,
    )
    assert response.status_code == 400
    assert response.json()["field_errors"] == {"values.email.smtp_port": ["Must be at least 1."]}
    assert not Setting.objects.exists()
    unknown = staff_client.patch(LIST_URL, {"values": {"email.nope": 1}}, headers=ADMIN)
    assert unknown.status_code == 404


def test_writes_need_settings_edit(make_admin: Any) -> None:
    client = APIClient()
    client.force_login(make_admin(permissions=["settings.view"]))
    assert client.get(LIST_URL, headers=ADMIN).status_code == 200
    assert client.patch(LIST_URL, {"values": {"xtream.port": 81}}, headers=ADMIN).status_code == 403
    assert client.delete(url("xtream.port"), headers=ADMIN).status_code == 403
    assert client.post(url("email.smtp_password", "adopt"), headers=ADMIN).status_code == 403


def test_a_logo_upload_is_checked_and_queued(
    staff_client: APIClient, django_capture_on_commit_callbacks: Any
) -> None:
    with (
        mock.patch("apps.core.tasks.store_branding_image.delay") as delay,
        django_capture_on_commit_callbacks(execute=True),
    ):
        response = staff_client.post(
            url("branding.logo_light", "image"),
            {"file": io.BytesIO(png())},
            format="multipart",
            headers=ADMIN,
        )
    assert response.status_code == 202
    (request,) = delay.call_args.args
    assert request["key"] == "branding.logo_light"
    assert AuditLog.objects.filter(action="setting.image_upload").exists()


@pytest.mark.parametrize(
    ("key", "content", "code"),
    [
        ("branding.logo_light", b"<svg xmlns='http://www.w3.org/2000/svg'/>", "unsupported_format"),
        ("branding.logo_light", b"<html><script>alert(1)</script></html>", "unsupported_format"),
        ("branding.accent_color", b"", "not_an_image_setting"),
    ],
)
def test_other_uploads_are_refused(
    staff_client: APIClient, key: str, content: bytes, code: str
) -> None:
    upload = io.BytesIO(content or png())
    upload.name = "logo.svg"
    with mock.patch("apps.core.tasks.store_branding_image.delay") as delay:
        response = staff_client.post(
            url(key, "image"), {"file": upload}, format="multipart", headers=ADMIN
        )
    assert response.status_code == 400
    assert response.json()["field_error_codes"] == {"file": [code]}
    delay.assert_not_called()


def test_branding_is_public_on_every_app_host(api_client: APIClient, staff_user: User) -> None:
    set_setting("branding.support_phone", "+966501234567", actor=staff_user)
    set_setting(
        "branding.logo_light", "images/branding/logo-light-0123456789ab.png", actor=staff_user
    )
    paths = {
        settings.ADMIN_HOST: "/api/v1/branding",
        settings.APP_HOST: "/api/v1/branding",
        settings.API_HOST: "/api/v1/branding",
    }
    for host, path in paths.items():
        response = api_client.get(path, headers={"host": host})
        assert response.status_code == 200, host
        assert response["Cache-Control"] == "public, max-age=60"
        body = response.json()
        assert body["service_name"] == {"en": "Smart IPTV", "ar": "سمارت IPTV"}
        assert body["support"]["phone"] == "+966501234567"
        assert body["default_language"] == "ar"
        logo = f"{settings.MEDIA_BASE_URL}/images/branding/logo-light-0123456789ab.png"
        assert body["logo_light_url"] == logo
        assert body["logo_dark_url"] == logo  # the light logo when no dark one is set
