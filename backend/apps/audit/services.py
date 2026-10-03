"""Record audit entries (SPEC §11: every admin, billing and subscription mutation)."""

import ipaddress
import json
from dataclasses import dataclass
from typing import Any

from django.core.serializers.json import DjangoJSONEncoder
from django.db import models

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.core.redaction import redact_value


@dataclass(frozen=True, slots=True)
class AuditTarget:
    """What an entry is about when it isn't a model instance, e.g. a setting key."""

    type: str
    id: str


def target_of(target: models.Model | AuditTarget | None) -> tuple[str, str]:
    """(`app_label.model`, primary key) for a model instance; the pair itself for a target."""
    if target is None:
        return "", ""
    if isinstance(target, AuditTarget):
        return target.type, target.id
    return target._meta.label_lower, str(target.pk)


def _snapshot(value: Any) -> Any:
    """Redacted, JSON-normalised copy, so what's stored is exactly what's read back."""
    if value is None:
        return None
    return json.loads(json.dumps(redact_value(value), cls=DjangoJSONEncoder))


def _valid_ip(ip: str | None) -> str | None:
    if not ip:
        return None
    try:
        return str(ipaddress.ip_address(ip))
    except ValueError:
        return None


def record(  # noqa: PLR0913 (the plan's signature; everything after action is keyword-only)
    action: str,
    *,
    actor: User | None,
    target: models.Model | AuditTarget | None = None,
    before: Any = None,
    after: Any = None,
    ip: str | None = None,
) -> AuditLog:
    """Append an audit entry. `action` reads like `customer.create` or `setting.update`.

    Call it inside the transaction of the change it describes, so both commit or
    neither does. Snapshots are redacted: secrets never reach the audit log.
    """
    target_type, target_id = target_of(target)
    return AuditLog.objects.create(
        actor=actor if actor is not None and actor.is_authenticated else None,
        actor_ip=_valid_ip(ip),
        action=action,
        target_type=target_type,
        target_id=target_id,
        before=_snapshot(before),
        after=_snapshot(after),
    )
