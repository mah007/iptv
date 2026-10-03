"""Typed access to environment variables for settings modules.

Every setting that varies by deployment comes from the environment (12-factor).
Missing required values fail loudly at startup instead of silently defaulting.
"""

import os

from django.core.exceptions import ImproperlyConfigured

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def env(name: str, default: str | None = None) -> str:
    """Return the variable, or `default`; raise if neither is set."""
    value = os.environ.get(name)
    if value is None or value == "":
        if default is None:
            msg = f"Missing required environment variable {name}"
            raise ImproperlyConfigured(msg)
        return default
    return value


def env_bool(name: str, *, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    lowered = value.strip().lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    msg = f"Environment variable {name} must be a boolean, got {value!r}"
    raise ImproperlyConfigured(msg)


def env_int(name: str, *, default: int) -> int:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        msg = f"Environment variable {name} must be an integer, got {value!r}"
        raise ImproperlyConfigured(msg) from exc


def env_list(name: str, default: list[str]) -> list[str]:
    """Comma-separated list; blank items are dropped."""
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return list(default)
    return [item.strip() for item in value.split(",") if item.strip()]
