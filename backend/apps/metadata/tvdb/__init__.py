"""TheTVDB v4 access: the TV fallback's client and its offline fixture mode (ADR-0023).

Django-free (the settings-driven factory lives in `.factory`), like the TMDB package.
"""

from .client import API_BASE_URL, ARTWORK_HOST, CACHE_TTL_S, SeasonType, TVDBClient, is_artwork_url
from .errors import (
    TVDBAuthError,
    TVDBError,
    TVDBNotFoundError,
    TVDBRateLimitedError,
    TVDBRetryableError,
    TVDBServerError,
    TVDBUnavailableError,
)
from .fixtures import DEFAULT_FIXTURES_DIR, FixtureTransport

__all__ = [
    "API_BASE_URL",
    "ARTWORK_HOST",
    "CACHE_TTL_S",
    "DEFAULT_FIXTURES_DIR",
    "FixtureTransport",
    "SeasonType",
    "TVDBAuthError",
    "TVDBClient",
    "TVDBError",
    "TVDBNotFoundError",
    "TVDBRateLimitedError",
    "TVDBRetryableError",
    "TVDBServerError",
    "TVDBUnavailableError",
    "is_artwork_url",
]
