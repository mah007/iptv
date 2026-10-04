"""What customers change about their own account from the portal or an app (SPEC §9
Account, §10 Me; ADR-0013). Device creation, credential resets and revocations reuse
`apps.accounts.services` (with its max_devices limit and audit); this module adds the
profile edit and device renames, audited the same way with the customer as the actor."""

from collections.abc import Mapping
from typing import Any

from django.db import transaction
from django.db.models import QuerySet

from apps.accounts import services
from apps.accounts.models import Device, User
from apps.audit import services as audit
from apps.core.errors import ErrorCode, ProblemError

SELF_EDITABLE_FIELDS = ("name", "locale", "timezone", "marketing_opt_in")
DEVICE_EDITABLE_FIELDS = ("name", "app_hint")


def update_profile(user: User, changes: Mapping[str, Any], *, ip: str | None = None) -> User:
    allowed = {key: value for key, value in changes.items() if key in SELF_EDITABLE_FIELDS}
    return services.update_customer(user, allowed, actor=user, ip=ip)


def my_devices(user: User) -> QuerySet[Device]:
    """The customer's devices still in use (revoked ones are history)."""
    return (
        Device.objects.filter(user=user, revoked_at__isnull=True)
        .select_related("credential")
        .order_by("created_at", "id")
    )


def my_device(user: User, device_id: Any) -> Device:
    device = my_devices(user).filter(pk=device_id).first()
    if device is None:
        raise ProblemError(ErrorCode.NOT_FOUND, "No such device.")
    return device


def rename_device(
    device: Device, changes: Mapping[str, Any], *, actor: User, ip: str | None = None
) -> Device:
    with transaction.atomic():
        device = Device.objects.select_for_update().get(pk=device.pk)
        before = {field: getattr(device, field) for field in DEVICE_EDITABLE_FIELDS}
        changed = [
            field
            for field in DEVICE_EDITABLE_FIELDS
            if field in changes and getattr(device, field) != changes[field]
        ]
        if not changed:
            return device
        for field in changed:
            setattr(device, field, changes[field])
        device.save(update_fields=[*changed, "updated_at"])
        audit.record(
            "device.update",
            actor=actor,
            target=device,
            before={field: before[field] for field in changed},
            after={field: getattr(device, field) for field in changed},
            ip=ip,
        )
    return device
