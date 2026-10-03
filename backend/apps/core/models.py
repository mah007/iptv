from django.conf import settings
from django.db import models

from apps.core.ids import uuid7


class BaseModel(models.Model):
    """UUIDv7 primary key plus created/updated timestamps; every table uses it (SPEC §6)."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Setting(BaseModel):
    """An admin override of a typed setting (SPEC §6 ops, §8.3 Settings).

    Only overrides live here; defaults, types and validation live in code
    (`apps.core.registry`). Read through `apps.core.services.get_setting`, which
    caches values under a version key so changes apply without a restart.
    """

    key = models.CharField(max_length=100, unique=True)
    value = models.JSONField()
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    def __str__(self) -> str:
        return self.key
