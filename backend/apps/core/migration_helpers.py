"""Data-migration helpers for stored settings (ADR-0019).

A migration that makes a setting sensitive encrypts the rows already stored with
`encrypt_rows`, passing its own frozen list of keys (never the live registry, which
keeps changing). Both directions are idempotent.
"""

from collections.abc import Iterable, Mapping
from typing import Any

from apps.accounts import crypto


def encrypt_rows(setting_model: Any, keys: Iterable[str]) -> int:
    """Move plaintext values of these keys into `ciphertext`; returns how many."""
    count = 0
    for row in setting_model.objects.filter(key__in=list(keys), value__isnull=False):
        text = row.value if isinstance(row.value, str) else str(row.value)
        row.ciphertext = crypto.encrypt(text) if text else ""
        row.value = None
        row.save(update_fields=["value", "ciphertext"])
        count += 1
    return count


def decrypt_rows(setting_model: Any, keys: Iterable[str]) -> int:
    """The reverse of `encrypt_rows`: back to plaintext values."""
    count = 0
    for row in setting_model.objects.filter(key__in=list(keys), value__isnull=True):
        row.value = crypto.decrypt(row.ciphertext) if row.ciphertext else ""
        row.ciphertext = ""
        row.save(update_fields=["value", "ciphertext"])
        count += 1
    return count


def rename_rows(setting_model: Any, renames: Mapping[str, str]) -> None:
    """Give stored rows a setting's new key; a row already stored under the new key wins."""
    for old, new in renames.items():
        if setting_model.objects.filter(key=new).exists():
            setting_model.objects.filter(key=old).delete()
        else:
            setting_model.objects.filter(key=old).update(key=new)
