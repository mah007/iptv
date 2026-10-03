"""TMDB v3 access: client, shared rate limiter, response caches and offline fixture mode.

Django-free (the settings-driven factory lives in `.factory`), so it imports
without a database or configured settings.
"""

from .cache import InMemoryResponseCache, RedisResponseCache, ResponseCache, cache_key
from .client import (
    API_BASE_URL,
    CACHE_TTL_S,
    IMAGE_BASE_URL,
    IMAGE_LANGUAGES,
    MOVIE_APPEND,
    TV_APPEND,
    JSONObject,
    TMDBClient,
    image_url,
)
from .errors import (
    RateLimitTimeoutError,
    TMDBAuthError,
    TMDBError,
    TMDBNotFoundError,
    TMDBRateLimitedError,
    TMDBRetryableError,
    TMDBServerError,
    TMDBUnavailableError,
)
from .fixtures import DEFAULT_FIXTURES_DIR, FixtureTransport
from .ratelimit import DEFAULT_RATE_PER_S, RateLimiter, RedisTokenBucket

__all__ = [
    "API_BASE_URL",
    "CACHE_TTL_S",
    "DEFAULT_FIXTURES_DIR",
    "DEFAULT_RATE_PER_S",
    "IMAGE_BASE_URL",
    "IMAGE_LANGUAGES",
    "MOVIE_APPEND",
    "TV_APPEND",
    "FixtureTransport",
    "InMemoryResponseCache",
    "JSONObject",
    "RateLimitTimeoutError",
    "RateLimiter",
    "RedisResponseCache",
    "RedisTokenBucket",
    "ResponseCache",
    "TMDBAuthError",
    "TMDBClient",
    "TMDBError",
    "TMDBNotFoundError",
    "TMDBRateLimitedError",
    "TMDBRetryableError",
    "TMDBServerError",
    "TMDBUnavailableError",
    "cache_key",
    "image_url",
]
