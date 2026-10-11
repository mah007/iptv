"""HTTP security headers, the admin IP allowlist and CSP reports (SPEC §11; ADR-0021).

- **`SecurityHeadersMiddleware`** completes every Django response per host: a
  `default-src 'none'` Content-Security-Policy unless the view set its own (documents
  such as invoices do), a Permissions-Policy, Cross-Origin-Opener-Policy, and the
  Referrer-Policy of the host: `no-referrer` on the Xtream host (G-19; its URLs carry
  credentials), `same-origin` on the admin, `strict-origin-when-cross-origin` elsewhere.
  The SPAs' own policies come from the frontend's nginx (production) and Vite (dev),
  built by `frontend/packages/ui/csp.ts`; the media edge sets its own.
- **`AdminAllowlistMiddleware`** refuses the admin host's API (and the monitoring
  sign-in, `apps.accounts.monitoring`) to addresses outside
  `security.admin_ip_allowlist` when that setting is not empty (G-40). Recovery:
  `manage.py set_setting security.admin_ip_allowlist --stdin </dev/null` on the host.
- **`csp_report`** receives browsers' violation reports for both SPAs, logs a redacted
  line and counts them (`iptv_csp_violations_total`), so a policy that breaks something
  in production shows up without anyone running the end-to-end suites there.
"""

import ipaddress
import json
import logging
from collections.abc import Awaitable, Callable
from functools import lru_cache
from typing import Any, Final, cast
from urllib.parse import urlsplit

import redis
from asgiref.sync import iscoroutinefunction, markcoroutinefunction
from django.conf import settings
from django.http import HttpRequest, HttpResponse, HttpResponseBase
from django.views.decorators.csrf import csrf_exempt
from prometheus_client import Counter

from apps.core.errors import ErrorCode, problem_response
from apps.core.http import client_ip
from apps.core.redaction import redact_text
from apps.core.services import get_setting
from apps.core.stores import cache_redis

logger = logging.getLogger(__name__)

type GetResponse = Callable[[HttpRequest], HttpResponseBase | Awaitable[HttpResponseBase]]

#: Responses that carry no policy of their own: nothing may load, frame or submit.
API_CSP: Final = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
PERMISSIONS_POLICY: Final = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
ADMIN_URLCONF: Final = "config.urls_admin"
PORTAL_URLCONF: Final = "config.urls_portal"
XTREAM_URLCONF: Final = "config.urls_xtream"
_REFERRER_BY_URLCONF: Final = {
    XTREAM_URLCONF: "no-referrer",
    ADMIN_URLCONF: "same-origin",
}

CSP_VIOLATIONS = Counter(
    "iptv_csp_violations_total",
    "Content-Security-Policy violations reported by browsers, by app and directive.",
    ("app", "directive"),
)
_DIRECTIVES: Final = frozenset(
    {
        "default-src",
        "script-src",
        "script-src-elem",
        "script-src-attr",
        "style-src",
        "style-src-elem",
        "style-src-attr",
        "img-src",
        "font-src",
        "connect-src",
        "media-src",
        "frame-src",
        "child-src",
        "worker-src",
        "manifest-src",
        "form-action",
        "frame-ancestors",
        "base-uri",
        "object-src",
        "require-trusted-types-for",
        "trusted-types",
    }
)
_REPORT_MAX_BYTES: Final = 16 * 1024
_REPORTS_PER_MINUTE: Final = 60


def _urlconf(request: HttpRequest) -> str | None:
    return cast("str | None", getattr(request, "urlconf", None))


# --- Origins the SPAs may load media from (shared with the frontend build) -------------------


def media_origins() -> list[str]:
    """The media edge's origin, any subdomain of DOMAIN (edges, `media.`), and the admin's
    `security.csp_extra_media_origins` (a CDN, standalone edges elsewhere)."""
    base = urlsplit(str(settings.MEDIA_BASE_URL))
    found = [f"{base.scheme}://{base.netloc}"] if base.scheme and base.netloc else []
    scheme = str(settings.PUBLIC_SCHEME)
    port = int(settings.PUBLIC_PORT)
    default = 443 if scheme == "https" else 80
    suffix = "" if port == default else f":{port}"
    found.append(f"{scheme}://*.{settings.DOMAIN}{suffix}")
    found.extend(extra_media_origins())
    return list(dict.fromkeys(found))


def extra_media_origins() -> list[str]:
    raw = str(get_setting("security.csp_extra_media_origins"))
    return [item.strip() for item in raw.split(",") if item.strip()]


def document_csp(*, frame_ancestors: str = "'self'", sandbox: bool = False) -> str:
    """For HTML documents Django renders (invoices, message previews): inline styles and
    images from the media edge (logos), nothing else; never scripts."""
    images = " ".join(["data:", *media_origins()])
    policy = (
        f"default-src 'none'; style-src 'unsafe-inline'; img-src {images}; "
        f"frame-ancestors {frame_ancestors}; base-uri 'none'; form-action 'none'"
    )
    return f"{policy}; sandbox" if sandbox else policy


# --- Middleware -------------------------------------------------------------------------------


class _Middleware:
    sync_capable = True
    async_capable = True

    def __init__(self, get_response: GetResponse) -> None:
        self.get_response = get_response
        if iscoroutinefunction(get_response):
            markcoroutinefunction(self)

    def __call__(self, request: HttpRequest) -> Any:
        if iscoroutinefunction(self):
            return self._acall(request)
        early = self.before(request)
        if early is not None:
            return self.after(request, early)
        response = cast("HttpResponseBase", self.get_response(request))
        return self.after(request, response)

    async def _acall(self, request: HttpRequest) -> HttpResponseBase:
        early = self.before(request)
        if early is not None:
            return self.after(request, early)
        response = await cast("Awaitable[HttpResponseBase]", self.get_response(request))
        return self.after(request, response)

    def before(self, request: HttpRequest) -> HttpResponseBase | None:
        return None

    def after(self, request: HttpRequest, response: HttpResponseBase) -> HttpResponseBase:
        return response


