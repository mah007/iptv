"""Logos the admin uploads (CFG, ADR-0019): checked, re-encoded and stored by the
worker under a content-hashed name, and shown in emails and invoices."""

import base64
import io
from pathlib import Path

import pytest
from django.core.exceptions import ValidationError
from PIL import Image

from apps.accounts.models import User
from apps.core import branding, tasks
from apps.core.services import get_setting

pytestmark = pytest.mark.django_db


def image(fmt: str, size: tuple[int, int] = (1200, 400)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(out, format=fmt)
    return out.getvalue()


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "WEBP"])
def test_logos_are_stored_as_small_pngs(data_root: Path, staff_user: User, fmt: str) -> None:
    name = branding.store_logo("branding.logo_light", image(fmt), actor=staff_user, ip=None)
    assert name.startswith("images/branding/logo-light-")
    assert get_setting("branding.logo_light") == name
    with Image.open(data_root / name) as stored:
        assert stored.format == "PNG"
        assert max(stored.size) == branding.LOGO_MAX_SIDE


def test_a_new_logo_replaces_the_old_file(data_root: Path, staff_user: User) -> None:
    first = branding.store_logo("branding.logo_dark", image("PNG"), actor=staff_user, ip=None)
    second = branding.store_logo(
        "branding.logo_dark", image("PNG", (300, 300)), actor=staff_user, ip=None
    )
    assert first != second
    assert not (data_root / first).exists()
    assert (data_root / second).is_file()


@pytest.mark.parametrize(
    "content",
    [b"GIF89a" + b"\x00" * 20, b"<svg xmlns='http://www.w3.org/2000/svg'/>", b"not an image"],
)
def test_anything_else_is_refused(content: bytes) -> None:
    with pytest.raises(ValidationError):
        branding.check_logo(content)


def test_oversized_uploads_are_refused() -> None:
    with pytest.raises(ValidationError, match="2 MB"):
        branding.check_logo(b"x" * (branding.LOGO_MAX_BYTES + 1))


def test_the_worker_task_stores_the_logo(data_root: Path, staff_user: User) -> None:
    tasks.store_branding_image(
        {
            "key": "branding.logo_light",
            "image_b64": base64.b64encode(image("PNG")).decode(),
            "actor_id": str(staff_user.pk),
            "ip": "203.0.113.5",
        }
    )
    name = str(get_setting("branding.logo_light"))
    assert (data_root / name).is_file()
    tasks.delete_branding_image(name)
    assert not (data_root / name).exists()


def test_a_rejected_task_upload_is_logged_not_raised(
    data_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    tasks.store_branding_image(
        {"key": "branding.logo_light", "image_b64": base64.b64encode(b"nope").decode()}
    )
    assert get_setting("branding.logo_light") == ""
    assert "branding.logo_rejected" in caplog.text


def test_files_stay_inside_the_branding_folder() -> None:
    with pytest.raises(ValueError, match="images/branding"):
        branding.delete_logo_file("images/movie/1/poster/w500.webp")
    with pytest.raises(ValueError, match="images/branding"):
        branding.delete_logo_file("images/branding/../../etc/passwd")


def test_emails_and_invoices_show_the_logo(staff_user: User) -> None:
    from apps.core.services import set_setting  # noqa: PLC0415
    from apps.notifications.rendering import wrap_html  # noqa: PLC0415
    from apps.notifications.services import common_context  # noqa: PLC0415

    assert branding.logo_url() == ""
    set_setting(
        "branding.logo_light", "images/branding/logo-light-0123456789ab.png", actor=staff_user
    )
    context = common_context(None, "en")
    assert context["logo_url"].endswith("/images/branding/logo-light-0123456789ab.png")
    html = wrap_html("<p>Hi</p>", title="T", locale="en", context=context)
    assert f'<img src="{context["logo_url"]}"' in html
    plain = wrap_html("<p>Hi</p>", title="T", locale="en", context={**context, "logo_url": ""})
    assert "<img" not in plain
