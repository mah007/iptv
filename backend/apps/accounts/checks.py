"""System checks: refuse to start with a missing or malformed encryption key."""

from collections.abc import Sequence
from typing import Any

from django.apps import AppConfig
from django.core.checks import CheckMessage, Error
from django.core.exceptions import ImproperlyConfigured

from apps.accounts import crypto


def check_field_encryption_key(
    app_configs: Sequence[AppConfig] | None = None, **kwargs: Any
) -> list[CheckMessage]:
    """FIELD_ENCRYPTION_KEY must hold Fernet keys, or admin MFA cannot work."""
    try:
        crypto.validate_configuration()
    except ImproperlyConfigured as exc:
        return [
            Error(
                str(exc),
                hint="Run `make secrets` (dev) or generate one with "
                "`openssl rand -base64 32 | tr '+/' '-_'`.",
                id="accounts.E001",
            )
        ]
    return []
