"""Where admin-supplied URLs may point (threat model G-11, ADR-0017).

Live sources, guide feeds, logos and integrations are URLs an admin types. The
worker and the packager fetch them from inside our network, so a URL must never
reach our own services or the host's sensitive addresses:

- refused always, after DNS resolution (every address a name resolves to):
  loopback, link-local (169.254.0.0/16, fe80::/10, so also the cloud metadata
  addresses 169.254.169.254 and fd00:ec2::254), unspecified, multicast and reserved
  addresses;
- refused always: our own stack's hosts (postgres, redis-state, web, the edge, ...)
  by name, and any address those names resolve to;
- allowed: everything else, including private (RFC 1918) networks, because
  operators run their encoders, ErsatzTV and MediaMTX on the LAN or in Docker. The
  host of a configured integration is allowed even when it shares an address range.

httpx clients re-check every request, redirects included (`guarded_client`). ffmpeg
follows a source's redirects and an HLS source's segment URLs by itself; only the
first host is checked there (with `-protocol_whitelist` limiting what it may open).
"""

import contextlib
import ipaddress
import socket
import time
from collections.abc import Callable, Iterable
from threading import Lock
from urllib.parse import urlsplit

import httpx

#: Our own services (docker/compose*.yml). Names only: their addresses are resolved too.
INTERNAL_HOSTS = frozenset(
    {
        "postgres",
        "redis-state",
        "redis-cache",
        "meilisearch",
        "web",
        "worker",
        "beat",
        "watcher",
        "transcoder",
        "nginx-stream",
        "traefik",
        "live",
        "live-relay",
        "frontend",
        "migrate",
        "media-init",
        "mailpit",
        "localhost",
    }
)
_METADATA = frozenset(
    {ipaddress.ip_address("169.254.169.254"), ipaddress.ip_address("fd00:ec2::254")}
)
_INTERNAL_TTL_S = 60.0

type Address = ipaddress.IPv4Address | ipaddress.IPv6Address
type Resolver = Callable[[str, int | None], list[str]]


class UnsafeDestination(ValueError):
    """The URL points somewhere we never fetch from (the message names the reason only)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def resolve(host: str, port: int | None) -> list[str]:
    """Every address `host` resolves to (raises OSError when it does not resolve)."""
    infos = socket.getaddrinfo(host, port or 80, proto=socket.IPPROTO_TCP)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


class _InternalAddresses:
    """The addresses our services resolve to, refreshed every minute."""

    def __init__(self) -> None:
        self.lock = Lock()
        self.until = 0.0
        self.addresses: frozenset[Address] = frozenset()

    def get(self, resolver: Resolver) -> frozenset[Address]:
        with self.lock:
            if time.monotonic() < self.until:
                return self.addresses
            found: set[Address] = set()
            for name in INTERNAL_HOSTS - {"localhost"}:
                with contextlib.suppress(OSError, ValueError):
                    found.update(ipaddress.ip_address(item) for item in resolver(name, None))
            self.addresses = frozenset(found)
            self.until = time.monotonic() + _INTERNAL_TTL_S
            return self.addresses


INTERNAL = _InternalAddresses()


def _plain(address: Address) -> Address:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def forbidden_address(address: Address) -> str | None:
    """Why this address is never fetched from, or None."""
    plain = _plain(address)
    if plain in _METADATA:
        return "metadata_address"
    if plain.is_loopback:
        return "loopback"
    if plain.is_link_local:
        return "link_local"
    if plain.is_unspecified or plain.is_multicast or plain.is_reserved:
        return "special_address"
    return None


def check_host(
    host: str,
    port: int | None = None,
    *,
    allowed: Iterable[str] = (),
    resolver: Resolver = resolve,
) -> None:
    """Raise UnsafeDestination unless `host` (and every address it has) may be fetched."""
    name = (host or "").strip().rstrip(".").lower().strip("[]")
    if not name:
        raise UnsafeDestination("no_host")
    if name in {item.lower() for item in allowed}:
        return
    if name in INTERNAL_HOSTS or name.endswith(".localhost"):
        raise UnsafeDestination("internal_host")
    try:
        literal: Address | None = ipaddress.ip_address(name)
    except ValueError:
        literal = None
    if literal is not None:
        addresses = [literal]
    else:
        try:
            addresses = [ipaddress.ip_address(item) for item in resolver(name, port)]
        except (OSError, ValueError):
            raise UnsafeDestination("unresolvable") from None
    internal = INTERNAL.get(resolver)
    for address in addresses:
        reason = forbidden_address(address)
        if reason is not None:
            raise UnsafeDestination(reason)
        if _plain(address) in internal or address in internal:
            raise UnsafeDestination("internal_host")


def _no_resolution(host: str, port: int | None) -> list[str]:
    return []


def check_url_static(url: str) -> None:
    """The checks that need no DNS (internal names, literal addresses), for saving a URL
    where the web process cannot resolve it; fetches run the full `check_url`."""
    parts = urlsplit(url)
    name = (parts.hostname or "").lower()
    if name in INTERNAL_HOSTS or name.endswith(".localhost"):
        raise UnsafeDestination("internal_host")
    try:
        literal = ipaddress.ip_address(name)
    except ValueError:
        return
    reason = forbidden_address(literal)
    if reason is not None:
        raise UnsafeDestination(reason)


def check_url(url: str, *, allowed: Iterable[str] = (), resolver: Resolver = resolve) -> None:
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        raise UnsafeDestination("bad_port") from None
    check_host(parts.hostname or "", port, allowed=allowed, resolver=resolver)


def guarded_client(
    *, allowed: Iterable[str] = (), resolver: Resolver = resolve, **options: object
) -> httpx.Client:
    """An httpx client that checks every request it sends, redirects included."""
    hosts = tuple(allowed)

    def check(request: httpx.Request) -> None:
        check_host(request.url.host, request.url.port, allowed=hosts, resolver=resolver)

    return httpx.Client(event_hooks={"request": [check]}, **options)  # type: ignore[arg-type]


def integration_hosts() -> set[str]:
    """Hosts of the configured LiveIntegrations: channel sources may point at them."""
    from apps.live.models import LiveIntegration  # noqa: PLC0415 (Django models)

    hosts: set[str] = set()
    for base, stream in LiveIntegration.objects.values_list("base_url", "stream_base_url"):
        for url in (base, stream):
            host = urlsplit(url or "").hostname
            if host:
                hosts.add(host.lower())
    return hosts
