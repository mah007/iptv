"""Public origins of the browser apps, for CSRF_TRUSTED_ORIGINS (ADR-0004)."""

from collections.abc import Iterable

from django.core.exceptions import ImproperlyConfigured

_DEFAULT_PORTS = {"http": 80, "https": 443}


def default_port(scheme: str) -> int:
    try:
        return _DEFAULT_PORTS[scheme]
    except KeyError:
        msg = f"PUBLIC_SCHEME must be http or https, got {scheme!r}"
        raise ImproperlyConfigured(msg) from None


def origin(scheme: str, host: str, port: int) -> str:
    """`scheme://host[:port]`, leaving out the scheme's default port as browsers do."""
    if port == default_port(scheme):
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def origins(scheme: str, hosts: Iterable[str], port: int) -> list[str]:
    return [origin(scheme, host, port) for host in hosts]
