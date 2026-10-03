from typing import Any

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.utils import timezone

from apps.core.ids import uuid7


class AuditLogImmutableError(Exception):
    """Audit entries are append-only."""


class AuditLog(models.Model):
    """Who changed what, when, from where, with before/after snapshots (SPEC §6 ops, §11).

    Append-only. A database trigger (migration 0002) refuses UPDATE and DELETE,
    and the model refuses them before they reach the database. Not a BaseModel:
    an entry is never updated, so it has `at` instead of created/updated stamps.
    Before/after snapshots are redacted by `apps.audit.services.record`.
    Monthly partitioning arrives with the other high-volume tables in M15 (ADR-0005).
    """

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    at = models.DateTimeField(default=timezone.now, db_index=True)
    # Null for system actions. PROTECT: users are disabled, never deleted, and a
    # SET_NULL would have to UPDATE audit rows, which the trigger refuses anyway.
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="audit_entries",
    )
    actor_ip = models.GenericIPAddressField(null=True, blank=True)
    action = models.CharField(max_length=100)
    target_type = models.CharField(max_length=100, blank=True)
    target_id = models.CharField(max_length=100, blank=True)
    before = models.JSONField(null=True, blank=True, encoder=DjangoJSONEncoder)
    after = models.JSONField(null=True, blank=True, encoder=DjangoJSONEncoder)

    class Meta:
        ordering = ("-at", "-id")
        indexes = (
            models.Index(fields=("target_type", "target_id", "-at"), name="audit_target_at_idx"),
            models.Index(fields=("action", "-at"), name="audit_action_at_idx"),
        )

    def __str__(self) -> str:
        return f"{self.action} {self.target_type}:{self.target_id}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            msg = "Audit entries are append-only"
            raise AuditLogImmutableError(msg)
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        msg = "Audit entries are append-only"
        raise AuditLogImmutableError(msg)