class SecurityHeadersMiddleware(_Middleware):
    """Per-host security headers on every Django response (see the module docstring)."""

    def after(self, request: HttpRequest, response: HttpResponseBase) -> HttpResponseBase:
        if "Content-Security-Policy" not in response:
            response["Content-Security-Policy"] = API_CSP
        if "Permissions-Policy" not in response:
            response["Permissions-Policy"] = PERMISSIONS_POLICY
        if "Cross-Origin-Opener-Policy" not in response:
            response["Cross-Origin-Opener-Policy"] = "same-origin"
        response["X-Content-Type-Options"] = "nosniff"
        referrer = _REFERRER_BY_URLCONF.get(_urlconf(request) or "")
        if referrer is not None:
            response["Referrer-Policy"] = referrer
        elif "Referrer-Policy" not in response:
            response["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response


@lru_cache(maxsize=8)
def _networks(raw: str) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    networks = []
    for part in (item.strip() for item in raw.split(",")):
        if not part:
            continue
        try:
            networks.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            logger.warning("admin_ip_allowlist holds an invalid entry; ignored")
    return tuple(networks)


def allowlist_permits(address: str | None, raw: str | None = None) -> bool:
    """Whether the admin allowlist lets `address` in; an empty list lets everyone in."""
    text = str(get_setting("security.admin_ip_allowlist")) if raw is None else raw
    networks = _networks(text.strip())
    if not networks:
        return True
    if address is None:
        return False
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return any(ip in network for network in networks if network.version == ip.version)


def ip_not_allowed() -> HttpResponse:
    return problem_response(
        ErrorCode.IP_NOT_ALLOWED,
        "The admin can't be used from this network. Ask an owner to add it to the allowlist.",
    )


class AdminAllowlistMiddleware(_Middleware):
    """The optional admin IP allowlist, on every request of the admin host (G-40)."""

    def before(self, request: HttpRequest) -> HttpResponseBase | None:
        if _urlconf(request) != ADMIN_URLCONF:
            return None
        # Health and branding are public facts the sign-in page needs.
        if request.path in ("/api/v1/health", "/api/v1/branding"):
            return None
        if allowlist_permits(client_ip(request)):
            return None
        logger.info("admin.ip_refused", extra={"path": redact_text(request.path)})
        return ip_not_allowed()


# --- CSP reports ------------------------------------------------------------------------------


def _report_items(body: bytes) -> list[dict[str, Any]]:
    """The violation objects of a legacy `application/csp-report` body or a Reporting API
    `application/reports+json` batch."""
    try:
        data = json.loads(body)
    except ValueError:
        return []
    if isinstance(data, dict) and isinstance(data.get("csp-report"), dict):
        return [data["csp-report"]]
    if isinstance(data, list):
        return [
            item["body"]
            for item in data[:20]
            if isinstance(item, dict)
            and item.get("type") == "csp-violation"
            and isinstance(item.get("body"), dict)
        ]
    return []


def _directive(item: dict[str, Any]) -> str:
    raw = str(
        item.get("effective-directive")
        or item.get("effectiveDirective")
        or item.get("violated-directive")
        or ""
    )
    name = raw.split(" ", 1)[0].strip().lower()
    return name if name in _DIRECTIVES else "other"


def _over_budget(ip: str | None) -> bool:
    if ip is None:
        return False
    key = f"csp:reports:{ip}"
    try:
        pipe = cache_redis().pipeline()
        pipe.incr(key)
        pipe.expire(key, 60, nx=True)
        count, _ = pipe.execute()
    except redis.RedisError:
        return False
    return int(count) > _REPORTS_PER_MINUTE


@csrf_exempt
def csp_report(request: HttpRequest) -> HttpResponse:
    """POST /api/v1/csp-report on the admin and portal hosts: 204, whatever it holds."""
    if request.method != "POST":
        return problem_response(ErrorCode.METHOD_NOT_ALLOWED)
    if int(request.META.get("CONTENT_LENGTH") or 0) > _REPORT_MAX_BYTES:
        return HttpResponse(status=204)
    if _over_budget(client_ip(request)):
        return HttpResponse(status=204)
    app = "admin" if _urlconf(request) == ADMIN_URLCONF else "portal"
    for item in _report_items(request.body[:_REPORT_MAX_BYTES]):
        directive = _directive(item)
        CSP_VIOLATIONS.labels(app=app, directive=directive).inc()
        blocked = str(item.get("blocked-uri") or item.get("blockedURL") or "")[:200]
        document = urlsplit(str(item.get("document-uri") or item.get("documentURL") or ""))
        logger.warning(
            "csp.violation",
            extra={
                "app": app,
                "directive": directive,
                "blocked": redact_text(blocked.split("?", 1)[0]),
                "page": redact_text(document.path[:200]),
            },
        )
    return HttpResponse(status=204)


def frontend_csp(request: HttpRequest) -> HttpResponse:
    """GET /internal/frontend-csp: the media origins for the SPAs' policy, one per line.

    The frontend container reads it once at start (docker/nginx/30-csp.sh), so a changed
    `security.csp_extra_media_origins` applies after the next update or frontend restart."""
    if request.method != "GET":
        return problem_response(ErrorCode.METHOD_NOT_ALLOWED)
    response = HttpResponse("\n".join(media_origins()) + "\n", content_type="text/plain")
    response["Cache-Control"] = "no-store"
    return response
