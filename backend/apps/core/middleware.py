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
from apps.core.redaction import redact_text

type GetResponse = Callable[[HttpRequest], HttpResponseBase | Awaitable[HttpResponseBase]]

REQUEST_ID_HEADER = "X-Request-ID"
# Client-supplied request ids are kept only when they are short and plain.
_VALID_REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")
# Healthchecks and scrapes would flood the log; they are logged only when they fail.
_QUIET_PATHS = frozenset({"/internal/health/live", "/internal/health/ready", "/metrics"})

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
        if request.path in _QUIET_PATHS and status < 400:
            return
        fields: dict[str, Any] = {
            "method": request.method,
            "host": split_domain_port(request.META.get("HTTP_HOST", ""))[0][:255],
            "path": redact_text(request.path),
            "status": status,
            "latency_ms": round((time.perf_counter() - start) * 1000, 1),
        }
        user_id = _user_id(request)
        if user_id is not None:
            fields["user_id"] = user_id
        level = logging.ERROR if status >= 500 else logging.INFO
        request_log.log(level, "request", **fields)


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
