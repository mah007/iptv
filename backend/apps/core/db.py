"""Database helpers shared by models."""

from typing import Any

from django.db import models


class NextVal(models.Func):
    """`nextval('<sequence>')`, for integer ids drawn from a PostgreSQL sequence.

    Used as a `db_default`: Xtream clients need integer ids (SPEC §6), while our
    primary keys are UUIDv7. The migration creates the sequence before the column.
    """

    function = "nextval"
    output_field = models.BigIntegerField()

    def __init__(self, sequence: str, **extra: Any) -> None:
        super().__init__(models.Value(sequence), **extra)
