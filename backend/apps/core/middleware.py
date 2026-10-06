import logging
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any, cast

import structlog
from asgiref.sync import iscoroutinefunction, markcoroutinefunction
from django.conf import settings
from django.http import HttpRequest, HttpResponseBase
from django.http.request import split_domain_port
from django.utils.functional import SimpleLazyObject, empty

from apps.core.ids import uuid7
from apps.core.metrics import XTREAM_LATENCY, XTREAM_REQUESTS
from apps.core.redaction import redact_text

type GetResponse = Callable[[HttpRequest], HttpResponseBase | Awaitable[HttpResponseBase]]

REQUEST_ID_HEADER = "X-Request-ID"
# Client-supplied request ids are kept only when they are short and plain.
_VALID_REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")
# Healthchecks and scrapes would flood the log; they are logged only when they fail.
_QUIET_PATHS = frozenset({"/internal/health/live", "/internal/health/ready", "/metrics"})

# The Xtream host's URLconf (settings.HOST_URLCONFS) and the action labels of
# iptv_xtream_requests_total: player_api.php actions from this set, else a fixed name.
XTREAM_URLCONF = "config.urls_xtream"
XTREAM_ACTIONS = frozenset(
    {
        "get_account_info",
        "get_live_categories",
        "get_live_streams",
        "get_series",
        "get_series_categories",
        "get_series_info",
        "get_short_epg",
        "get_simple_data_table",
        "get_vod_categories",
        "get_vod_info",
        "get_vod_streams",
    }
)
_XTREAM_FIXED = {"/get.php": "m3u", "/xmltv.php": "xmltv"}
_XTREAM_PLAY_KINDS = {
    "movie": "play_movie",
    "series": "play_series",
    "live": "play_live",
    "timeshift": "play_timeshift",
}
# Xtream's short live form: /<user>/<pass>/<stream id>[.ext]
_XTREAM_SHORT_LIVE = re.compile(r"/[^/]+/[^/]+/[0-9]+(?:\.[A-Za-z0-9]{1,8})?")

request_log = structlog.get_logger("apps.request")


class RequestLogMiddleware:
    """One structured, redacted log line per request (SPEC §14), plus X-Request-ID.

    The request id comes from a valid incoming X-Request-ID, else a fresh UUIDv7.
    It is bound to structlog's contextvars for the request, so every log line the
    request produces carries it, and it is echoed in the response header. The
    path is redacted (Xtream URLs carry credentials); the query string is never
    logged. This replaces the server access logs, which print raw paths.

    The context is cleared when a request starts, not when it ends: Django logs
    4xx/5xx responses (`django.request`) after the middleware chain returns, and
    those lines must still carry the request id. Under ASGI every request runs in
    its own context anyway.
    """

    sync_capable = True
    async_capable = True

    def __init__(self, get_response: GetResponse) -> None:
        self.get_response = get_response
        if iscoroutinefunction(get_response):
            markcoroutinefunction(self)

    def __call__(self, request: HttpRequest) -> Any:
        if iscoroutinefunction(self):
            return self._acall(request)
        request_id, start = self._start(request)
        response = cast("HttpResponseBase", self.get_response(request))
        self._finish(request, response, request_id, start)
        return response

    async def _acall(self, request: HttpRequest) -> HttpResponseBase:
        request_id, start = self._start(request)
        response = await cast("Awaitable[HttpResponseBase]", self.get_response(request))
        self._finish(request, response, request_id, start)
        return response

    def _start(self, request: HttpRequest) -> tuple[str, float]:
        incoming = request.META.get("HTTP_X_REQUEST_ID", "")
        request_id = incoming if _VALID_REQUEST_ID.fullmatch(incoming) else uuid7().hex
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        return request_id, time.perf_counter()

    def _finish(
        self, request: HttpRequest, response: HttpResponseBase, request_id: str, start: float
    ) -> None:
        response[REQUEST_ID_HEADER] = request_id
        status = response.status_code
        elapsed = time.perf_counter() - start
        if getattr(request, "urlconf", None) == XTREAM_URLCONF:
            action = xtream_action(request)
            XTREAM_REQUESTS.labels(action, str(status)).inc()
            XTREAM_LATENCY.labels(action).observe(elapsed)
        if request.path in _QUIET_PATHS and status < 400:
            return
        fields: dict[str, Any] = {
            "method": request.method,
            "host": split_domain_port(request.META.get("HTTP_HOST", ""))[0][:255],
            "path": redact_text(request.path),
            "status": status,
            "latency_ms": round(elapsed * 1000, 1),
        }
        route = _route_pattern(request)
        if route:
            fields["route"] = route
        user_id = _user_id(request)
        if user_id is not None:
            fields["user_id"] = user_id
        level = logging.ERROR if status >= 500 else logging.INFO
        request_log.log(level, "request", **fields)


