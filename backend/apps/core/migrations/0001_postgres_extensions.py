from django.contrib.postgres.operations import (
    BtreeGistExtension,
    TrigramExtension,
    UnaccentExtension,
)
from django.db import migrations


class Migration(migrations.Migration):
    """Extensions SPEC §4 requires: trigram search, accent folding, and GiST
    exclusion constraints (no overlapping subscriptions, M3)."""

    initial = True
    dependencies: list[tuple[str, str]] = []

    operations = [
        TrigramExtension(),
        UnaccentExtension(),
        BtreeGistExtension(),
    ]
