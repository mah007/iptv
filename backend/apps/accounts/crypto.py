"""Encryption at rest for secrets stored in the database (admin TOTP seeds).

FIELD_ENCRYPTION_KEY holds one or more Fernet keys separated by commas. The first
encrypts; every key decrypts, so a key is rotated by putting the new one first,
re-encrypting (`rotate`), then dropping the old one.
"""

from functools import cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


class DecryptionError(Exception):
    """The value was not encrypted with any configured key, or was altered."""


@cache
def _fernet(raw_keys: str) -> MultiFernet:
    keys = [key.strip() for key in raw_keys.split(",") if key.strip()]
    if not keys:
        msg = "FIELD_ENCRYPTION_KEY is empty"
        raise ImproperlyConfigured(msg)
    try:
        return MultiFernet([Fernet(key) for key in keys])
    except ValueError as exc:
        msg = "FIELD_ENCRYPTION_KEY must hold Fernet keys (urlsafe base64 of 32 bytes)"
        raise ImproperlyConfigured(msg) from exc


def _keys() -> MultiFernet:
    return _fernet(settings.FIELD_ENCRYPTION_KEY)


def validate_configuration() -> None:
    """Raise ImproperlyConfigured unless FIELD_ENCRYPTION_KEY holds valid Fernet keys."""
    _keys()


def encrypt(plaintext: str) -> str:
    return _keys().encrypt(plaintext.encode()).decode("ascii")


def decrypt(token: str) -> str:
    try:
        return _keys().decrypt(token.encode("ascii")).decode()
    except (InvalidToken, UnicodeError):
        raise DecryptionError("cannot decrypt the stored value") from None


def rotate(token: str) -> str:
    """Re-encrypt with the first (current) key."""
    try:
        return _keys().rotate(token.encode("ascii")).decode("ascii")
    except (InvalidToken, UnicodeError):
        raise DecryptionError("cannot decrypt the stored value") from None
