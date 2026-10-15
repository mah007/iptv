"""Failed Xtream sign-ins per client IP and per username (SPEC §7.5, §11; G-20, ADR-0021).

Every endpoint on tv.* authenticates, so each refusal (unknown user, wrong password)
counts in redis-state. Past a limit, credentials are not verified at all: the caller
answers its usual failure (`{"user_info":{"auth":0}}` for player_api.php), so nothing
reveals the limit, and a guesser spends no Argon2 work.

- **Per username and network** (`xtream.auth_failures_per_username` in the window): the
  client's /24 or /64 is part of the key, so someone guessing from elsewhere can't lock
  the customer out of their own network.
- **Per username, all networks together:** `USERNAME_SPREAD` times that limit, which slows a
  guess spread over many addresses.
- **Per client IP** (`xtream.auth_failures_per_ip`; generous, for carrier-grade NAT), with
  progressive delays: each time an address reaches the limit within a day, its pause is
  twice as long as the last (the window, then twice, four times and so on, up to `MAX_BLOCK_S`).

A recent successful sign-in (the 5-minute auth cache) still works while its address or
username is paused, so a household behind a noisy NAT keeps watching. A success clears
the username's counts. Usernames are counted case-insensitively and stored only as a
digest. If redis-state is down, nothing is limited.
"""

import hashlib
import ipaddress
import logging
from dataclasses import dataclass
from typing import Final, cast

import redis

from apps.core.registry import UnknownSettingError
from apps.core.services import get_setting
from apps.core.stores import state_redis

logger = logging.getLogger(__name__)

_IP_PREFIX: Final = "xc:fail:ip:"
_USER_PREFIX: Final = "xc:fail:user:"
_USER_ALL_PREFIX: Final = "xc:fail:userall:"
_BLOCK_PREFIX: Final = "xc:block:ip:"
_STRIKE_PREFIX: Final = "xc:strike:ip:"
#: Read by the admin's Lockouts page (`apps.accounts.lockouts`, ADR-0023).
USER_PREFIX: Final = _USER_PREFIX
USER_ALL_PREFIX: Final = _USER_ALL_PREFIX
BLOCK_PREFIX: Final = _BLOCK_PREFIX
#: The all-networks username limit is this many times the per-network one.
USERNAME_SPREAD: Final = 10
STRIKE_MEMORY_S: Final = 86400
MAX_BLOCK_S: Final = 86400

#: Used until the registry declares the settings (keys in `_SETTINGS`).
DEFAULTS: Final = {
    "xtream.auth_failures_per_ip": 60,
    "xtream.auth_failures_per_username": 30,
    "xtream.auth_failure_window_s": 900,
}


def _setting(key: str) -> int:
    try:
        return int(cast("int", get_setting(key)))
    except UnknownSettingError:
        return DEFAULTS[key]


def _digest(username: str) -> str:
    return hashlib.sha256(username.casefold().encode()).hexdigest()[:32]


def digest(username: str) -> str:
    """How a TV login is named in the counters (never stored in clear)."""
    return _digest(username)


def username_limit() -> int:
    """Failures from one network that pause a TV login."""
    return _setting("xtream.auth_failures_per_username")


def _network(ip: str | None) -> str:
    if not ip:
        return "-"
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return "-"
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    prefix = 24 if address.version == 4 else 64
    return str(ipaddress.ip_network(f"{address}/{prefix}", strict=False).network_address)


def _user_key(username: str, ip: str | None) -> str:
    return f"{_USER_PREFIX}{_digest(username)}:{_network(ip)}"


def _user_all_key(username: str) -> str:
    return _USER_ALL_PREFIX + _digest(username)


@dataclass(frozen=True, slots=True)
class Failures:
    username: int = 0
    username_all: int = 0
    ip: int = 0
    ip_blocked: bool = False

    @property
    def blocked(self) -> bool:
        per_user = _setting("xtream.auth_failures_per_username")
        return (
            self.ip_blocked
            or self.username >= per_user
            or self.username_all >= per_user * USERNAME_SPREAD
            or self.ip >= _setting("xtream.auth_failures_per_ip")
        )


def failures(username: str, ip: str | None) -> Failures:
    """The failures counted in the current windows (one MGET)."""
    keys = [_user_key(username, ip), _user_all_key(username)]
    if ip:
        keys += [_IP_PREFIX + ip, _BLOCK_PREFIX + ip]
    try:
        counts = cast("list[bytes | None]", state_redis().mget(keys))
    except redis.RedisError:
        logger.warning("redis-state unavailable; Xtream sign-ins not rate limited")
        return Failures()
    values = [int(count) if count is not None else 0 for count in counts]
    return Failures(
        username=values[0],
        username_all=values[1],
        ip=values[2] if len(values) > 2 else 0,
        ip_blocked=bool(values[3]) if len(values) > 3 else False,
    )


def record_failure(username: str, ip: str | None) -> None:
    window = _setting("xtream.auth_failure_window_s")
    try:
        client = state_redis()
        pipe = client.pipeline(transaction=False)
        for key in (_user_key(username, ip), _user_all_key(username)):
            pipe.incr(key)
            pipe.expire(key, window, nx=True)
        if ip:
            pipe.incr(_IP_PREFIX + ip)
            pipe.expire(_IP_PREFIX + ip, window, nx=True)
        results = pipe.execute()
        if ip and int(results[-2]) == _setting("xtream.auth_failures_per_ip"):
            _strike(client, ip, window)
    except redis.RedisError:
        logger.warning("redis-state unavailable; Xtream sign-in failure not counted")


def _strike(client: redis.Redis, ip: str, window: int) -> None:
    """The address reached its limit: pause it, twice as long as last time today."""
    strikes = int(cast("int", client.incr(_STRIKE_PREFIX + ip)))
    client.expire(_STRIKE_PREFIX + ip, STRIKE_MEMORY_S)
    block_s = min(MAX_BLOCK_S, window * 2 ** min(strikes - 1, 10))
    client.set(_BLOCK_PREFIX + ip, strikes, ex=block_s)
    logger.info("xtream.ip_paused", extra={"strikes": strikes, "seconds": block_s})


def clear_username(username: str, ip: str | None = None) -> None:
    try:
        state_redis().delete(_user_key(username, ip), _user_all_key(username))
    except redis.RedisError:
        logger.warning("redis-state unavailable; Xtream sign-in failures not cleared")


def clear_digest(login_digest: str, network: str | None) -> None:
    """An admin's unlock: one network's count of a TV login, or (`network` None) every
    count of it, the all-networks one included."""
    client = state_redis()
    if network is not None:
        client.delete(f"{_USER_PREFIX}{login_digest}:{network}")
        return
    keys = list(client.scan_iter(match=f"{_USER_PREFIX}{login_digest}:*", count=1000))
    client.delete(_USER_ALL_PREFIX + login_digest, *keys)


def clear_ip(ip: str) -> None:
    """An admin's unlock of a paused address: its pause, strikes and count."""
    state_redis().delete(_BLOCK_PREFIX + ip, _STRIKE_PREFIX + ip, _IP_PREFIX + ip)
