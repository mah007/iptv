"""TMDB errors. Messages name the API path only: never the query string or a credential."""

__all__ = [
    "RateLimitTimeoutError",
    "TMDBAuthError",
    "TMDBError",
    "TMDBNotFoundError",
    "TMDBRateLimitedError",
    "TMDBRetryableError",
    "TMDBServerError",
    "TMDBUnavailableError",
]


class TMDBError(Exception):
    """A TMDB request failed."""

    def __init__(self, message: str, *, path: str = "", status_code: int | None = None) -> None:
        super().__init__(message)
        self.path = path
        self.status_code = status_code


class TMDBNotFoundError(TMDBError):
    """404: no such movie, series, season or external ID (or no fixture for it)."""


class TMDBAuthError(TMDBError):
    """401/403: the API key or read-access token is missing, wrong or revoked."""


class TMDBRetryableError(TMDBError):
    """A failure worth retrying; raised again once the retries are spent."""


class TMDBRateLimitedError(TMDBRetryableError):
    """429. `retry_after` is the server's Retry-After in seconds, when it sent one."""

    def __init__(
        self,
        message: str,
        *,
        path: str = "",
        status_code: int | None = 429,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message, path=path, status_code=status_code)
        self.retry_after = retry_after


class TMDBServerError(TMDBRetryableError):
    """5xx from TMDB or its CDN."""


class TMDBUnavailableError(TMDBRetryableError):
    """The request did not complete: timeout, DNS or connection failure."""


class RateLimitTimeoutError(TMDBError):
    """The shared rate limiter would have to wait longer than the caller allows."""
