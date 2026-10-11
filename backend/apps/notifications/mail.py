"""Email through the admin's SMTP settings (SPEC §7.7; CFG, ADR-0019).

The settings registry's `email.*` keys (with `.env`'s EMAIL_* as the fallback) are
read again for every connection, so a change in the admin applies at the next send
in every process. `SettingsEmailBackend` is Django's SMTP backend fed from them.
"""

import smtplib
import ssl
from dataclasses import dataclass
from secrets import token_hex
from typing import Final

from django.conf import settings
from django.core.mail import EmailMessage, get_connection

from apps.core.integration import TestCode, TestResult
from apps.core.services import get_secret, get_setting

SETTINGS_BACKEND: Final = "apps.notifications.email_backend.SettingsEmailBackend"
TEST_TIMEOUT_S: Final = 10


@dataclass(frozen=True, slots=True)
class SmtpConfig:
    host: str
    port: int
    security: str  # starttls | ssl | none
    username: str
    password: str
    timeout_s: int

    @property
    def use_tls(self) -> bool:
        return self.security == "starttls"

    @property
    def use_ssl(self) -> bool:
        return self.security == "ssl"


def smtp_config() -> SmtpConfig:
    return SmtpConfig(
        host=str(get_setting("email.smtp_host")),
        port=int(get_setting("email.smtp_port")),
        security=str(get_setting("email.smtp_security")),
        username=str(get_setting("email.smtp_username")),
        password=get_secret("email.smtp_password"),
        timeout_s=int(get_setting("email.timeout_s")),
    )


def email_configured() -> bool:
    """SMTP needs a host; Django's other backends (tests, console) always send."""
    if settings.EMAIL_BACKEND == SETTINGS_BACKEND:
        return bool(get_setting("email.smtp_host"))
    return True


def from_address() -> str:
    """The sender of every email: the admin's, else "<service name> <no-reply@domain>"."""
    configured = str(get_setting("email.from_address"))
    if configured:
        return configured
    return f"{get_setting('branding.service_name_en')} <no-reply@{settings.DOMAIN}>"


# SMTP and network errors, most specific first, as (types, code, message).
_FAILURES: Final[tuple[tuple[tuple[type[BaseException], ...], TestCode, str], ...]] = (
    (
        (smtplib.SMTPAuthenticationError,),
        TestCode.AUTH_FAILED,
        "The SMTP server refused the user name or password.",
    ),
    (
        (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused),
        TestCode.REJECTED,
        "The SMTP server refused the sender or the recipient.",
    ),
    (
        (smtplib.SMTPNotSupportedError,),
        TestCode.TLS_FAILED,
        "The SMTP server doesn't support STARTTLS; try SSL or none.",
    ),
    (
        (ssl.SSLError,),
        TestCode.TLS_FAILED,
        "The TLS handshake failed: check the port and the encryption.",
    ),
    ((TimeoutError,), TestCode.TIMEOUT, "The SMTP server did not answer in time."),
    (
        (smtplib.SMTPServerDisconnected,),
        TestCode.UNREACHABLE,
        "The SMTP server closed the connection: check the port and the encryption.",
    ),
    ((OSError,), TestCode.UNREACHABLE, "The SMTP server could not be reached."),
    ((smtplib.SMTPException,), TestCode.REJECTED, "The SMTP server refused the message."),
)


def _smtp_failure(exc: BaseException) -> TestResult:
    """An SMTP or network error as a test result (never a secret)."""
    for types, code, message in _FAILURES:
        if isinstance(exc, types):
            return TestResult.failed(code, f"{message} ({type(exc).__name__})")
    return TestResult.failed(TestCode.UNEXPECTED, f"Sending failed ({type(exc).__name__}).")


def send_test_email(to: str) -> TestResult:
    """Send a short test message to `to` with the saved SMTP settings, now."""
    if not to:
        return TestResult.failed(
            TestCode.NO_RECIPIENT, "Your admin account has no email address to send to."
        )
    config = smtp_config()
    if not config.host:
        return TestResult.failed(TestCode.NOT_CONFIGURED, "No SMTP server is set.")
    reference = token_hex(4)
    service = str(get_setting("branding.service_name_en"))
    message = EmailMessage(
        subject=f"Test email from {service} (ref {reference})",
        body=(
            f"This test message was sent from the {service} admin "
            f"(Settings, Integrations, Email) to check the SMTP settings.\n\n"
            f"Reference: {reference}\n"
        ),
        from_email=from_address(),
        to=[to],
        connection=get_connection(SETTINGS_BACKEND, timeout=min(config.timeout_s, TEST_TIMEOUT_S)),
    )
    try:
        message.send(fail_silently=False)
    except Exception as exc:  # every failure becomes a result
        return _smtp_failure(exc)
    return TestResult.passed(f"Sent to {to}.", reference=reference, recipient=to)
