"""Build the TheTVDB client from the admin's settings (ADR-0019, ADR-0023).

Settings (Settings → Integrations → TheTVDB; `.env`'s THETVDB_API_KEY is the fallback):

- `metadata.tvdb_api_key` and, for a subscriber-supported key, `metadata.tvdb_pin`:
  without a key there is no TheTVDB fallback (`client_for` returns None);
- `metadata.tvdb_rate_limit_per_s` (10); responses are cached as long as TMDB's
  (`metadata.tmdb_cache_ttl_s`).

The live client shares one token bucket (`ratelimit:tvdb`) and the response cache
(`tvdb:response:`) in redis-cache, both recomputable state, with every other worker.
"""

import hashlib
from dataclasses import dataclass

from apps.core.services import get_secret, get_setting

from .client import TVDBClient

__all__ = ["TVDBConfig", "client_for", "tvdb_config"]


@dataclass(frozen=True, slots=True)
class TVDBConfig:
    api_key: str
    pin: str
    rate_per_s: int
    cache_ttl_s: int

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @property
    def fingerprint(self) -> str:
        """Identifies the configuration without holding the secrets themselves."""
        material = "\0".join((self.api_key, self.pin, str(self.rate_per_s), str(self.cache_ttl_s)))
        return hashlib.sha256(material.encode()).hexdigest()


def tvdb_config() -> TVDBConfig:
    return TVDBConfig(
        api_key=get_secret("metadata.tvdb_api_key"),
        pin=get_secret("metadata.tvdb_pin"),
        rate_per_s=int(get_setting("metadata.tvdb_rate_limit_per_s")),
        cache_ttl_s=int(get_setting("metadata.tmdb_cache_ttl_s")),
    )


def client_for(config: TVDBConfig) -> TVDBClient | None:
    """A live client when a key is set; None (no fallback) without one."""
    if not config.configured:
        return None

    from apps.core.stores import cache_redis  # noqa: PLC0415 (needs configured settings)
    from apps.metadata.tmdb.cache import RedisResponseCache  # noqa: PLC0415
    from apps.metadata.tmdb.ratelimit import RedisTokenBucket  # noqa: PLC0415

    redis_client = cache_redis()
    return TVDBClient(
        api_key=config.api_key,
        pin=config.pin or None,
        rate_limiter=RedisTokenBucket(
            redis_client, key="ratelimit:tvdb", rate_per_s=float(config.rate_per_s)
        ),
        cache=RedisResponseCache(redis_client, prefix="tvdb:response:"),
        cache_ttl_s=config.cache_ttl_s,
    )
