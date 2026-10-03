"""Credential secrets, Argon2id cost, field encryption and TOTP (SPEC §11)."""

import re
import statistics
import time

import pytest
from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from apps.accounts import checks, credentials, crypto, mfa

# SPEC §11: Xtream logins must stay under 150 ms at p95; verification is most of it.
VERIFY_P95_BUDGET_MS = 150.0
SAMPLES = 40


def test_usernames_are_readable_and_random() -> None:
    assert credentials.username_prefix("محمد", "Sara Ali", "x") == "sar"
    assert credentials.username_prefix("", "", "") == "usr"
    names = {credentials.generate_username("sar") for _ in range(50)}
    assert len(names) == 50
    assert all(re.fullmatch(r"sar-[a-z2-7]{6}", name) for name in names)


def test_passwords_avoid_look_alikes() -> None:
    password = credentials.generate_password()
    assert len(password) == credentials.PASSWORD_LENGTH == 16
    assert set(password) <= set(credentials.PASSWORD_ALPHABET)
    assert not set("01ilo") & set(credentials.PASSWORD_ALPHABET)


def test_hashes_are_argon2id_with_the_chosen_parameters() -> None:
    encoded = credentials.hash_password("tv-password-1234")
    assert encoded.startswith("$argon2id$")
    assert f"m={credentials.ARGON2_MEMORY_COST_KIB},t={credentials.ARGON2_TIME_COST}" in encoded
    assert credentials.verify_password(encoded, "tv-password-1234")
    assert not credentials.verify_password(encoded, "tv-password-1235")
    assert not credentials.verify_password("not-a-hash", "tv-password-1234")
    assert not credentials.needs_rehash(encoded)
    assert credentials.needs_rehash("not-a-hash")


def test_verify_latency_p95_is_within_budget() -> None:
    """The measurement ADR-0006 records: Argon2id verify with production parameters."""
    encoded = credentials.hash_password("tv-password-1234")
    timings_ms = []
    for _ in range(SAMPLES):
        start = time.perf_counter()
        credentials.verify_password(encoded, "tv-password-1234")
        timings_ms.append((time.perf_counter() - start) * 1000)
    p50 = statistics.median(timings_ms)
    p95 = statistics.quantiles(timings_ms, n=20)[-1]
    print(f"argon2id verify (n={SAMPLES}): p50={p50:.1f} ms, p95={p95:.1f} ms")  # noqa: T201
    assert p95 < VERIFY_P95_BUDGET_MS


def test_auth_cache_key_hides_the_pair() -> None:
    key = credentials.auth_cache_key("sar-abcdef", "secret-password")
    assert key.startswith("xauth:")
    assert "sar-abcdef" not in key
    assert "secret-password" not in key
    assert key != credentials.auth_cache_key("sar-abcdef", "secret-passwore")
    cached = credentials.CachedAuth("id", "fp")
    assert credentials.CachedAuth.decode(cached.encode().encode()) == cached
    assert credentials.CachedAuth.decode("garbage") is None


def test_field_encryption_round_trip_and_rotation() -> None:
    token = crypto.encrypt("JBSWY3DPEHPK3PXP")
    assert "JBSWY3DPEHPK3PXP" not in token
    assert crypto.decrypt(token) == "JBSWY3DPEHPK3PXP"
    new_key = Fernet.generate_key().decode()
    from django.conf import settings  # noqa: PLC0415

    with override_settings(FIELD_ENCRYPTION_KEY=f"{new_key},{settings.FIELD_ENCRYPTION_KEY}"):
        assert crypto.decrypt(token) == "JBSWY3DPEHPK3PXP"  # old key still decrypts
        rotated = crypto.rotate(token)
    with override_settings(FIELD_ENCRYPTION_KEY=new_key):
        assert crypto.decrypt(rotated) == "JBSWY3DPEHPK3PXP"
        with pytest.raises(crypto.DecryptionError):
            crypto.decrypt(token)
        with pytest.raises(crypto.DecryptionError):
            crypto.rotate(token)
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt("not-a-token")


@pytest.mark.parametrize("value", ["", " , ", "not-a-fernet-key"])
def test_bad_encryption_keys_fail_the_system_check(value: str) -> None:
    with override_settings(FIELD_ENCRYPTION_KEY=value):
        with pytest.raises(ImproperlyConfigured):
            crypto.encrypt("x")
        [error] = checks.check_field_encryption_key()
        assert error.id == "accounts.E001"
    assert checks.check_field_encryption_key() == []


def test_totp_accepts_one_step_of_drift_and_never_twice() -> None:
    secret = mfa.new_secret()
    now = 1_800_000_015.0  # mid-step
    step = mfa.current_step(now)
    assert mfa.matching_step(secret, mfa.code_at(secret, step), now=now) == step
    assert mfa.matching_step(secret, mfa.code_at(secret, step - 1), now=now) == step - 1
    assert mfa.matching_step(secret, mfa.code_at(secret, step + 1), now=now) == step + 1
    assert mfa.matching_step(secret, mfa.code_at(secret, step - 2), now=now) is None
    code = mfa.code_at(secret, step)
    assert mfa.matching_step(secret, code[:3] + " " + code[3:], now=now) == step
    assert mfa.matching_step(secret, code, last_used_step=step, now=now) is None
    assert mfa.matching_step(secret, "12345", now=now) is None
    assert mfa.matching_step(secret, "abcdef", now=now) is None
    uri = mfa.provisioning_uri(secret, "sam@example.com", "Smart IPTV")
    assert uri.startswith("otpauth://totp/Smart%20IPTV:sam%40example.com?secret=")
