"""What an integration's "Test" action answers, and outbound HTTP for those checks
(CFG, ADR-0019).

A test runs on the server with the saved settings and a short timeout, and answers
a stable `code` the admin translates (en/ar) plus a short English message. Neither
ever holds a secret: messages are built here or are the provider's own error text,
redacted.
"""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

import httpx

from apps.core.redaction import redact_text

TIMEOUT_S: Final = 10.0
_MESSAGE_LENGTH: Final = 300
_transport: httpx.BaseTransport | None = None


class TestCode(StrEnum):
    """Why a test passed or failed."""

    __test__ = False  # not a pytest class

    OK = "ok"
    NOT_CONFIGURED = "not_configured"
    AUTH_FAILED = "auth_failed"
    FORBIDDEN = "forbidden"
    UNREACHABLE = "unreachable"
    TIMEOUT = "timeout"
    TLS_FAILED = "tls_failed"
    REJECTED = "rejected"
    NO_RECIPIENT = "no_recipient"
    CHAT_NOT_FOUND = "chat_not_found"
    UNEXPECTED = "unexpected"


@dataclass(frozen=True, slots=True)
class TestResult:
    """The outcome of one test; `details` holds harmless facts (a bot name, the mode)."""

    __test__ = False  # not a pytest class

    ok: bool
    code: TestCode
    message: str
    details: Mapping[str, str | bool] = field(default_factory=dict)

    @classmethod
    def passed(cls, message: str, **details: str | bool) -> "TestResult":
        return cls(True, TestCode.OK, message, details)

    @classmethod
    def failed(cls, code: TestCode, message: str, **details: str | bool) -> "TestResult":
        return cls(False, code, redact_text(message)[:_MESSAGE_LENGTH], details)


@contextmanager
def use_transport(transport: httpx.BaseTransport | None) -> Iterator[None]:
    """Route the checks' HTTP calls through `transport` (tests: canned responses)."""
    global _transport  # noqa: PLW0603 (process-wide test hook)
    previous, _transport = _transport, transport
    try:
        yield
    finally:
        _transport = previous


def http_client(**kwargs: Any) -> httpx.Client:
    """A client for one check: short timeouts, no redirects followed."""
    return httpx.Client(
        timeout=httpx.Timeout(TIMEOUT_S, connect=5.0),
        transport=_transport,
        follow_redirects=False,
        **kwargs,
    )


def from_http_error(service: str, exc: httpx.HTTPError) -> TestResult:
    """A transport failure as a result (never the URL: it may carry a token)."""
    if isinstance(exc, httpx.TimeoutException):
        return TestResult.failed(TestCode.TIMEOUT, f"{service} did not answer in time.")
    if isinstance(exc, httpx.ConnectError) and "SSL" in str(exc):
        return TestResult.failed(TestCode.TLS_FAILED, f"The TLS connection to {service} failed.")
    return TestResult.failed(
        TestCode.UNREACHABLE, f"{service} could not be reached ({type(exc).__name__})."
    )


def from_status(service: str, response: httpx.Response, reason: str = "") -> TestResult:
    """An HTTP error answer as a result."""
    detail = f": {reason}" if reason else ""
    if response.status_code == httpx.codes.UNAUTHORIZED:
        return TestResult.failed(TestCode.AUTH_FAILED, f"{service} refused the key{detail}.")
    if response.status_code == httpx.codes.FORBIDDEN:
        return TestResult.failed(TestCode.FORBIDDEN, f"{service} refused access{detail}.")
    return TestResult.failed(
        TestCode.REJECTED, f"{service} answered {response.status_code}{detail}."
    )
