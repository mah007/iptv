"""Email through the admin's SMTP settings (CFG, ADR-0019): the backend reads them at
each connection, the sender, and the Integrations page's test email."""

import smtplib
import socket
import ssl
from typing import Any, ClassVar
from unittest import mock

import pytest
from django.core import mail as django_mail
from django.core.mail import EmailMessage, get_connection

from apps.accounts.models import User
from apps.core.integration import TestCode
from apps.core.services import set_setting
from apps.notifications import mail
from apps.notifications.email_backend import SettingsEmailBackend, SMTPNotConfiguredError

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("no_server_config")]

PASSWORD = "smtp-test-Pa55!"  # noqa: S105 (a fake secret)


class FakeSMTP:
    """smtplib.SMTP/SMTP_SSL for the backend: records the session, fails on request."""

    instances: ClassVar[list["FakeSMTP"]] = []
    fail_with: ClassVar[BaseException | None] = None

    def __init__(self, host: str, port: int, **kwargs: Any) -> None:
        if FakeSMTP.fail_with is not None and not isinstance(
            FakeSMTP.fail_with, smtplib.SMTPAuthenticationError
        ):
            raise FakeSMTP.fail_with
        self.host, self.port, self.kwargs = host, port, kwargs
        self.started_tls = False
        self.login_args: tuple[str, str] | None = None
        self.sent: list[tuple[str, list[str], bytes]] = []
        FakeSMTP.instances.append(self)

    def starttls(self, **kwargs: Any) -> None:
        self.started_tls = True

    def login(self, user: str, password: str) -> None:
        if isinstance(FakeSMTP.fail_with, smtplib.SMTPAuthenticationError):
            raise FakeSMTP.fail_with
        self.login_args = (user, password)

    def sendmail(self, sender: str, recipients: list[str], message: bytes) -> None:
        self.sent.append((sender, recipients, message))

    def quit(self) -> None:
        pass

    def close(self) -> None:
        pass


@pytest.fixture
def smtp() -> Any:
    FakeSMTP.instances = []
    FakeSMTP.fail_with = None
    with (
        mock.patch("smtplib.SMTP", FakeSMTP),
        mock.patch("smtplib.SMTP_SSL", FakeSMTP),
    ):
        yield FakeSMTP


def configure(security: str = "starttls", **extra: object) -> None:
    set_setting("email.smtp_host", "smtp.example.com", actor=None)
    set_setting("email.smtp_port", 587, actor=None)
    set_setting("email.smtp_security", security, actor=None)
    set_setting("email.smtp_username", "mailer", actor=None)
    set_setting("email.smtp_password", PASSWORD, actor=None)
    for key, value in extra.items():
        set_setting(f"email.{key}", value, actor=None)


def test_the_backend_reads_the_settings_at_each_connection(smtp: Any) -> None:
    configure()
    EmailMessage(
        "s", "b", "a@example.com", ["to@example.com"], connection=SettingsEmailBackend()
    ).send()
    first = smtp.instances[-1]
    assert (first.host, first.port, first.started_tls) == ("smtp.example.com", 587, True)
    assert first.login_args == ("mailer", PASSWORD)
    # An admin switches to SSL on 465: the next connection follows, no restart.
    set_setting("email.smtp_security", "ssl", actor=None)
    set_setting("email.smtp_port", 465, actor=None)
    EmailMessage(
        "s", "b", "a@example.com", ["to@example.com"], connection=SettingsEmailBackend()
    ).send()
    second = smtp.instances[-1]
    assert (second.port, second.started_tls) == (465, False)


def test_without_a_host_the_backend_refuses_to_connect() -> None:
    with pytest.raises(SMTPNotConfiguredError):
        SettingsEmailBackend().open()


def test_messages_without_a_sender_get_the_configured_one(smtp: Any) -> None:
    configure(from_address="Shop <shop@example.com>")
    message = EmailMessage("s", "b", None, ["to@example.com"])
    message.from_email = ""
    SettingsEmailBackend().send_messages([message])
    assert "shop@example.com" in smtp.instances[-1].sent[0][0]


def test_the_default_sender_uses_the_service_name(settings: Any) -> None:
    settings.DOMAIN = "tv.example.com"
    assert mail.from_address() == "Smart IPTV <no-reply@tv.example.com>"
    settings.DEFAULT_FROM_EMAIL = "Old <old@example.com>"
    assert mail.from_address() == "Old <old@example.com>"


def test_email_is_configured_once_a_host_is_set(settings: Any) -> None:
    settings.EMAIL_BACKEND = mail.SETTINGS_BACKEND
    assert mail.email_configured() is False
    settings.EMAIL_HOST = "smtp.env.example.com"
    assert mail.email_configured() is True
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.EMAIL_HOST = None
    assert mail.email_configured() is True


def test_the_test_email_goes_to_the_admin(smtp: Any, staff_user: User) -> None:
    configure()
    result = mail.send_test_email(staff_user.email)
    assert result.ok is True
    assert result.code is TestCode.OK
    reference = str(result.details["reference"])
    (_sender, recipients, raw) = smtp.instances[-1].sent[0]
    assert recipients == [staff_user.email]
    assert reference.encode() in raw
    assert PASSWORD not in repr(result)


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (smtplib.SMTPAuthenticationError(535, b"5.7.8 bad credentials"), TestCode.AUTH_FAILED),
        (ConnectionRefusedError(111, "refused"), TestCode.UNREACHABLE),
        (socket.gaierror(-2, "Name or service not known"), TestCode.UNREACHABLE),
        (TimeoutError("timed out"), TestCode.TIMEOUT),
        (ssl.SSLError("wrong version number"), TestCode.TLS_FAILED),
        (smtplib.SMTPServerDisconnected("closed"), TestCode.UNREACHABLE),
    ],
)
def test_test_email_failures_become_codes(
    smtp: Any, staff_user: User, error: BaseException, code: TestCode
) -> None:
    configure()
    smtp.fail_with = error
    result = mail.send_test_email(staff_user.email)
    assert (result.ok, result.code) == (False, code)
    assert PASSWORD not in result.message


def test_the_test_email_needs_a_host_and_a_recipient(staff_user: User) -> None:
    assert mail.send_test_email(staff_user.email).code is TestCode.NOT_CONFIGURED
    configure()
    assert mail.send_test_email("").code is TestCode.NO_RECIPIENT


def test_django_s_send_mail_uses_the_settings(smtp: Any, settings: Any) -> None:
    settings.EMAIL_BACKEND = mail.SETTINGS_BACKEND
    configure(security="none")
    assert get_connection().host == "smtp.example.com"
    django_mail.send_mail("s", "b", None, ["to@example.com"])
    assert smtp.instances[-1].started_tls is False
