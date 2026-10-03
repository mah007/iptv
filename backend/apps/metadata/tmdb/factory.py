"""Build the TMDB client the metadata tasks use from Django settings.

Settings (all optional; the orchestrator adds them to `config/settings`):

- `TMDB_READ_ACCESS_TOKEN` (preferred) or `TMDB_API_KEY`: without either the
  client runs in fixture mode, serving the synthetic sample fixtures offline.
- `TMDB_RATE_LIMIT_PER_S` (35), `TMDB_CACHE_TTL_S` (86400), `TMDB_LANGUAGE` ("en-US").
- `TMDB_FIXTURES_DIR`: another fixture directory for fixture mode.

The live client shares one token bucket and the 24 h response cache in
redis-cache (both recomputable state) with every other worker.
"""

import logging
from pathlib import Path

from django.conf import settings

from .cache import RedisResponseCache
from .client import CACHE_TTL_S, TMDBClient
from .fixtures import DEFAULT_LANGUAGE
from .ratelimit import DEFAULT_RATE_PER_S, RedisTokenBucket

__all__ = ["client_from_settings"]

logger = logging.getLogger(__name__)


def client_from_settings() -> TMDBClient:
    """A live client when a TMDB credential is configured, otherwise the fixture-mode client."""
    token = getattr(settings, "TMDB_READ_ACCESS_TOKEN", "") or None
    api_key = getattr(settings, "TMDB_API_KEY", "") or None
    language = getattr(settings, "TMDB_LANGUAGE", DEFAULT_LANGUAGE)
    if not token and not api_key:
        fixtures = getattr(settings, "TMDB_FIXTURES_DIR", None)
        logger.info("No TMDB credential configured: metadata comes from the offline fixtures")
        return TMDBClient.offline(Path(fixtures) if fixtures else None, language=language)

    from apps.core.stores import cache_redis  # noqa: PLC0415 (needs configured settings)

    redis_client = cache_redis()
    return TMDBClient(
        api_key=api_key,
        bearer_token=token,
        language=language,
        rate_limiter=RedisTokenBucket(
            redis_client,
            rate_per_s=float(getattr(settings, "TMDB_RATE_LIMIT_PER_S", DEFAULT_RATE_PER_S)),
        ),
        cache=RedisResponseCache(redis_client),
        cache_ttl_s=int(getattr(settings, "TMDB_CACHE_TTL_S", CACHE_TTL_S)),
    )
