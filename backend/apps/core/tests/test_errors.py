"""RFC 9457 problem details for every API error (SPEC §10)."""

import json
from types import SimpleNamespace
from typing import Any

import pytest
from django.conf import settings
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404, HttpResponse
from django.test import Client, RequestFactory
from rest_framework import exceptions

from apps.core import errors
from apps.core.errors import (
    PROBLEM_CONTENT_TYPE,
    ErrorCode,
    ProblemError,
    flatten_errors,
    problem_exception_handler,
    problem_type,
)


def body(response: HttpResponse) -> Any:
    return json.loads(response.content)


def handle(exc: Exception, context: dict[str, Any] | None = None) -> Any:
    response = problem_exception_handler(exc, context or {})
    assert response is not None
    return response


def test_every_code_has_a_status_title_and_type() -> None:
    for code in ErrorCode:
        assert 400 <= errors.default_status(code) < 600
        assert errors.title_for(code)
    assert problem_type(ErrorCode.VALIDATION_ERROR) == "urn:smart-iptv:problem:validation-error"
    assert problem_type(ErrorCode.MFA_SETUP_REQUIRED) == (
        "urn:smart-iptv:problem:mfa-setup-required"
    )


def test_problem_error_answers_with_its_code_and_field_errors() -> None:
    response = handle(
        ProblemError(
            ErrorCode.DEVICE_LIMIT, "Two devices at most.", field_errors={"name": ("taken",)}
        )
    )
    assert response.status_code == 409
    assert response.content_type == PROBLEM_CONTENT_TYPE
    assert response.data == {
        "type": "urn:smart-iptv:problem:device-limit",
        "title": "Device limit reached",
        "status": 409,
        "code": "DEVICE_LIMIT",
        "detail": "Two devices at most.",
        "field_errors": {"name": ["taken"]},
        "field_error_codes": {"name": ["invalid"]},
    }


def test_problem_error_defaults_and_status_override() -> None:
    response = handle(ProblemError(ErrorCode.SUBSCRIPTION_EXPIRED, status=402))
    assert response.status_code == 402
    assert response.data["detail"] == "Subscription expired"
    assert "field_errors" not in response.data


def test_server_side_problem_errors_hide_their_detail() -> None:
    response = handle(ProblemError(ErrorCode.INTERNAL_ERROR, "db at 10.0.0.5 refused"))
    assert response.status_code == 500
    assert response.data["detail"] == "Internal error"


def test_validation_errors_are_flattened_into_field_errors() -> None:
    exc = exceptions.ValidationError(
        {
            "email": ["Enter a valid email address."],
            "profile": {"phone": ["Invalid phone."]},
            "devices": [{}, {"name": ["This field is required."]}],
            "non_field_errors": ["Plans don't match."],
        }
    )
    response = handle(exc)
    assert response.status_code == 400
    assert response.data["code"] == "VALIDATION_ERROR"
    assert response.data["detail"] == "Invalid input."
    assert response.data["field_errors"] == {
        "email": ["Enter a valid email address."],
        "profile.phone": ["Invalid phone."],
        "devices.1.name": ["This field is required."],
        "non_field_errors": ["Plans don't match."],
    }


def test_a_single_validation_message_becomes_the_detail() -> None:
    response = handle(exceptions.ValidationError({"value": ["Must be at most 10."]}))
    assert response.data["detail"] == "Must be at most 10."
    response = handle(exceptions.ValidationError("Nope."))
    assert response.data["field_errors"] == {"non_field_errors": ["Nope."]}


@pytest.mark.parametrize(
    ("exc", "status", "code"),
    [
        (exceptions.NotAuthenticated(), 401, "NOT_AUTHENTICATED"),
        (exceptions.AuthenticationFailed(), 401, "NOT_AUTHENTICATED"),
        (exceptions.PermissionDenied(), 403, "PERMISSION_DENIED"),
        (exceptions.NotFound(), 404, "NOT_FOUND"),
        (exceptions.MethodNotAllowed("DELETE"), 405, "METHOD_NOT_ALLOWED"),
        (exceptions.ParseError(), 400, "VALIDATION_ERROR"),
        (exceptions.UnsupportedMediaType("text/plain"), 415, "VALIDATION_ERROR"),
        (exceptions.NotAcceptable(), 406, "VALIDATION_ERROR"),
        (Http404(), 404, "NOT_FOUND"),
        (DjangoPermissionDenied(), 403, "PERMISSION_DENIED"),
    ],
)
def test_framework_exceptions_map_to_stable_codes(exc: Exception, status: int, code: str) -> None:
    response = handle(exc)
    assert response.status_code == status
    assert response.data["code"] == code
    assert response.data["status"] == status
    assert response.data["type"] == problem_type(ErrorCode(code))


def test_unauthenticated_answers_carry_www_authenticate() -> None:
    exc = exceptions.NotAuthenticated()
    exc.auth_header = 'Session realm="api"'  # type: ignore[attr-defined]
    assert handle(exc)["WWW-Authenticate"] == 'Session realm="api"'


