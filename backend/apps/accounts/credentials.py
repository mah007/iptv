"""Xtream credential secrets: readable usernames, TV-friendly passwords, Argon2id.

IPTV apps send the username and password on every Xtream API call, so checking
them must stay fast (SPEC §11: login p95 < 150 ms). Argon2id with OWASP's
recommended minimum (19 MiB, 2 passes, 1 lane) verifies in about 15 ms on the
reference host (ADR-0006 records the measurement), and successful checks are
cached in redis-state for 5 minutes, keyed by an HMAC of the pair, so repeated
calls skip the hash entirely. The cache entry is bound to the stored hash, so a
reset or revoke invalidates it at once.
"""

import hashlib
import hmac
import secrets
import string
from dataclasses import dataclass

import argon2
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from django.conf import settings

# RFC 9106 Argon2id with OWASP's first recommended parameter set.
ARGON2_TIME_COST = 2
ARGON2_MEMORY_COST_KIB = 19 * 1024
ARGON2_PARALLELISM = 1

_hasher = argon2.PasswordHasher(
    time_cost=ARGON2_TIME_COST,
    memory_cost=ARGON2_MEMORY_COST_KIB,
    parallelism=ARGON2_PARALLELISM,
    type=argon2.Type.ID,
)

USERNAME_SUFFIX_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"  # RFC 4648 base32, lowercase
USERNAME_SUFFIX_LENGTH = 6
USERNAME_PREFIX_LENGTH = 3
USERNAME_FALLBACK_PREFIX = "usr"

# Lowercase letters and digits without look-alikes (0/o, 1/l/i): easy to read off
# a screen and to type with a TV remote. 16 characters carry about 79 bits.
PASSWORD_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"  # noqa: S105 (an alphabet)
PASSWORD_LENGTH = 16

AUTH_CACHE_TTL_S = 300


def username_prefix(*candidates: str) -> str:
    """The first three ASCII letters of the first candidate that has three."""
    for candidate in candidates:
        letters = [char for char in candidate.lower() if char in string.ascii_lowercase]
        if len(letters) >= USERNAME_PREFIX_LENGTH:
            return "".join(letters[:USERNAME_PREFIX_LENGTH])
    return USERNAME_FALLBACK_PREFIX


def generate_username(prefix: str) -> str:
    """`mah-7k3p9q`: a readable prefix and six random base32 characters."""
    suffix = "".join(
        secrets.choice(USERNAME_SUFFIX_ALPHABET) for _ in range(USERNAME_SUFFIX_LENGTH)
    )
    return f"{prefix}-{suffix}"


def generate_password(length: int = PASSWORD_LENGTH) -> str:
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Constant-time check of a password against its Argon2id hash."""
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


# Verified when the username is unknown, so a miss costs as much as a wrong password.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def burn_verify(password: str) -> None:
    """Spend one verification's worth of time without a real hash (timing equaliser)."""
    verify_password(_DUMMY_HASH, password)


@dataclass(frozen=True, slots=True)
class CachedAuth:
    credential_id: str
    hash_fingerprint: str

    def encode(self) -> str:
        return f"{self.credential_id}:{self.hash_fingerprint}"

    @classmethod
    def decode(cls, raw: bytes | str) -> "CachedAuth | None":
        text = raw.decode() if isinstance(raw, bytes) else raw
        credential_id, sep, fingerprint = text.partition(":")
        return cls(credential_id, fingerprint) if sep else None


def auth_cache_key(username: str, password: str) -> str:
    """`xauth:<hmac>`: the plaintext never reaches Redis, not even hashed alone."""
    digest = hmac.new(
        settings.SECRET_KEY.encode(),
        f"xtream-auth\0{username}\0{password}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"xauth:{digest}"


def hash_fingerprint(password_hash: str) -> str:
    """Short digest of the stored hash: a cached success is valid only while it matches."""
    return hashlib.sha256(password_hash.encode()).hexdigest()[:32]