def xtream_action(request: HttpRequest) -> str:
    """The bounded action label of an Xtream request (never a raw parameter value)."""
    path = request.path
    if path == "/player_api.php":
        try:
            action = request.GET.get("action") or (
                request.POST.get("action") if request.method == "POST" else None
            )
        except Exception:  # an unreadable body is still a request to count
            action = None
        if not action:
            return "login"
        return action if action in XTREAM_ACTIONS else "other"
    if path in _XTREAM_FIXED:
        return _XTREAM_FIXED[path]
    kind = path.split("/", 2)[1] if path.count("/") >= 2 else ""
    if kind in _XTREAM_PLAY_KINDS:
        return _XTREAM_PLAY_KINDS[kind]
    return "play_live" if _XTREAM_SHORT_LIVE.fullmatch(path) else "other"


def _route_pattern(request: HttpRequest) -> str:
    """The matched URL pattern (`api/v1/titles/<uuid:pk>`), never the path itself.

    Regex patterns (`re_path`) are long and unreadable in a log, so those give the
    view name instead (`xtream-play-movie`).
    """
    match = getattr(request, "resolver_match", None)
    if match is None:
        return ""
    route = str(getattr(match, "route", "") or "")
    if not route or "(?P<" in route or route.startswith("^"):
        route = str(getattr(match, "view_name", "") or "")
    return route[:200]


def _user_id(request: HttpRequest) -> str | None:
    """The authenticated user's id, without forcing a session or database lookup.

    AuthenticationMiddleware installs a lazy user; if nothing in the request
    evaluated it, the request was not authenticated by session and we don't look.
    """
    user = request.__dict__.get("user")
    if user is None:
        return None
    if isinstance(user, SimpleLazyObject) and cast("Any", user)._wrapped is empty:
        return None
    if getattr(user, "is_authenticated", False):
        return str(user.pk)
    return None


class HostURLConfMiddleware:
    """Serve each public host from its own URLconf (settings.HOST_URLCONFS).

    Any other allowed host, such as the in-network `web` name, keeps ROOT_URLCONF,
    which holds only internal endpoints. So `api.<domain>/internal/...` is a 404
    even if a proxy rule were ever misconfigured. Supports sync and async stacks.
    """

    sync_capable = True
    async_capable = True

    def __init__(self, get_response: GetResponse) -> None:
        self.get_response = get_response
        self.urlconfs = {host.lower(): conf for host, conf in settings.HOST_URLCONFS.items()}
        if iscoroutinefunction(get_response):
            markcoroutinefunction(self)

    def __call__(self, request: HttpRequest) -> Any:
        self._route(request)
        if iscoroutinefunction(self):
            return self._acall(request)
        return self.get_response(request)

    async def _acall(self, request: HttpRequest) -> HttpResponseBase:
        return await cast("Awaitable[HttpResponseBase]", self.get_response(request))

    def _route(self, request: HttpRequest) -> None:
        host, _port = split_domain_port(request.get_host())
        urlconf = self.urlconfs.get(host)
        if urlconf is not None:
            request.urlconf = urlconf  # type: ignore[attr-defined]  # Django reads it; stubs omit it
