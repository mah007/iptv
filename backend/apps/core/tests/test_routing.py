"""Each public host sees only its own URLconf; internal endpoints stay internal."""

import pytest
from asgiref.sync import async_to_sync
from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.test import Client, RequestFactory

from apps.core.middleware import HostURLConfMiddleware

API = settings.API_HOST
TV = settings.TV_HOST


@pytest.fixture
def client() -> Client:
    return Client()


def test_api_host_serves_service_info(client: Client) -> None:
    response = client.get("/", headers={"host": API})
    assert response.status_code == 200
    assert response.json() == {"service": "smart-iptv", "api": "/api/v1"}


def test_api_health_is_public_and_reveals_nothing(client: Client) -> None:
    response = client.get("/api/v1/health", headers={"host": API})
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert "no-cache" in response["Cache-Control"]


def test_tv_host_has_its_own_urlconf(client: Client) -> None:
    assert client.get("/health", headers={"host": TV}).status_code == 200
    assert client.get("/", headers={"host": TV}).status_code == 404
    assert client.get("/api/v1/health", headers={"host": TV}).status_code == 404


def test_port_in_host_header_is_ignored_for_routing(client: Client) -> None:
    assert client.get("/api/v1/health", headers={"host": f"{API}:8080"}).status_code == 200


@pytest.mark.parametrize("host", [API, TV])
def test_internal_endpoints_are_unreachable_from_public_hosts(client: Client, host: str) -> None:
    assert client.get("/internal/health/live", headers={"host": host}).status_code == 404
    assert client.get("/internal/health/ready", headers={"host": host}).status_code == 404


def test_internal_host_serves_internal_endpoints(client: Client) -> None:
    response = client.get("/internal/health/live", headers={"host": "web"})
    assert response.status_code == 200
    assert client.get("/api/v1/health", headers={"host": "web"}).status_code == 404


def test_unknown_host_is_rejected(client: Client) -> None:
    assert client.get("/", headers={"host": "evil.example.com"}).status_code == 400


def test_health_rejects_unsafe_methods(client: Client) -> None:
    assert client.post("/api/v1/health", headers={"host": API}).status_code == 405


def test_middleware_routes_on_the_async_stack() -> None:
    async def get_response(request: HttpRequest) -> HttpResponse:
        return HttpResponse(getattr(request, "urlconf", "root"))

    middleware = HostURLConfMiddleware(get_response)
    api_request = RequestFactory().get("/", headers={"host": API})
    internal_request = RequestFactory().get("/", headers={"host": "web"})
    assert async_to_sync(middleware)(api_request).content == b"config.urls_api"
    assert async_to_sync(middleware)(internal_request).content == b"root"
