import base64
import logging
import time
from typing import Any

from celery import shared_task
from django.core.exceptions import ValidationError

from apps.core.stores import state_redis

logger = logging.getLogger(__name__)

HEARTBEAT_KEY = "hb:beat"
HEARTBEAT_TTL_S = 300


@shared_task(name="apps.core.tasks.heartbeat", ignore_result=True)
def heartbeat() -> None:
    """Scheduled by beat every 30 s and run by a worker.

    A fresh key proves the whole beat → broker → worker path works; the beat
    container's healthcheck reads it.
    """
    state_redis().set(HEARTBEAT_KEY, int(time.time()), ex=HEARTBEAT_TTL_S)


@shared_task(name="apps.core.tasks.store_branding_image", ignore_result=True)
def store_branding_image(request: dict[str, Any]) -> None:
    """Store a logo the admin uploaded (CFG, ADR-0019) on the worker, which mounts the
    media volume. `request`: `{key, image_b64, actor_id, ip}`."""
    from apps.accounts.models import User  # noqa: PLC0415 (models need the app registry)
    from apps.core import branding  # noqa: PLC0415

    actor_id = str(request.get("actor_id") or "")
    actor = User.objects.filter(pk=actor_id).first() if actor_id else None
    try:
        branding.store_logo(
            str(request["key"]),
            base64.b64decode(str(request["image_b64"])),
            actor=actor,
            ip=str(request.get("ip") or "") or None,
        )
    except ValidationError as exc:
        logger.warning("branding.logo_rejected", extra={"error": exc.messages[0][:120]})


@shared_task(name="apps.core.tasks.delete_branding_image", ignore_result=True)
def delete_branding_image(name: str) -> None:
    """Remove a logo file the settings no longer point at."""
    from apps.core import branding  # noqa: PLC0415

    branding.delete_logo_file(name)


@shared_task(name="apps.core.tasks.measure_replica_lag", ignore_result=True)
def measure_replica_lag() -> float | None:
    """The read replica's lag every 15 s (M15, ADR-0022); nothing without a replica."""
    from apps.core import replica  # noqa: PLC0415

    measured = replica.measure()
    return None if measured is None else measured.get("lag_s")


@shared_task(name="apps.core.tasks.heal_settings_cache", ignore_result=True)
def heal_settings_cache() -> None:
    """Every minute: a settings cache that missed a version bump is reset (ADR-0023)."""
    from apps.core.services import heal_settings_cache as heal  # noqa: PLC0415

    heal()