def test_throttling_sets_retry_after() -> None:
    response = handle(exceptions.Throttled(wait=12.4))
    assert response.status_code == 429
    assert response.data["code"] == "RATE_LIMITED"
    assert response["Retry-After"] == "13"  # DRF rounds the wait up


class _Conflict(exceptions.APIException):
    status_code = 409
    default_detail = "Already exists."


class _Unavailable(exceptions.APIException):
    status_code = 503
    default_detail = "Upstream at 10.0.0.9 timed out."


class _Gone(exceptions.APIException):
    status_code = 410
    default_detail = "Gone."


def test_other_api_exceptions_map_by_status_and_hide_server_details() -> None:
    assert handle(_Conflict()).data["code"] == "CONFLICT"
    assert handle(_Gone()).data["code"] == "VALIDATION_ERROR"
    unavailable = handle(_Unavailable())
    assert unavailable.status_code == 503
    assert unavailable.data["code"] == "INTERNAL_ERROR"
    assert "10.0.0.9" not in unavailable.data["detail"]


def test_structured_detail_on_non_validation_errors_becomes_field_errors() -> None:
    data = handle(exceptions.ParseError({"body": ["Malformed JSON."]})).data
    assert data["detail"] == "Invalid request"
    assert data["field_errors"] == {"body": ["Malformed JSON."]}


def test_django_validation_errors_become_validation_problems() -> None:
    by_field = handle(DjangoValidationError({"email": ["Taken."], "__all__": ["Mismatch."]})).data
    assert by_field["field_errors"] == {"email": ["Taken."], "non_field_errors": ["Mismatch."]}
    plain = handle(DjangoValidationError(["One.", "Two."])).data
    assert plain["field_errors"] == {"non_field_errors": ["One.", "Two."]}
    assert plain["detail"] == "Invalid input."


def test_unexpected_exceptions_are_left_to_django() -> None:
    assert problem_exception_handler(RuntimeError("boom"), {}) is None


def test_browsable_api_errors_keep_their_html_content_type() -> None:
    request = SimpleNamespace(accepted_renderer=SimpleNamespace(format="api"))
    response = handle(exceptions.NotFound(), {"request": request})
    assert response.content_type is None
    json_request = SimpleNamespace(accepted_renderer=SimpleNamespace(format="json"))
    assert handle(exceptions.NotFound(), {"request": json_request}).content_type == (
        PROBLEM_CONTENT_TYPE
    )


def test_flatten_errors_handles_scalars_and_empty_lists() -> None:
    assert flatten_errors("Bad.") == {"non_field_errors": ["Bad."]}
    assert flatten_errors([]) == {}
    assert flatten_errors({"tags": {0: ["Not a string."]}}) == {"tags.0": ["Not a string."]}
    assert flatten_errors([["a"], ["b"]]) == {"0": ["a"], "1": ["b"]}


# --- Plain Django views -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("view", "status", "code"),
    [
        (errors.bad_request, 400, "VALIDATION_ERROR"),
        (errors.permission_denied, 403, "PERMISSION_DENIED"),
        (errors.page_not_found, 404, "NOT_FOUND"),
    ],
)
def test_django_error_handlers_answer_problem_json(view: Any, status: int, code: str) -> None:
    response = view(RequestFactory().get("/x"), Exception("internal detail"))
    assert response.status_code == status
    assert response["Content-Type"] == PROBLEM_CONTENT_TYPE
    assert b"internal detail" not in response.content
    assert body(response)["code"] == code


def test_server_error_handler_reveals_nothing() -> None:
    response = errors.server_error(RequestFactory().get("/x"))
    assert response.status_code == 500
    assert body(response) == {
        "type": "urn:smart-iptv:problem:internal-error",
        "title": "Internal error",
        "status": 500,
        "code": "INTERNAL_ERROR",
        "detail": "Internal error",
    }


def test_csrf_failures_answer_problem_json() -> None:
    response = errors.csrf_failure(RequestFactory().post("/x"), reason="CSRF cookie not set.")
    assert response.status_code == 403
    assert body(response)["code"] == "PERMISSION_DENIED"
    assert body(response)["detail"] == "CSRF verification failed."


@pytest.mark.parametrize("host", [settings.ADMIN_HOST, settings.APP_HOST, settings.API_HOST])
@pytest.mark.parametrize("method", ["get", "post", "delete"])
def test_unknown_api_paths_are_problem_404s_for_any_method(host: str, method: str) -> None:
    client = Client(enforce_csrf_checks=True)
    for path in ("/api/v1/does-not-exist", "/api", "/api/"):
        response = getattr(client, method)(path, headers={"host": host})
        assert response.status_code == 404, path
        assert response["Content-Type"] == PROBLEM_CONTENT_TYPE
        assert response.json()["code"] == "NOT_FOUND"


@pytest.mark.parametrize("host", [settings.ADMIN_HOST, settings.APP_HOST, settings.API_HOST])
def test_non_api_paths_use_the_problem_404_handler(host: str) -> None:
    response = Client().get("/not-an-api-path", headers={"host": host})
    assert response.status_code == 404
    assert response["Content-Type"] == PROBLEM_CONTENT_TYPE
