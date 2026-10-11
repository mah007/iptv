"""Branding the admin sets (CFG, ADR-0019): logos, names and contacts for every app.

Logos are uploaded through the admin's Settings (`POST admin/settings/<key>/image`):
web checks the file, the worker (which mounts the media volume) re-encodes it to a
PNG of at most 512 px without metadata, stores it under a content-hashed name in
`images/branding/` (served by the edge, cacheable forever) and points the setting
at it. PNG because email clients don't all show WebP or AVIF.
"""

import base64
import hashlib
import io
import os
import tempfile
from pathlib import Path
from typing import Any, Final

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from PIL import Image, UnidentifiedImageError

from apps.accounts.models import User
from apps.audit import services as audit
from apps.audit.services import AuditTarget
from apps.core import registry
from apps.core.serializers import media_url
from apps.core.services import SETTING_AUDIT_TARGET, get_setting, set_setting

LOGO_MAX_BYTES: Final = 2 * 1024 * 1024
LOGO_MAX_SIDE: Final = 512
#: Refuse absurd dimensions before decoding (decompression bombs).
_MAX_SOURCE_PIXELS: Final = 40_000_000
_FORMATS: Final = frozenset({"PNG", "JPEG", "WEBP"})
BRANDING_DIR: Final = "images/branding"


def _invalid(message: str, code: str) -> ValidationError:
    return ValidationError(message, code=code)


def check_image_key(key: str) -> registry.SettingDef:
    """The definition of an image setting. Raises UnknownSettingError or ValidationError."""
    definition = registry.get_definition(key)
    if definition.widget != "image":
        msg = "This setting is not an image."
        raise _invalid(msg, "not_an_image_setting")
    return definition


def check_logo(data: bytes) -> None:
    """Refuse anything but a reasonable PNG, JPEG or WebP. Raises ValidationError."""
    if len(data) > LOGO_MAX_BYTES:
        msg = "The image is too large (2 MB at most)."
        raise _invalid(msg, "too_large")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in _FORMATS:
                msg = "Upload a PNG, JPEG or WebP image."
                raise _invalid(msg, "unsupported_format")
            width, height = image.size
            if width * height > _MAX_SOURCE_PIXELS:
                msg = "The image is too large."
                raise _invalid(msg, "too_large")
            image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError):
        msg = "Upload a PNG, JPEG or WebP image."
        raise _invalid(msg, "unsupported_format") from None


def queue_logo(key: str, data: bytes, *, actor: User | None, ip: str | None) -> None:
    """Check an upload and queue storing it (the worker mounts the media volume)."""
    check_image_key(key)
    check_logo(data)
    from apps.core import tasks  # noqa: PLC0415 (tasks import this module)

    request = {
        "key": key,
        "image_b64": base64.b64encode(data).decode(),
        "actor_id": str(actor.pk) if actor is not None else "",
        "ip": ip or "",
    }
    with transaction.atomic():
        audit.record(
            "setting.image_upload",
            actor=actor,
            target=AuditTarget(SETTING_AUDIT_TARGET, key),
            after={"bytes": len(data)},
            ip=ip,
        )
        transaction.on_commit(lambda: tasks.store_branding_image.delay(request))


def render_logo(data: bytes) -> bytes:
    """The upload as an optimised PNG of at most LOGO_MAX_SIDE px, without metadata."""
    with Image.open(io.BytesIO(data)) as source:
        image = source.convert("RGBA")
    image.thumbnail((LOGO_MAX_SIDE, LOGO_MAX_SIDE))
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()


def _path(key: str) -> Path:
    if not key.startswith(f"{BRANDING_DIR}/") or ".." in key.split("/"):
        msg = "branding files live in images/branding/"
        raise ValueError(msg)
    return Path(settings.DATA_ROOT) / key


def _write(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=target.parent, prefix=".tmp-")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        Path(temporary).chmod(0o644)
        Path(temporary).replace(target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def delete_logo_file(key: str) -> None:
    """Remove a stored logo file (after it was replaced or the setting reset)."""
    if key:
        _path(key).unlink(missing_ok=True)


def store_logo(key: str, data: bytes, *, actor: User | None, ip: str | None) -> str:
    """Re-encode, store and point the setting at the logo; returns the stored name."""
    check_image_key(key)
    check_logo(data)
    png = render_logo(data)
    variant = key.rsplit("_", 1)[-1]  # branding.logo_light -> light
    name = f"{BRANDING_DIR}/logo-{variant}-{hashlib.sha256(png).hexdigest()[:12]}.png"
    _write(_path(name), png)
    previous = str(get_setting(key))
    set_setting(key, name, actor=actor, ip=ip)
    if previous and previous != name:
        delete_logo_file(previous)
    return name


def public_branding() -> dict[str, Any]:
    """What every app shows: names, colour, logos, support contacts, default language."""

    def logo(key: str) -> str | None:
        value = str(get_setting(key))
        return media_url(value) if value else None

    return {
        "service_name": {
            "en": str(get_setting("branding.service_name_en")),
            "ar": str(get_setting("branding.service_name_ar")),
        },
        "accent_color": str(get_setting("branding.accent_color")),
        "logo_light_url": logo("branding.logo_light"),
        "logo_dark_url": logo("branding.logo_dark") or logo("branding.logo_light"),
        "support": {
            "email": str(get_setting("branding.support_email")),
            "phone": str(get_setting("branding.support_phone")),
            "whatsapp": str(get_setting("branding.support_whatsapp")),
        },
        "default_language": str(get_setting("branding.default_language")),
    }


def logo_url() -> str:
    """The light logo's public URL for emails and invoices ("" when none is set)."""
    value = str(get_setting("branding.logo_light"))
    return media_url(value) if value else ""
