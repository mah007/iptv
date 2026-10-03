from collections.abc import Awaitable, Callable
from typing import Any, cast

from asgiref.sync import iscoroutinefunction, markcoroutinefunction
from django.conf import settings
from django.http import HttpRequest, HttpResponseBase
from django.http.request import split_domain_port

type GetResponse = Callable[[HttpRequest], HttpResponseBase | Awaitable[HttpResponseBase]]


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
