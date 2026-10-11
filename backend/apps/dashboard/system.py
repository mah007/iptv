"""The admin's System page (CFG, ADR-0019): read-only facts about this installation,
so nothing important needs SSH just to look at.

It lists what stays outside the admin's settings on purpose (the bootstrap
configuration in `.env` and the files under `secrets/`) and why, never with a
value or a length: only whether each one is present and where it lives.
"""

import os
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from django.conf import settings
from django.utils import timezone

from apps.accounts import monitoring
from apps.audit.models import AuditLog
from apps.core.services import from_server_config, get_setting, server_config
from config.origins import origin

KEY_ROTATION_ACTION: Final = "keys.rotate"
SECRETS_DIR: Final = Path("/run/secrets")


class SystemEnvironment(StrEnum):
    DEVELOPMENT = "development"
    PRODUCTION = "production"


class SecretReason(StrEnum):
    """Why a secret stays out of the admin's settings."""

    SIGNING = "signing"  # signs sessions and CSRF tokens before anything else runs
    ENCRYPTION = "encryption"  # decrypts the secret settings themselves
    DATASTORE = "datastore"  # needed to reach the database or Valkey holding the settings
    EDGE = "edge"  # the media edge checks tokens without Django
    MONITORING = "monitoring"  # read by the monitoring containers when they start
    INGEST = "ingest"  # read by MediaMTX when it starts


class SecretLocation(StrEnum):
    ENVIRONMENT = "environment"  # a line in .env
    FILE = "file"  # a file under secrets/ (a Docker secret)
    MISSING = "missing"


@dataclass(frozen=True, slots=True)
class BootstrapSecret:
    name: str
    reason: SecretReason
    #: The Docker secret file name, when it is (or may be) a file.
    file: str = ""
    required: bool = True


BOOTSTRAP_SECRETS: Final[tuple[BootstrapSecret, ...]] = (
    BootstrapSecret("DJANGO_SECRET_KEY", SecretReason.SIGNING),
    BootstrapSecret("FIELD_ENCRYPTION_KEY", SecretReason.ENCRYPTION),
    BootstrapSecret("POSTGRES_PASSWORD", SecretReason.DATASTORE, "postgres_password"),
    BootstrapSecret("REDIS_STATE_PASSWORD", SecretReason.DATASTORE, "redis_state_password"),
    BootstrapSecret("REDIS_CACHE_PASSWORD", SecretReason.DATASTORE, "redis_cache_password"),
    BootstrapSecret("MEILI_MASTER_KEY", SecretReason.DATASTORE),
    BootstrapSecret("MEDIA_TOKEN_KEYS", SecretReason.EDGE, "media_token_keys"),
    BootstrapSecret(
        "ALERT_WEBHOOK_TOKEN", SecretReason.MONITORING, "alert_webhook_token", required=False
    ),
    BootstrapSecret(
        "GRAFANA_ADMIN_PASSWORD", SecretReason.MONITORING, "grafana_admin_password", False
    ),
    BootstrapSecret(
        "POSTGRES_MONITOR_PASSWORD", SecretReason.MONITORING, "postgres_monitor_password", False
    ),
    BootstrapSecret("MEDIAMTX_PUBLISH_PASS", SecretReason.INGEST, required=False),
    BootstrapSecret("MEDIAMTX_READ_PASS", SecretReason.INGEST, required=False),
)


def _location(secret: BootstrapSecret) -> SecretLocation:
    if secret.name == "MEDIA_TOKEN_KEYS":
        from apps.playback.conf import media_token_keys_file  # noqa: PLC0415

        return SecretLocation.FILE if media_token_keys_file().is_file() else SecretLocation.MISSING
    if os.environ.get(secret.name, "").strip():
        return SecretLocation.ENVIRONMENT
    pointer = os.environ.get(f"{secret.name}_FILE", "").strip()
    if pointer and Path(pointer).is_file():
        return SecretLocation.FILE
    if secret.file and (SECRETS_DIR / secret.file).is_file():
        return SecretLocation.FILE
    return SecretLocation.MISSING


def _hosts() -> list[dict[str, str]]:
    scheme, port = str(settings.PUBLIC_SCHEME), int(settings.PUBLIC_PORT)
    roles = [
        ("tv", settings.TV_HOST),
        ("admin", settings.ADMIN_HOST),
        ("app", settings.APP_HOST),
        ("api", settings.API_HOST),
        ("media", settings.MEDIA_HOST),
    ]
    if monitoring.monitoring_enabled():
        roles.append(("grafana", settings.GRAFANA_HOST))
    return [
        {"role": role, "host": str(host), "url": origin(scheme, str(host), port)}
        for role, host in roles
    ]


def _rotations() -> list[dict[str, Any]]:
    """The latest rotation of each kind of key, as the key-rotation tools audit them."""
    latest: dict[str, AuditLog] = {}
    entries = (
        AuditLog.objects.filter(action=KEY_ROTATION_ACTION)
        .select_related("actor")
        .order_by("-at")[:100]
    )
    for entry in entries:
        latest.setdefault(entry.target_id, entry)
    return [
        {
            "kind": kind,
            "at": entry.at,
            "actor": entry.actor.username if entry.actor is not None else None,
        }
        for kind, entry in sorted(latest.items())
    ]


def _flag(name: str) -> bool:
    value = server_config(name)
    return str(value).strip().lower() in {"1", "true", "yes", "on"} if value is not None else False


def system_info() -> dict[str, Any]:
    now: datetime = timezone.now()
    return {
        "environment": (
            SystemEnvironment.DEVELOPMENT if settings.DEBUG else SystemEnvironment.PRODUCTION
        ).value,
        "version": str(settings.APP_VERSION),
        "commit": str(server_config("APP_COMMIT") or ""),
        "domain": str(settings.DOMAIN),
        "public_scheme": str(settings.PUBLIC_SCHEME),
        "hosts": _hosts(),
        # The host folder, from .env (Django's own MEDIA_ROOT setting means something else).
        "media_root": os.environ.get("MEDIA_ROOT", "").strip(),
        "library_root": str(settings.LIBRARY_ROOT),
        "data_root": str(settings.DATA_ROOT),
        "monitoring_enabled": monitoring.monitoring_enabled(),
        "live_ingest_enabled": _flag("LIVE_INGEST"),
        "server_time": now,
        "time_zone": str(get_setting("branding.time_zone")),
        "from_server_config": [
            {"key": state.definition.key, "env": list(state.definition.env)}
            for state in from_server_config()
        ],
        "secrets": [
            {
                "name": secret.name,
                "reason": secret.reason.value,
                "location": _location(secret).value,
                "required": secret.required,
            }
            for secret in BOOTSTRAP_SECRETS
        ],
        "key_rotations": _rotations(),
    }
