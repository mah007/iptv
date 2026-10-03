"""Stop sessions when access ends (apps.accounts.signals, sent after commit)."""

from typing import Any
from uuid import UUID

from django.dispatch import receiver

from apps.accounts.signals import access_expired, access_suspended, device_disabled
from apps.playback import services
from apps.playback.concurrency import KickReason


@receiver(access_expired, dispatch_uid="playback.stop_on_access_expired")
def stop_on_access_expired(sender: object, *, user_id: UUID, **kwargs: Any) -> None:
    services.stop_user_sessions(user_id, KickReason.ACCESS_EXPIRED)


@receiver(access_suspended, dispatch_uid="playback.stop_on_access_suspended")
def stop_on_access_suspended(sender: object, *, user_id: UUID, **kwargs: Any) -> None:
    services.stop_user_sessions(user_id, KickReason.ACCESS_SUSPENDED)


@receiver(device_disabled, dispatch_uid="playback.stop_on_device_disabled")
def stop_on_device_disabled(
    sender: object, *, user_id: UUID, device_id: UUID, **kwargs: Any
) -> None:
    services.stop_device_sessions(device_id, KickReason.DEVICE_DISABLED)
