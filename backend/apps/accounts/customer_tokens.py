"""Bearer tokens for app clients of the customer API on api.<domain> (SPEC §11; ADR-0013).

A sign-in starts a token *family* bound to one customer and one `app` device:

- an **access token**, short-lived (`security.customer_access_token_ttl_s`, 10 min),
  sent as `Authorization: Bearer <token>`;
- a **refresh token**, rotated on every use: `refresh` answers a new pair and the old
  refresh token stops working. Presenting a refresh token that was already rotated is
  *reuse*: someone kept a copy, so the whole family is revoked (the thief's and the
  owner's tokens alike), and the app device with it.

Everything lives in redis-state (noeviction, AOF), never the plaintext tokens:

    ctok:a:<sha256(access)>  -> "<user>|<device>|<family>"   EX access TTL
    ctok:f:<family>          -> hash {u, d, rt: sha256(current refresh)}, EX refresh TTL
    ctok:r:<sha256(refresh)> -> "<family>"                    EX refresh TTL
    ctok:u:<user>            -> set of the user's families    EX refresh TTL

An access token is valid only while its family exists, so revoking a family (sign-out,
reuse, password reset, account disabled) ends its access tokens at once. Rotation runs
in one Lua script, so two concurrent refreshes with the same token cannot both win.
Tokens are opaque random strings rather than JWTs: every request checks Redis anyway
for revocation, so a self-contained token would buy nothing and could not be revoked.
"""

import hashlib
import secrets
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, cast
from uuid import UUID

from apps.core.services import get_setting
from apps.core.stores import state_redis

ACCESS_PREFIX: Final = "siptv_at_"
REFRESH_PREFIX: Final = "siptv_rt_"
_TOKEN_BYTES: Final = 32
_KEY: Final = "ctok"

# KEYS: family hash, old refresh key, new refresh key, user set.
# ARGV: sha256(old refresh), sha256(new refresh), family, TTL seconds.
_ROTATE = """
local family = redis.call('GET', KEYS[2])
if not family or family ~= ARGV[3] then return {'invalid'} end
local current = redis.call('HGET', KEYS[1], 'rt')
if not current then return {'invalid'} end
if current ~= ARGV[1] then
  local user = redis.call('HGET', KEYS[1], 'u')
  local device = redis.call('HGET', KEYS[1], 'd')
  redis.call('DEL', KEYS[1])
  return {'reuse', user or '', device or ''}
end
redis.call('HSET', KEYS[1], 'rt', ARGV[2])
redis.call('EXPIRE', KEYS[1], ARGV[4])
redis.call('SET', KEYS[3], ARGV[3], 'EX', ARGV[4])
redis.call('EXPIRE', KEYS[4], ARGV[4])
return {'ok', redis.call('HGET', KEYS[1], 'u'), redis.call('HGET', KEYS[1], 'd')}
"""


class RefreshOutcome(StrEnum):
    OK = "ok"
    INVALID = "invalid"  # unknown, expired or revoked
    REUSE = "reuse"  # an already rotated token: the family is now revoked


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str
    refresh_token: str
    access_expires_in: int
    refresh_expires_in: int
    family: str


@dataclass(frozen=True, slots=True)
class AccessGrant:
    """What a valid access token stands for."""

    user_id: UUID
    device_id: UUID
    family: str


@dataclass(frozen=True, slots=True)
class Rotation:
    outcome: RefreshOutcome
    pair: TokenPair | None = None
    user_id: UUID | None = None
    device_id: UUID | None = None


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _access_key(token: str) -> str:
    return f"{_KEY}:a:{_digest(token)}"


def _refresh_key(digest: str) -> str:
    return f"{_KEY}:r:{digest}"


def _family_key(family: str) -> str:
    return f"{_KEY}:f:{family}"


def _user_key(user_id: UUID | str) -> str:
    return f"{_KEY}:u:{user_id}"


def access_ttl_s() -> int:
    return int(cast("int", get_setting("security.customer_access_token_ttl_s")))


def refresh_ttl_s() -> int:
    return int(cast("int", get_setting("security.customer_refresh_token_days"))) * 86400


def _new(prefix: str) -> str:
    return prefix + secrets.token_urlsafe(_TOKEN_BYTES)


