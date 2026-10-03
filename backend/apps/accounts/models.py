from django.contrib.auth.models import AbstractUser

from apps.core.models import BaseModel


class User(BaseModel, AbstractUser):
    """Platform user: customers and staff alike.

    It exists from the very first migration so the UUIDv7 key and custom model
    never need a database rebuild (ADR-0002). Profile fields, status, locale and
    RBAC arrive in M3.
    """

    def __str__(self) -> str:
        return self.username
