"""Build the TMDB client the metadata tasks use from the admin's settings (ADR-0019).

Settings (Settings → Integrations → TMDB; `.env`'s TMDB_* variables are the fallback):

- `metadata.tmdb_read_access_token` (preferred) or `metadata.tmdb_api_key`: without
  either the client runs in fixture mode, serving the synthetic sample fixtures offline;
- `metadata.tmdb_rate_limit_per_s` (35), `metadata.tmdb_cache_ttl_s` (86400),
  `metadata.tmdb_language` ("en-US").
- `TMDB_FIXTURES_DIR` (a Django setting, tests and development): another fixture
  directory for fixture mode.

The live client shares one token bucket and the 24 h response cache in
redis-cache (both recomputable state) with every other worker.
"""

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

from apps.core.services import get_secret, get_setting

from .cache import RedisResponseCache
from .client import TMDBClient

__all__ = ["TMDBConfig", "client_for", "client_from_settings", "tmdb_config"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TMDBConfig:
    token: str
    api_key: str
    language: str
    rate_per_s: int
    cache_ttl_s: int

    @property
    def has_credential(self) -> bool:
        return bool(self.token or self.api_key)

    @property
    def fingerprint(self) -> str:
        """Identifies the configuration without holding the secrets themselves."""
        material = "\0".join(
            (self.token, self.api_key, self.language, str(self.rate_per_s), str(self.cache_ttl_s))
        )
        return hashlib.sha256(material.encode()).hexdigest()


def tmdb_config() -> TMDBConfig:
    return TMDBConfig(
        token=get_secret("metadata.tmdb_read_access_token"),
        api_key=get_secret("metadata.tmdb_api_key"),
        language=str(get_setting("metadata.tmdb_language")),
        rate_per_s=int(get_setting("metadata.tmdb_rate_limit_per_s")),
        cache_ttl_s=int(get_setting("metadata.tmdb_cache_ttl_s")),
    )


def client_for(config: TMDBConfig) -> TMDBClient:
    """A live client when the configuration has a credential, else fixture mode."""
    if not config.has_credential:
        fixtures = getattr(settings, "TMDB_FIXTURES_DIR", None)
        logger.info("No TMDB credential configured: metadata comes from the offline fixtures")
        return TMDBClient.offline(Path(fixtures) if fixtures else None, language=config.language)

    from apps.core.stores import cache_redis  # noqa: PLC0415 (needs configured settings)

    from .ratelimit import RedisTokenBucket  # noqa: PLC0415

    redis_client = cache_redis()
    return TMDBClient(
        api_key=config.api_key or None,
        bearer_token=config.token or None,
        language=config.language,
        rate_limiter=RedisTokenBucket(redis_client, rate_per_s=float(config.rate_per_s)),
        cache=RedisResponseCache(redis_client),
        cache_ttl_s=config.cache_ttl_s,
    )


def client_from_settings() -> TMDBClient:
    """A client for the current settings (live with a credential, fixture mode without)."""
    return client_for(tmdb_config())
