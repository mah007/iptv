"""Security headers on every Django response, CSP reports and the SPA's media origins
(SPEC §11 headers; G-19, G-24, ADR-0021). A response without them fails here."""

import json
from urllib.parse import urlsplit

import pytest
from asgiref.sync import async_to_sync
from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.test import Client, RequestFactory

from apps.core import security
from apps.core.services import reset_settings_cache, set_setting

pytestmark = pytest.mark.django_db

HOSTS = [settings.ADMIN_HOST, settings.APP_HOST, settings.API_HOST, settings.TV_HOST]
#: One success, one problem and one anonymous refusal per public host.
PATHS = {
    settings.ADMIN_HOST: ["/api/v1/health", "/api/v1/nope", "/api/v1/auth/me"],
    settings.APP_HOST: ["/api/v1/health", "/api/v1/nope", "/api/v1/me"],
    settings.API_HOST: ["/api/v1/health", "/api/v1/nope", "/api/v1/me"],
    settings.TV_HOST: ["/player_api.php?username=x&password=y", "/get.php", "/nope"],
}


@pytest.mark.parametrize("host", HOSTS)
def test_every_response_carries_the_headers(host: str) -> None:
    for path in PATHS[host]:
        response = Client().get(path, headers={"host": host})
        assert response["X-Content-Type-Options"] == "nosniff", path
        assert "frame-ancestors 'none'" in response["Content-Security-Policy"], path
        assert "default-src 'none'" in response["Content-Security-Policy"], path
        assert "camera=()" in response["Permissions-Policy"], path
        assert response["Cross-Origin-Opener-Policy"] == "same-origin", path
        assert response["Referrer-Policy"], path


def test_referrer_policy_per_host() -> None:
    expected = {
        settings.TV_HOST: "no-referrer",  # G-19: Xtream URLs carry credentials
        settings.ADMIN_HOST: "same-origin",
        settings.APP_HOST: "strict-origin-when-cross-origin",
    }
    for host, policy in expected.items():
        response = Client().get(PATHS[host][0], headers={"host": host})
        assert response["Referrer-Policy"] == policy, host


def test_documents_keep_their_own_policy() -> None:
    policy = security.document_csp()
    assert "style-src 'unsafe-inline'" in policy
    assert "script-src" not in policy
    assert "frame-ancestors 'self'" in policy
    assert settings.MEDIA_BASE_URL.split("/")[2] in policy  # the logo on the media edge
    assert security.document_csp(sandbox=True).endswith("; sandbox")


def test_media_origins_for_the_spas() -> None:
    origins = security.media_origins()
    base = urlsplit(settings.MEDIA_BASE_URL)
    assert origins[0] == f"{base.scheme}://{base.netloc}"
    assert any(
        origin.startswith(f"{settings.PUBLIC_SCHEME}://*.{settings.DOMAIN}") for origin in origins
    )
    set_setting("security.csp_extra_media_origins", "https://cdn.example.net", actor=None)
    assert "https://cdn.example.net" in security.media_origins()
    response = Client().get("/internal/frontend-csp", headers={"host": "web"})
    assert response.status_code == 200
    assert "https://cdn.example.net" in response.content.decode().split("\n")
    assert (
        Client().get("/internal/frontend-csp", headers={"host": settings.APP_HOST}).status_code
        == 404
    )


@pytest.mark.parametrize("bad", ["https://a.b/path", "'unsafe-inline'", "https://x;y", "*"])
def test_extra_origins_are_strict(bad: str) -> None:
    from django.core.exceptions import ValidationError  # noqa: PLC0415

    with pytest.raises(ValidationError):
        set_setting("security.csp_extra_media_origins", bad, actor=None)


def test_csp_reports_are_counted_and_redacted(caplog: pytest.LogCaptureFixture) -> None:
    before = security.CSP_VIOLATIONS.labels(app="portal", directive="img-src")._value.get()
    legacy = {
        "csp-report": {
            "document-uri": "https://app.example.com/watch/movie/1?x=y",
            "effective-directive": "img-src",
            "blocked-uri": "https://media.example.com/v/k1.0123456789abcdef0123456789abcdef.t.compat.9.sig/x.mp4",
        }
    }
    response = Client().post(
        "/api/v1/csp-report",
        json.dumps(legacy),
        content_type="application/csp-report",
        headers={"host": settings.APP_HOST},
    )
    assert response.status_code == 204
    after = security.CSP_VIOLATIONS.labels(app="portal", directive="img-src")._value.get()
    assert after == before + 1
    assert "0123456789abcdef0123456789abcdef" not in caplog.text
    batch = [{"type": "csp-violation", "body": {"effectiveDirective": "script-src-elem"}}]
    response = Client().post(
        "/api/v1/csp-report",
        json.dumps(batch),
        content_type="application/reports+json",
        headers={"host": settings.ADMIN_HOST},
    )
    assert response.status_code == 204
    assert (
        Client().get("/api/v1/csp-report", headers={"host": settings.ADMIN_HOST}).status_code == 405
    )
    assert (
        Client()
        .post(
            "/api/v1/csp-report",
            "{}",
            content_type="application/json",
            headers={"host": settings.API_HOST},
        )
        .status_code
        == 404
    )


@pytest.mark.parametrize(("address", "status"), [("198.51.100.7", 403), ("203.0.113.9", 204)])
def test_the_allowlist_works_on_the_async_stack(address: str, status: int) -> None:
    """Right after a settings change the in-process cache is empty, so the allowlist
    reads its setting from the database: never from the event loop, where the ORM
    refuses to run (it answered 500 there)."""
    set_setting("security.admin_ip_allowlist", "203.0.113.0/24", actor=None)
    reset_settings_cache()

    async def get_response(request: HttpRequest) -> HttpResponse:
        return HttpResponse(status=204)

    middleware = security.AdminAllowlistMiddleware(get_response)
    request = RequestFactory().get(
        "/api/v1/auth/me", headers={"host": settings.ADMIN_HOST}, REMOTE_ADDR=address
    )
    request.urlconf = security.ADMIN_URLCONF  # type: ignore[attr-defined]  # set by HostURLConf
    assert async_to_sync(middleware)(request).status_code == status
