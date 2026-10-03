"""RFC 9457 problem details for every API error (SPEC §10).

Body: `{type, title, status, code, detail, field_errors?}` served as
`application/problem+json`. `code` is a stable machine-readable value from
`ErrorCode`; clients switch on it, never on `detail` (human text) or `title`.

- DRF views: `problem_exception_handler` (REST_FRAMEWORK["EXCEPTION_HANDLER"]).
- Everything else: the `handler400/403/404/500` views wired into the admin, api
  and portal URLconfs, the CSRF failure view, and a catch-all for unknown `/api/`
  paths that answers problem+json even when DEBUG would show an HTML page.

5xx responses never carry exception text: it can hold hosts, SQL or secrets.
The exception itself is logged (redacted) by Django's `django.request` logger.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404, HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from rest_framework import exceptions
from rest_framework.response import Response
from rest_framework.settings import api_settings
from rest_framework.views import set_rollback

PROBLEM_CONTENT_TYPE = "application/problem+json"
PROBLEM_TYPE_PREFIX = "urn:smart-iptv:problem:"


class ErrorCode(StrEnum):
    """Stable error codes. Adding one is fine; renaming or removing one breaks clients."""

    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_AUTHENTICATED = "NOT_AUTHENTICATED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    NOT_FOUND = "NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    RATE_LIMITED = "RATE_LIMITED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    ACCOUNT_LOCKED = "ACCOUNT_LOCKED"
    MFA_REQUIRED = "MFA_REQUIRED"
    MFA_SETUP_REQUIRED = "MFA_SETUP_REQUIRED"
    MFA_INVALID = "MFA_INVALID"
    SUBSCRIPTION_EXPIRED = "SUBSCRIPTION_EXPIRED"
    CONCURRENCY_LIMIT = "CONCURRENCY_LIMIT"
    CATEGORY_NOT_ALLOWED = "CATEGORY_NOT_ALLOWED"
    DEVICE_BLOCKED = "DEVICE_BLOCKED"
    DEVICE_LIMIT = "DEVICE_LIMIT"
    TITLE_PREPARING = "TITLE_PREPARING"
    CONFLICT = "CONFLICT"


# Default HTTP status and RFC 9457 title per code; a raise site may override the status.
_DEFAULTS: dict[ErrorCode, tuple[int, str]] = {
    ErrorCode.VALIDATION_ERROR: (400, "Invalid request"),
    ErrorCode.NOT_AUTHENTICATED: (401, "Authentication required"),
    ErrorCode.PERMISSION_DENIED: (403, "Permission denied"),
    ErrorCode.NOT_FOUND: (404, "Not found"),
    ErrorCode.METHOD_NOT_ALLOWED: (405, "Method not allowed"),
    ErrorCode.RATE_LIMITED: (429, "Too many requests"),
    ErrorCode.INTERNAL_ERROR: (500, "Internal error"),
    ErrorCode.INVALID_CREDENTIALS: (400, "Invalid credentials"),
    ErrorCode.ACCOUNT_LOCKED: (429, "Account temporarily locked"),
    ErrorCode.MFA_REQUIRED: (403, "Multi-factor authentication required"),
    ErrorCode.MFA_SETUP_REQUIRED: (403, "Multi-factor authentication setup required"),
    ErrorCode.MFA_INVALID: (400, "Invalid verification code"),
    ErrorCode.SUBSCRIPTION_EXPIRED: (403, "Subscription expired"),
    ErrorCode.CONCURRENCY_LIMIT: (409, "Stream limit reached"),
    ErrorCode.CATEGORY_NOT_ALLOWED: (403, "Category not in plan"),
    ErrorCode.DEVICE_BLOCKED: (403, "Device blocked"),
    ErrorCode.DEVICE_LIMIT: (409, "Device limit reached"),
    ErrorCode.TITLE_PREPARING: (409, "Title is being prepared"),
    ErrorCode.CONFLICT: (409, "Conflict"),
}

# Key for errors that belong to no single field (DRF's convention).
NON_FIELD_ERRORS = api_settings.NON_FIELD_ERRORS_KEY

type FieldErrors = dict[str, list[str]]


def default_status(code: ErrorCode) -> int:
    return _DEFAULTS[code][0]


def title_for(code: ErrorCode) -> str:
    return _DEFAULTS[code][1]


def problem_type(code: ErrorCode) -> str:
    """`urn:smart-iptv:problem:validation-error` for VALIDATION_ERROR."""
    return PROBLEM_TYPE_PREFIX + code.value.lower().replace("_", "-")


class ProblemError(exceptions.APIException):
    """Raise from services or views to answer with a specific problem code."""

    def __init__(
        self,
        code: ErrorCode,
        detail: str | None = None,
        *,
        status: int | None = None,
        field_errors: Mapping[str, Sequence[str]] | None = None,
    ) -> None:
        self.problem_code = code
        self.status_code = status if status is not None else default_status(code)
        self.field_errors: FieldErrors = {
            name: [str(message) for message in messages]
            for name, messages in (field_errors or {}).items()
        }
        super().__init__(detail=detail if detail is not None else title_for(code), code=code)


@dataclass(frozen=True, slots=True)
class Problem:
    code: ErrorCode
    status: int
    detail: str
    field_errors: FieldErrors = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)

    def body(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "type": problem_type(self.code),
            "title": title_for(self.code),
            "status": self.status,
            "code": self.code.value,
            "detail": self.detail,
        }
        if self.field_errors:
            body["field_errors"] = self.field_errors
        return body


def flatten_errors(detail: Any, prefix: str = "") -> FieldErrors:
    """Flatten DRF/Django error structures into `{"a.b.0.c": [messages]}`."""
    if isinstance(detail, Mapping):
        flat: FieldErrors = {}
        for name, value in detail.items():
            path = f"{prefix}.{name}" if prefix else str(name)
            for key, messages in flatten_errors(value, path).items():
                flat.setdefault(key, []).extend(messages)
        return flat
    if isinstance(detail, list | tuple):
        if all(not isinstance(item, Mapping | list | tuple) for item in detail):
            return {prefix or NON_FIELD_ERRORS: [str(item) for item in detail]} if detail else {}
        flat = {}
        for index, item in enumerate(detail):
            path = f"{prefix}.{index}" if prefix else str(index)
            for key, messages in flatten_errors(item, path).items():
                flat.setdefault(key, []).extend(messages)
        return flat
    return {prefix or NON_FIELD_ERRORS: [str(detail)]}


def _validation_problem(field_errors: FieldErrors, status: int = 400) -> Problem:
    messages = [message for group in field_errors.values() for message in group]
    detail = messages[0] if len(messages) == 1 else str(exceptions.ValidationError.default_detail)
    return Problem(ErrorCode.VALIDATION_ERROR, status, detail, field_errors)


def _django_validation_errors(exc: DjangoValidationError) -> FieldErrors:
    if hasattr(exc, "error_dict"):
        return {
            (NON_FIELD_ERRORS if name == "__all__" else name): list(messages)
            for name, messages in exc.message_dict.items()
        }
    return {NON_FIELD_ERRORS: list(exc.messages)}


_CODE_BY_EXCEPTION: tuple[tuple[type[exceptions.APIException], ErrorCode], ...] = (
    (exceptions.NotAuthenticated, ErrorCode.NOT_AUTHENTICATED),
    (exceptions.AuthenticationFailed, ErrorCode.NOT_AUTHENTICATED),
    (exceptions.PermissionDenied, ErrorCode.PERMISSION_DENIED),
    (exceptions.NotFound, ErrorCode.NOT_FOUND),
    (exceptions.MethodNotAllowed, ErrorCode.METHOD_NOT_ALLOWED),
    (exceptions.Throttled, ErrorCode.RATE_LIMITED),
)

_CODE_BY_STATUS: dict[int, ErrorCode] = {
    401: ErrorCode.NOT_AUTHENTICATED,
    403: ErrorCode.PERMISSION_DENIED,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.METHOD_NOT_ALLOWED,
    409: ErrorCode.CONFLICT,
    429: ErrorCode.RATE_LIMITED,
}


def _api_exception_problem(exc: exceptions.APIException) -> Problem:
    status = exc.status_code
    headers: dict[str, str] = {}
    auth_header = getattr(exc, "auth_header", None)
    if auth_header:
        headers["WWW-Authenticate"] = str(auth_header)
    wait = getattr(exc, "wait", None)
    if wait:
        headers["Retry-After"] = str(int(wait))

    if isinstance(exc, ProblemError):
        detail = str(exc.detail) if status < 500 else title_for(exc.problem_code)
        return Problem(exc.problem_code, status, detail, exc.field_errors, headers)
    if isinstance(exc, exceptions.ValidationError):
        return _validation_problem(flatten_errors(exc.detail), status)
    if status >= 500:
        return Problem(ErrorCode.INTERNAL_ERROR, status, title_for(ErrorCode.INTERNAL_ERROR))

    code = next(
        (code for exc_type, code in _CODE_BY_EXCEPTION if isinstance(exc, exc_type)),
        _CODE_BY_STATUS.get(status, ErrorCode.VALIDATION_ERROR),
    )
    raw_detail = exc.detail
    if isinstance(raw_detail, list | dict):
        # ParseError and friends can carry structured detail; keep it as field errors.
        return Problem(code, status, title_for(code), flatten_errors(raw_detail), headers)
    return Problem(code, status, str(raw_detail), headers=headers)


def to_problem(exc: BaseException) -> Problem | None:
    """Map an exception to a problem; None for unexpected errors, which Django turns into 500s."""
    if isinstance(exc, Http404):
        return Problem(ErrorCode.NOT_FOUND, 404, str(exceptions.NotFound.default_detail))
    if isinstance(exc, DjangoPermissionDenied):
        return Problem(
            ErrorCode.PERMISSION_DENIED, 403, str(exceptions.PermissionDenied.default_detail)
        )
    if isinstance(exc, DjangoValidationError):
        return _validation_problem(_django_validation_errors(exc))
    if isinstance(exc, exceptions.APIException):
        return _api_exception_problem(exc)
    return None


def _renders_json(context: Mapping[str, Any]) -> bool:
    """False when the browsable API (dev only) renders the error as HTML."""
    request = context.get("request")
    renderer = getattr(request, "accepted_renderer", None)
    return renderer is None or getattr(renderer, "format", "json") == "json"


def problem_exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    """DRF EXCEPTION_HANDLER: problem+json for every API exception."""
    problem = to_problem(exc)
    if problem is None:
        return None
    set_rollback()
    return Response(
        problem.body(),
        status=problem.status,
        headers=problem.headers,
        content_type=PROBLEM_CONTENT_TYPE if _renders_json(context) else None,
    )


def problem_response(
    code: ErrorCode, detail: str | None = None, *, status: int | None = None
) -> JsonResponse:
    """A problem+json response for plain Django views."""
    resolved_status = status if status is not None else default_status(code)
    problem = Problem(code, resolved_status, detail if detail is not None else title_for(code))
    return JsonResponse(problem.body(), status=resolved_status, content_type=PROBLEM_CONTENT_TYPE)


# --- Django error handlers (handler400/403/404/500 in the API URLconfs) ------------


def bad_request(request: HttpRequest, exception: Exception) -> JsonResponse:
    return problem_response(ErrorCode.VALIDATION_ERROR, "Bad request.")


def permission_denied(request: HttpRequest, exception: Exception) -> JsonResponse:
    return problem_response(ErrorCode.PERMISSION_DENIED)


def page_not_found(request: HttpRequest, exception: Exception) -> JsonResponse:
    return problem_response(ErrorCode.NOT_FOUND)


def server_error(request: HttpRequest) -> JsonResponse:
    return problem_response(ErrorCode.INTERNAL_ERROR)


def csrf_failure(request: HttpRequest, reason: str = "") -> JsonResponse:
    """CSRF_FAILURE_VIEW: Django's CSRF middleware rejected an unsafe request."""
    return problem_response(ErrorCode.PERMISSION_DENIED, "CSRF verification failed.")


@csrf_exempt
def api_not_found(request: HttpRequest, *args: object, **kwargs: object) -> JsonResponse:
    """Catch-all for unknown `/api/` paths: problem+json in every mode, any method."""
    return problem_response(ErrorCode.NOT_FOUND)
