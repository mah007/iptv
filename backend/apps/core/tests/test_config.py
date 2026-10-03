"""Settings that implement ADR-0004 (same-origin session auth) and ADR-0005 (foundations)."""

import importlib
import sys
from types import ModuleType

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from config.origins import default_port, origin, origins


@pytest.mark.parametrize(
    ("scheme", "port", "expected"),
    [
        ("http", 80, "http://admin.example.com"),
        ("http", 8080, "http://admin.example.com:8080"),
        ("https", 443, "https://admin.example.com"),
        ("https", 8443, "https://admin.example.com:8443"),
    ],
)
def test_origin_omits_default_ports(scheme: str, port: int, expected: str) -> None:
    assert origin(scheme, "admin.example.com", port) == expected


def test_origin_rejects_other_schemes() -> None:
    with pytest.raises(ImproperlyConfigured, match="PUBLIC_SCHEME"):
        default_port("ftp")


def test_csrf_trusts_exactly_the_admin_and_portal_origins() -> None:
    assert (
        origins(
            settings.PUBLIC_SCHEME, [settings.ADMIN_HOST, settings.APP_HOST], settings.PUBLIC_PORT
        )
        == settings.CSRF_TRUSTED_ORIGINS
    )
    assert settings.CSRF_TRUSTED_ORIGINS[0].startswith(f"http://{settings.ADMIN_HOST}")


def test_admin_and_portal_hosts_are_routed() -> None:
    assert settings.ADMIN_HOST in settings.ALLOWED_HOSTS
    assert settings.APP_HOST in settings.ALLOWED_HOSTS
    assert settings.HOST_URLCONFS[settings.ADMIN_HOST] == "config.urls_admin"
    assert settings.HOST_URLCONFS[settings.APP_HOST] == "config.urls_portal"


def test_session_and_csrf_cookies() -> None:
    assert settings.SESSION_COOKIE_HTTPONLY is True
    assert settings.SESSION_COOKIE_SAMESITE == "Lax"
    # SPEC §8.2: staff sessions end after 30 idle minutes.
    assert settings.SESSION_COOKIE_AGE == 30 * 60
    assert settings.SESSION_SAVE_EVERY_REQUEST is True
    # The SPA reads csrftoken and sends it back as X-CSRFToken.
    assert settings.CSRF_COOKIE_HTTPONLY is False
    assert settings.CSRF_FAILURE_VIEW == "apps.core.errors.csrf_failure"


def _fresh(module_name: str) -> ModuleType:
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)


def test_production_trusts_https_origins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PUBLIC_SCHEME", raising=False)
    monkeypatch.delenv("PUBLIC_PORT", raising=False)
    prod = _fresh("config.settings.prod")
    assert [
        f"https://{settings.ADMIN_HOST}",
        f"https://{settings.APP_HOST}",
    ] == prod.CSRF_TRUSTED_ORIGINS
    assert prod.SESSION_COOKIE_SECURE is True
    assert prod.CSRF_COOKIE_SECURE is True
    # Prometheus scrapes /metrics over plain HTTP inside the Docker network.
    assert r"^metrics$" in prod.SECURE_REDIRECT_EXEMPT


def test_development_uses_console_logs_and_the_browsable_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DJANGO_LOG_FORMAT", raising=False)
    dev = _fresh("config.settings.dev")
    assert dev.DEBUG is True
    assert dev.LOG_FORMAT == "console"
    assert (
        "rest_framework.renderers.BrowsableAPIRenderer"
        in (dev.REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"])
    )
    # The dev override never leaks into the settings other modules share.
    assert settings.REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] == [
        "rest_framework.renderers.JSONRenderer"
    ]


def test_rest_framework_defaults() -> None:
    rest = settings.REST_FRAMEWORK
    assert rest["DEFAULT_AUTHENTICATION_CLASSES"] == [
        "apps.core.authentication.SessionAuthentication"
    ]
    assert rest["DEFAULT_PERMISSION_CLASSES"] == ["rest_framework.permissions.IsAuthenticated"]
    assert rest["EXCEPTION_HANDLER"] == "apps.core.errors.problem_exception_handler"
    assert rest["DEFAULT_PAGINATION_CLASS"] == "apps.core.pagination.PageNumberPagination"