def _store_access(token: str, user_id: UUID | str, device_id: UUID | str, family: str) -> None:
    state_redis().set(_access_key(token), f"{user_id}|{device_id}|{family}", ex=access_ttl_s())


def issue(user_id: UUID | str, device_id: UUID | str) -> TokenPair:
    """Start a new family for a fresh sign-in."""
    family = secrets.token_hex(16)
    access, refresh = _new(ACCESS_PREFIX), _new(REFRESH_PREFIX)
    ttl = refresh_ttl_s()
    pipe = state_redis().pipeline(transaction=True)
    pipe.hset(
        _family_key(family),
        mapping={"u": str(user_id), "d": str(device_id), "rt": _digest(refresh)},
    )
    pipe.expire(_family_key(family), ttl)
    pipe.set(_refresh_key(_digest(refresh)), family, ex=ttl)
    pipe.sadd(_user_key(user_id), family)
    pipe.expire(_user_key(user_id), ttl)
    pipe.set(_access_key(access), f"{user_id}|{device_id}|{family}", ex=access_ttl_s())
    pipe.execute()
    return TokenPair(access, refresh, access_ttl_s(), ttl, family)


def authenticate(token: str) -> AccessGrant | None:
    """The grant behind a live access token of a live family; None otherwise."""
    if not token.startswith(ACCESS_PREFIX) or len(token) > 128:
        return None
    raw = cast("bytes | None", state_redis().get(_access_key(token)))
    if raw is None:
        return None
    try:
        user_id, device_id, family = raw.decode().split("|")
        grant = AccessGrant(UUID(user_id), UUID(device_id), family)
    except ValueError:
        return None
    if not state_redis().exists(_family_key(grant.family)):
        return None
    return grant


def rotate(refresh_token: str) -> Rotation:
    """Exchange a refresh token for a new pair (the old one stops working)."""
    if not refresh_token.startswith(REFRESH_PREFIX) or len(refresh_token) > 128:
        return Rotation(RefreshOutcome.INVALID)
    old = _digest(refresh_token)
    family_raw = cast("bytes | None", state_redis().get(_refresh_key(old)))
    if family_raw is None:
        return Rotation(RefreshOutcome.INVALID)
    family = family_raw.decode()
    new_refresh = _new(REFRESH_PREFIX)
    ttl = refresh_ttl_s()
    # The user set's key is only known from the family; EXPIRE on a missing key is a no-op.
    user_raw = cast("bytes | None", state_redis().hget(_family_key(family), "u"))
    user_key = _user_key(user_raw.decode() if user_raw else "-")
    result = cast(
        "list[bytes]",
        state_redis().eval(
            _ROTATE,
            4,
            _family_key(family),
            _refresh_key(old),
            _refresh_key(_digest(new_refresh)),
            user_key,
            old,
            _digest(new_refresh),
            family,
            str(ttl),
        ),
    )
    outcome = RefreshOutcome(result[0].decode())
    if outcome is RefreshOutcome.INVALID:
        return Rotation(outcome)
    user_id = UUID(result[1].decode()) if result[1] else None
    device_id = UUID(result[2].decode()) if result[2] else None
    if outcome is RefreshOutcome.REUSE or user_id is None or device_id is None:
        return Rotation(RefreshOutcome.REUSE, user_id=user_id, device_id=device_id)
    access = _new(ACCESS_PREFIX)
    _store_access(access, user_id, device_id, family)
    return Rotation(
        outcome,
        TokenPair(access, new_refresh, access_ttl_s(), ttl, family),
        user_id=user_id,
        device_id=device_id,
    )


def revoke_family(family: str) -> UUID | None:
    """End a family (sign-out); returns its device id, if the family was live."""
    raw = cast("bytes | None", state_redis().hget(_family_key(family), "d"))
    state_redis().delete(_family_key(family))
    return UUID(raw.decode()) if raw else None


def revoke_user(user_id: UUID | str) -> list[UUID]:
    """End every family of the user (password reset, account disabled); their devices."""
    families = [
        raw.decode() for raw in cast("set[bytes]", state_redis().smembers(_user_key(user_id)))
    ]
    devices = [device for family in families if (device := revoke_family(family)) is not None]
    state_redis().delete(_user_key(user_id))
    return devices
