"""The logging pipeline: JSON in production, redacted everywhere, one line per request."""

import io
import json
import logging
from collections.abc import Iterator
from typing import Any
from unittest import mock

import pytest
import structlog
from asgiref.sync import async_to_sync
from celery import signals
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest, HttpResponse
from django.test import Client, RequestFactory
from django.utils.functional import SimpleLazyObject
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.core.logs import logging_config, parse_log_format
from apps.core.middleware import REQUEST_ID_HEADER, RequestLogMiddleware
from config.celery import use_django_logging

USERNAME = "alice"
PASSWORD = "s3cr3t-pass"  # noqa: S105 (a fake credential the test hunts for)


@pytest.fixture
def log_output() -> Iterator[io.StringIO]:
    """Everything that reaches the root logger, rendered by the configured formatter."""
    root = logging.getLogger()
    console = next(handler for handler in root.handlers if handler.get_name() == "console")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(console.formatter)
    root.addHandler(handler)
    yield stream
    root.removeHandler(handler)


def json_lines(stream: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


def request_lines(stream: io.StringIO) -> list[dict[str, Any]]:
    return [line for line in json_lines(stream) if line.get("logger") == "apps.request"]


# --- Acceptance: SPEC §11 "a test fails if a raw password reaches a log" --------------


@pytest.mark.django_db
def test_no_raw_credentials_in_logs(log_output: io.StringIO) -> None:
    url = f"/movie/{USERNAME}/{PASSWORD}/1.mp4?password={PASSWORD}&username={USERNAME}"
    response = Client().get(url, headers={"host": settings.TV_HOST})
    assert response.status_code == 404

    output = log_output.getvalue()
    assert PASSWORD not in output
    assert USERNAME not in output
    # Positive control: the request was logged, redacted, by both the middleware
    # and Django's own request logger.
    lines = json_lines(log_output)
    assert any(line.get("path") == "/movie/***/***/1.mp4" for line in lines)
    assert any(line.get("event") == "Not Found: /movie/***/***/1.mp4" for line in lines)


@pytest.mark.django_db
def test_no_raw_credentials_in_logged_tracebacks(log_output: io.StringIO, staff_user: User) -> None:
    client = APIClient(raise_request_exception=False)
    client.force_login(staff_user)
    boom = RuntimeError(f"cannot reach redis://:{PASSWORD}@redis-state password={PASSWORD}")
    with mock.patch("apps.core.services.list_settings", side_effect=boom):
        response = client.get("/api/v1/admin/settings", headers={"host": settings.ADMIN_HOST})

    assert response.status_code == 500
    assert response["Content-Type"] == "application/problem+json"
    assert PASSWORD not in response.content.decode()
    assert "RuntimeError" not in response.content.decode()
    output = log_output.getvalue()
    assert "Traceback" in output
    assert "RuntimeError" in output
    assert PASSWORD not in output


def test_stdlib_and_structlog_records_share_the_redacting_pipeline(
    log_output: io.StringIO,
) -> None:
    logging.getLogger("tests.stdlib").warning("token=%s for %s", "abc123", "/v/tok3n/a.ts")
    structlog.get_logger("tests.structlog").info(
        "login", password=PASSWORD, path=f"/live/{USERNAME}/{PASSWORD}/1.ts"
    )
    try:
        raise ValueError(f"password={PASSWORD}")
    except ValueError:
        logging.getLogger("tests.exc").exception("failed")

    output = log_output.getvalue()
    assert "abc123" not in output
    assert "tok3n" not in output
    assert PASSWORD not in output
    stdlib, structured, failure = json_lines(log_output)
    assert stdlib["event"] == "token=*** for /v/***/a.ts"
    assert stdlib["level"] == "warning"
    assert stdlib["logger"] == "tests.stdlib"
    assert structured == {
        "event": "login",
        "password": "***",
        "path": "/live/***/***/1.ts",
        "level": "info",
        "logger": "tests.structlog",
        "timestamp": structured["timestamp"],
    }
    assert "ValueError: password=***" in failure["exception"]


# --- Configuration -------------------------------------------------------------------


def test_logs_go_to_stderr_only() -> None:
    config = logging_config(level="INFO", log_format="json")
    assert config["handlers"]["console"]["stream"] == "ext://sys.stderr"
    assert config["root"]["handlers"] == ["console"]


def test_django_loggers_lose_their_unredacted_default_handlers() -> None:
    config = logging_config(level="INFO", log_format="console")
    assert config["loggers"]["django"]["propagate"] is True
    assert "handlers" not in config["loggers"]["django"]
    assert logging.getLogger("django").handlers == []
    assert logging.getLogger("django.server").handlers == []


def test_production_renders_json_and_development_renders_console() -> None:
    json_processors = logging_config(level="INFO", log_format="json")["formatters"]["structlog"]
    console_processors = logging_config(level="INFO", log_format="console")["formatters"][
        "structlog"
    ]
    assert isinstance(json_processors["processors"][-1], structlog.processors.JSONRenderer)
    assert isinstance(console_processors["processors"][-1], structlog.dev.ConsoleRenderer)
    assert settings.LOG_FORMAT == "json"  # tests run the production format


def test_log_format_is_validated() -> None:
    assert parse_log_format("json") == "json"
    assert parse_log_format("console") == "console"
    with pytest.raises(ImproperlyConfigured, match="DJANGO_LOG_FORMAT"):
        parse_log_format("xml")


def test_celery_keeps_django_logging() -> None:
    # A receiver on setup_logging stops Celery from replacing the root handlers.
    responses = signals.setup_logging.send(
        sender=None, loglevel="INFO", logfile=None, format="", colorize=False
    )
    assert any(receiver is use_django_logging for receiver, _ in responses)


# --- Request logging middleware ----------------------------------------------------------


@pytest.mark.django_db
def test_every_request_gets_a_request_id_and_one_log_line(log_output: io.StringIO) -> None:
    response = Client().get("/api/v1/health", headers={"host": settings.API_HOST})
    request_id = response[REQUEST_ID_HEADER]
    assert len(request_id) == 32
    (line,) = request_lines(log_output)
    assert line["event"] == "request"
    assert line["method"] == "GET"
    assert line["host"] == settings.API_HOST
    assert line["path"] == "/api/v1/health"
    assert line["status"] == 200
    assert line["latency_ms"] >= 0
    assert line["request_id"] == request_id
    assert line["level"] == "info"
    assert "user_id" not in line


@pytest.mark.parametrize(
    ("incoming", "kept"),
    [
        ("req-123_abc.def:9", True),
        ("bad id with spaces", False),
        ("x" * 129, False),
        ("evil\nnewline", False),
    ],
)
def test_incoming_request_ids_are_kept_only_when_plain(incoming: str, kept: bool) -> None:
    response = Client().get(
        "/api/v1/health", headers={"host": settings.API_HOST, "x-request-id": incoming}
    )
    assert (response[REQUEST_ID_HEADER] == incoming) is kept


@pytest.mark.django_db
def test_log_lines_inside_a_request_carry_its_request_id(log_output: io.StringIO) -> None:
    response = Client().get("/nope", headers={"host": settings.TV_HOST})
    request_id = response[REQUEST_ID_HEADER]
    django_line = next(
        line for line in json_lines(log_output) if line.get("logger") == "django.request"
    )
    assert django_line["request_id"] == request_id


@pytest.mark.django_db
def test_request_context_never_leaks_into_the_next_request(
    log_output: io.StringIO, staff_user: User
) -> None:
    client = APIClient()
    client.force_login(staff_user)
    client.get("/api/v1/admin/settings", headers={"host": settings.ADMIN_HOST})
    Client().get("/api/v1/health", headers={"host": settings.API_HOST})
    authenticated, anonymous = request_lines(log_output)
    assert authenticated["user_id"] == str(staff_user.pk)
    assert "user_id" not in anonymous
    assert anonymous["request_id"] != authenticated["request_id"]


@pytest.mark.django_db
def test_authenticated_requests_log_the_user(log_output: io.StringIO, staff_user: User) -> None:
    client = APIClient()
    client.force_login(staff_user)
    client.get("/api/v1/admin/settings", headers={"host": settings.ADMIN_HOST})
    (line,) = request_lines(log_output)
    assert line["status"] == 200
    assert line["user_id"] == str(staff_user.pk)


def test_quiet_paths_are_logged_only_when_they_fail(log_output: io.StringIO) -> None:
    client = Client()
    assert client.get("/internal/health/live", headers={"host": "web"}).status_code == 200
    assert request_lines(log_output) == []
    assert client.post("/internal/health/live", headers={"host": "web"}).status_code == 405
    (line,) = request_lines(log_output)
    assert line["status"] == 405


def test_server_errors_log_at_error_level(log_output: io.StringIO) -> None:
    client = Client(raise_request_exception=False)
    with mock.patch("apps.core.views.run_checks", side_effect=RuntimeError("down")):
        response = client.get("/internal/health/ready", headers={"host": "web"})
    assert response.status_code == 500
    (line,) = request_lines(log_output)
    assert line["level"] == "error"
    assert line["status"] == 500


def test_unknown_hosts_are_logged_without_raising(log_output: io.StringIO) -> None:
    response = Client().get("/", headers={"host": "evil.example.com"})
    assert response.status_code == 400
    (line,) = request_lines(log_output)
    assert line["host"] == "evil.example.com"
    assert line["status"] == 400


def test_middleware_logs_on_the_async_stack(log_output: io.StringIO) -> None:
    async def get_response(request: HttpRequest) -> HttpResponse:
        structlog.contextvars.bind_contextvars(user_id="u-1")
        return HttpResponse(status=204)

    middleware = RequestLogMiddleware(get_response)
    request = RequestFactory().get(
        "/movie/alice/s3cr3t/1.mp4", headers={"host": settings.TV_HOST, "x-request-id": "abc"}
    )
    response = async_to_sync(middleware)(request)
    assert response[REQUEST_ID_HEADER] == "abc"
    (line,) = request_lines(log_output)
    assert line["path"] == "/movie/***/***/1.mp4"
    assert line["request_id"] == "abc"
    assert line["user_id"] == "u-1"
    assert line["status"] == 204


def test_user_lookup_never_forces_a_lazy_user(log_output: io.StringIO) -> None:
    loader = mock.Mock(side_effect=AssertionError("the user must not be loaded"))

    def get_response(request: HttpRequest) -> HttpResponse:
        request.user = SimpleLazyObject(loader)  # type: ignore[assignment]
        return HttpResponse()

    RequestLogMiddleware(get_response)(RequestFactory().get("/x", headers={"host": "web"}))
    (line,) = request_lines(log_output)
    assert "user_id" not in line
    loader.assert_not_called()
