"""Failed Xtream sign-ins per client IP and per username (SPEC §7.5, §11).

Every endpoint on tv.* authenticates, so each refusal (unknown user, wrong
password) counts in redis-state, for the IP and for the username, in a fixed
window that starts at the first failure. Past either limit, credentials are not
verified at all until the window ends: the caller answers its usual failure
(`{"user_info":{"auth":0}}` for player_api.php), so nothing reveals the limit, and
a guesser spends no Argon2 work. A success clears the username's count.

Traefik limits the request rate on the tv router in front of this; this counter
is what stops slow password guessing, which a request rate cannot.

Usernames are counted case-insensitively (logins are unique regardless of case)
and stored only as a digest. If redis-state is down, nothing is limited.
"""

import hashlib
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


def _user_key(username: str) -> str:
    return _USER_PREFIX + hashlib.sha256(username.casefold().encode()).hexdigest()[:32]


def _keys(username: str, ip: str | None) -> list[str]:
    return [_user_key(username), *([_IP_PREFIX + ip] if ip else [])]


@dataclass(frozen=True, slots=True)
class Failures:
    username: int = 0
    ip: int = 0

    @property
    def blocked(self) -> bool:
        return self.username >= _setting(
            "xtream.auth_failures_per_username"
        ) or self.ip >= _setting("xtream.auth_failures_per_ip")


def failures(username: str, ip: str | None) -> Failures:
    """The failures counted in the current windows (one MGET)."""
    try:
        counts = cast("list[bytes | None]", state_redis().mget(_keys(username, ip)))
    except redis.RedisError:
        logger.warning("redis-state unavailable; Xtream sign-ins not rate limited")
        return Failures()
    values = [int(count) if count is not None else 0 for count in counts]
    return Failures(username=values[0], ip=values[1] if len(values) > 1 else 0)


def record_failure(username: str, ip: str | None) -> None:
    window = _setting("xtream.auth_failure_window_s")
    try:
        pipe = state_redis().pipeline(transaction=False)
        for key in _keys(username, ip):
            pipe.incr(key)
            pipe.expire(key, window, nx=True)
        pipe.execute()
    except redis.RedisError:
        logger.warning("redis-state unavailable; Xtream sign-in failure not counted")


def clear_username(username: str) -> None:
    try:
        state_redis().delete(_user_key(username))
    except redis.RedisError:
        logger.warning("redis-state unavailable; Xtream sign-in failures not cleared")
