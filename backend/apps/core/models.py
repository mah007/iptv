from django.db import models

from apps.core.ids import uuid7


class BaseModel(models.Model):
    """UUIDv7 primary key plus created/updated timestamps; every table uses it (SPEC §6)."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
