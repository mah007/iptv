"""Django's SMTP backend fed from the settings registry (CFG, ADR-0019).

`EMAIL_BACKEND` points here. Each instance (Django makes one per `send_mail` or
`get_connection`) reads the admin's SMTP settings, so changes apply at the next
send without a restart. Messages without a sender get `mail.from_address()`.
"""

from collections.abc import Sequence
from typing import Any

from django.core.mail import EmailMessage
from django.core.mail.backends.smtp import EmailBackend


class SMTPNotConfiguredError(RuntimeError):
    """No SMTP host is set (Settings → Integrations → Email)."""


class SettingsEmailBackend(EmailBackend):
    def __init__(self, **kwargs: Any) -> None:
        from apps.notifications.mail import smtp_config  # noqa: PLC0415 (settings need apps)

        config = smtp_config()
        configured = {
            "host": config.host,
            "port": config.port,
            "username": config.username,
            "password": config.password,
            "use_tls": config.use_tls,
            "use_ssl": config.use_ssl,
            "timeout": config.timeout_s,
        }
        # Values a caller passes (`get_connection(timeout=...)`) win over the settings.
        for name, value in configured.items():
            if kwargs.get(name) is None:
                kwargs[name] = value
        super().__init__(**kwargs)

    def open(self) -> bool | None:
        if not self.host:
            raise SMTPNotConfiguredError("No SMTP server is set.")
        return super().open()

    def send_messages(self, email_messages: Sequence[EmailMessage]) -> int:
        from apps.notifications.mail import from_address  # noqa: PLC0415

        sender = ""
        for message in email_messages:
            if not message.from_email:
                sender = sender or from_address()
                message.from_email = sender
        return super().send_messages(email_messages)
