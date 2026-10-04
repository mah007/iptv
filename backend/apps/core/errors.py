"""RFC 9457 problem details for every API error (SPEC §10).

Body: `{type, title, status, code, detail, field_errors?, field_error_codes?}` served
as `application/problem+json`. `code` is a stable machine-readable value from
`ErrorCode`; clients switch on it, never on `detail` (human text) or `title`.
`field_error_codes` mirrors `field_errors` with a stable code per message (ADR-0015).

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
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    # Playback refusals (SPEC §7.4), in check order alongside the codes above.
    ACCOUNT_SUSPENDED = "ACCOUNT_SUSPENDED"
    DEVICE_NOT_APPROVED = "DEVICE_NOT_APPROVED"
    IP_BLOCKED = "IP_BLOCKED"
    GEO_BLOCKED = "GEO_BLOCKED"
    CONTENT_TYPE_NOT_ALLOWED = "CONTENT_TYPE_NOT_ALLOWED"
    QUALITY_NOT_ALLOWED = "QUALITY_NOT_ALLOWED"
    LICENSE_EXPIRED = "LICENSE_EXPIRED"
    # Billing (SPEC §7.6, ADR-0012).
    TRIAL_NOT_ELIGIBLE = "TRIAL_NOT_ELIGIBLE"
    PAYMENT_PROVIDER_UNAVAILABLE = "PAYMENT_PROVIDER_UNAVAILABLE"
    PAYMENT_PROVIDER_ERROR = "PAYMENT_PROVIDER_ERROR"
    WEBHOOK_INVALID = "WEBHOOK_INVALID"


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
    ErrorCode.PROVIDER_UNAVAILABLE: (503, "Metadata provider unavailable"),
    ErrorCode.ACCOUNT_SUSPENDED: (403, "Account suspended"),
    ErrorCode.DEVICE_NOT_APPROVED: (403, "Device not approved"),
    ErrorCode.IP_BLOCKED: (403, "Address blocked"),
    ErrorCode.GEO_BLOCKED: (403, "Not available in this country"),
    ErrorCode.CONTENT_TYPE_NOT_ALLOWED: (403, "Content type not in plan"),
    ErrorCode.QUALITY_NOT_ALLOWED: (403, "Quality not in plan"),
    ErrorCode.LICENSE_EXPIRED: (403, "Title no longer available"),
    # Billing (ADR-0012).
    ErrorCode.TRIAL_NOT_ELIGIBLE: (409, "Free trial not available"),
    ErrorCode.PAYMENT_PROVIDER_UNAVAILABLE: (503, "Payment method unavailable"),
    ErrorCode.PAYMENT_PROVIDER_ERROR: (502, "Payment provider error"),
    ErrorCode.WEBHOOK_INVALID: (400, "Invalid webhook"),
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
        # Messages made with `field_error(...)` (or DRF's ErrorDetail) carry their code.
        self.field_error_codes: FieldErrors = {
            name: [_code_of(message) for message in messages]
            for name, messages in (field_errors or {}).items()
        }
        super().__init__(detail=detail if detail is not None else title_for(code), code=code)


# --- Field error codes (ADR-0015) ----------------------------------------------------
# `field_error_codes` mirrors `field_errors`: the same keys, one stable code per message,
# in the same order. Clients translate the codes (the messages are English) and show the
# message only for a code they don't know.

#: The code of a message that names none (DRF's default validation code).
DEFAULT_FIELD_ERROR_CODE = "invalid"


def field_error(message: str, *, code: str) -> exceptions.ErrorDetail:
    """A field message with its stable code, for `ProblemError(field_errors=...)`."""
    return exceptions.ErrorDetail(message, code=code)


def _code_of(message: object) -> str:
    """The code of a DRF ErrorDetail or Django ValidationError; the default for plain text."""
    code = getattr(message, "code", None)
    return str(code) if code else DEFAULT_FIELD_ERROR_CODE


def _aligned_codes(messages: Sequence[str], codes: Sequence[str]) -> list[str]:
    """Exactly one code per message: missing ones are the default, extra ones dropped."""
    padding = [DEFAULT_FIELD_ERROR_CODE] * max(0, len(messages) - len(codes))
    return [*codes[: len(messages)], *padding]


@dataclass(frozen=True, slots=True)
class Problem:
    code: ErrorCode
    status: int
    detail: str
    field_errors: FieldErrors = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    field_error_codes: FieldErrors = field(default_factory=dict)

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
            body["field_error_codes"] = {
                name: _aligned_codes(messages, self.field_error_codes.get(name, ()))
                for name, messages in self.field_errors.items()
            }
        return body


def _flatten(detail: Any, prefix: str = "") -> dict[str, list[Any]]:
    """Flatten DRF/Django error structures into `{"a.b.0.c": [message objects]}`."""
    if isinstance(detail, Mapping):
        flat: dict[str, list[Any]] = {}
        for name, value in detail.items():
            path = f"{prefix}.{name}" if prefix else str(name)
            for key, messages in _flatten(value, path).items():
                flat.setdefault(key, []).extend(messages)
        return flat
    if isinstance(detail, list | tuple):
        if all(not isinstance(item, Mapping | list | tuple) for item in detail):
            return {prefix or NON_FIELD_ERRORS: list(detail)} if detail else {}
        flat = {}
        for index, item in enumerate(detail):
            path = f"{prefix}.{index}" if prefix else str(index)
            for key, messages in _flatten(item, path).items():
                flat.setdefault(key, []).extend(messages)
        return flat
    return {prefix or NON_FIELD_ERRORS: [detail]}


def flatten_errors(detail: Any, prefix: str = "") -> FieldErrors:
    """Flatten DRF/Django error structures into `{"a.b.0.c": [messages]}`."""
    return {key: [str(item) for item in items] for key, items in _flatten(detail, prefix).items()}


def flatten_error_codes(detail: Any, prefix: str = "") -> FieldErrors:
    """The codes of `flatten_errors(detail)`: same keys, one code per message."""
    return {
        key: [_code_of(item) for item in items] for key, items in _flatten(detail, prefix).items()
    }


def _validation_problem(
    field_errors: FieldErrors, status: int = 400, codes: FieldErrors | None = None
) -> Problem:
    messages = [message for group in field_errors.values() for message in group]
    detail = messages[0] if len(messages) == 1 else str(exceptions.ValidationError.default_detail)
    return Problem(
        ErrorCode.VALIDATION_ERROR, status, detail, field_errors, field_error_codes=codes or {}
    )


def _django_validation_errors(exc: DjangoValidationError) -> FieldErrors:
    if hasattr(exc, "error_dict"):
        return {
            (NON_FIELD_ERRORS if name == "__all__" else name): list(messages)
            for name, messages in exc.message_dict.items()
        }
    return {NON_FIELD_ERRORS: list(exc.messages)}


def _django_validation_codes(exc: DjangoValidationError) -> FieldErrors:
    """The codes of `_django_validation_errors(exc)`, aligned with its messages."""
    if hasattr(exc, "error_dict"):
        return {
            (NON_FIELD_ERRORS if name == "__all__" else name): [_code_of(error) for error in group]
            for name, group in exc.error_dict.items()
        }
    return {NON_FIELD_ERRORS: [_code_of(error) for error in exc.error_list]}


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
        return Problem(
            exc.problem_code,
            status,
            detail,
            exc.field_errors,
            headers,
            field_error_codes=exc.field_error_codes,
        )
    if isinstance(exc, exceptions.ValidationError):
        return _validation_problem(
            flatten_errors(exc.detail), status, flatten_error_codes(exc.detail)
        )
    if status >= 500:
        return Problem(ErrorCode.INTERNAL_ERROR, status, title_for(ErrorCode.INTERNAL_ERROR))

    code = next(
        (code for exc_type, code in _CODE_BY_EXCEPTION if isinstance(exc, exc_type)),
        _CODE_BY_STATUS.get(status, ErrorCode.VALIDATION_ERROR),
    )
    raw_detail = exc.detail
    if isinstance(raw_detail, list | dict):
        # ParseError and friends can carry structured detail; keep it as field errors.
        return Problem(
            code,
            status,
            title_for(code),
            flatten_errors(raw_detail),
            headers,
            field_error_codes=flatten_error_codes(raw_detail),
        )
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
        return _validation_problem(
            _django_validation_errors(exc), codes=_django_validation_codes(exc)
        )
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
