"""TheTVDB errors. Messages name the API path only: never the query string, the API key,
the PIN or the bearer token.

They extend the TMDB errors of the same meaning, so the metadata tasks' retry rules
(`TMDBRetryableError`, a 429's Retry-After) apply to both providers alike.
"""

from apps.metadata.tmdb.errors import (
    TMDBError,
    TMDBNotFoundError,
    TMDBRateLimitedError,
    TMDBRetryableError,
)

__all__ = [
    "TVDBAuthError",
    "TVDBError",
    "TVDBNotFoundError",
    "TVDBRateLimitedError",
    "TVDBRetryableError",
    "TVDBServerError",
    "TVDBUnavailableError",
]


class TVDBError(TMDBError):
    """A TheTVDB request failed."""


class TVDBNotFoundError(TVDBError, TMDBNotFoundError):
    """404: no such series, season or episode (or no fixture for it)."""


class TVDBAuthError(TVDBError):
    """401/403 at sign-in: the API key (or the subscriber PIN) is missing, wrong or revoked."""


class TVDBRetryableError(TVDBError, TMDBRetryableError):
    """A failure worth retrying; raised again once the retries are spent."""


class TVDBRateLimitedError(TVDBRetryableError, TMDBRateLimitedError):
    """429. `retry_after` is the server's Retry-After in seconds, when it sent one."""


class TVDBServerError(TVDBRetryableError):
    """5xx from TheTVDB."""


class TVDBUnavailableError(TVDBRetryableError):
    """The request did not complete: timeout, DNS or connection failure."""
