"""Time-based one-time passwords for admin MFA (RFC 6238; SPEC §11).

Codes are six digits on 30-second steps. One step of clock drift is accepted
either way, and a step is never accepted twice (`last_used_step`), so a code
seen over someone's shoulder cannot be replayed.
"""

import hmac
import time

import pyotp

ISSUER = "Smart IPTV"
STEP_S = 30
DIGITS = 6
DRIFT_STEPS = 1


def new_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, account: str, issuer: str = ISSUER) -> str:
    """otpauth://totp/... for the authenticator app's QR code."""
    return pyotp.TOTP(secret, digits=DIGITS, interval=STEP_S).provisioning_uri(
        name=account, issuer_name=issuer
    )


def current_step(now: float | None = None) -> int:
    return int((time.time() if now is None else now) // STEP_S)


def code_at(secret: str, step: int) -> str:
    return pyotp.TOTP(secret, digits=DIGITS, interval=STEP_S).generate_otp(step)


def matching_step(
    secret: str, code: str, *, last_used_step: int | None = None, now: float | None = None
) -> int | None:
    """The time step `code` belongs to, or None if it is wrong, stale or already used."""
    candidate = "".join(code.split())
    if len(candidate) != DIGITS or not candidate.isdigit():
        return None
    now_step = current_step(now)
    found: int | None = None
    # Check every step in the window so timing does not reveal which one matched;
    # the latest matching step wins.
    for step in range(now_step - DRIFT_STEPS, now_step + DRIFT_STEPS + 1):
        if hmac.compare_digest(code_at(secret, step), candidate):
            found = step
    if found is None or (last_used_step is not None and found <= last_used_step):
        return None
    return found
