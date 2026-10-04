"""The outbox: enqueue in the caller's transaction, deliver over email with retries,
wait while SMTP is not configured, and password links that are never stored."""

import smtplib
from collections.abc import Callable
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

import pytest
from django.core import mail
from django.core.mail import EmailMultiAlternatives
from django.utils import timezone
from pytest_django import Settings

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.conftest import CustomerFactory
from apps.core.errors import ProblemError
from apps.notifications import services, tasks
from apps.notifications.models import NotificationOutbox, NotificationTemplate, OutboxStatus

pytestmark = pytest.mark.django_db
type Capture = Callable[..., Any]

SMTP = "django.core.mail.backends.smtp.EmailBackend"


@pytest.fixture
def customer(make_customer: CustomerFactory) -> User:
    return make_customer(name="Sara Otaibi")


def queue(user: User, key: str = "expired", **payload: Any) -> NotificationOutbox:
    rows = services.enqueue(user, key, {"plan_name": "Premium", **payload})
    return rows[0]


def test_enqueue_sends_after_commit(
    customer: User, django_capture_on_commit_callbacks: Capture
) -> None:
    with mock.patch.object(tasks.send_notification, "delay") as delay:
        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            row = queue(customer)
        assert delay.call_count == 0
        for callback in callbacks:
            callback()
    delay.assert_called_once_with(str(row.pk))
    assert row.locale == "ar"
    assert row.status == OutboxStatus.QUEUED


def test_deliver_renders_in_the_customers_language(customer: User) -> None:
    row = queue(customer)
    assert tasks.send_notification(str(row.pk)) == OutboxStatus.SENT
    row.refresh_from_db()
    assert (row.status, row.attempts, row.to_address) == ("sent", 1, customer.email)
    assert row.sent_at is not None
    message = mail.outbox[0]
    assert message.to == [customer.email]
    assert message.subject == row.subject == "انتهى اشتراكك في سمارت IPTV"
    assert "Premium" in message.body
    html = cast(EmailMultiAlternatives, message).alternatives[0]
    assert 'dir="rtl"' in str(html[0])
    # Already sent: nothing happens twice.
    assert services.deliver(row.pk) is None
    assert len(mail.outbox) == 1


def test_the_admins_template_wins(customer: User) -> None:
    services.save_template(
        key="expired",
        channel="email",
        locale="ar",
        values={"subject": "انتهى {{ plan_name }}", "body_text": "مرحباً {{ name }}"},
        actor=None,
    )
    tasks.send_notification(str(queue(customer).pk))
    assert mail.outbox[0].subject == "انتهى Premium"
    assert mail.outbox[0].body == "مرحباً Sara Otaibi"


def test_secrets_never_reach_the_outbox(customer: User) -> None:
    row = queue(customer, password="hunter2", note="token=abc123")  # noqa: S106
    assert row.payload["password"] == "***"  # noqa: S105
    assert "abc123" not in row.payload["note"]


def test_deduplicated_events(customer: User) -> None:
    services.enqueue(customer, "expiring_7d", {}, dedupe_key="exp7:1")
    assert services.enqueue(customer, "expiring_7d", {}, dedupe_key="exp7:1") == []
    assert NotificationOutbox.objects.count() == 1


def test_unknown_and_secret_events_cannot_be_queued(customer: User) -> None:
    with pytest.raises(services.UnknownEventError):
        services.enqueue(customer, "nope", {})
    with pytest.raises(services.UnknownEventError):
        services.enqueue(customer, "password_reset", {})


def test_without_smtp_messages_wait(customer: User, settings: Settings) -> None:
    settings.EMAIL_BACKEND = SMTP
    settings.EMAIL_HOST = ""
    row = queue(customer)
    assert services.deliver(row.pk) == OutboxStatus.QUEUED
    assert services.dispatch_due() == 0
    row.refresh_from_db()
    assert row.status == OutboxStatus.QUEUED
    assert row.attempts == 0
    assert "not configured" in row.error


def test_smtp_failures_retry_with_backoff_then_fail(customer: User) -> None:
    row = queue(customer)
    now = timezone.now()
    with mock.patch.object(
        services, "_send_email", side_effect=smtplib.SMTPServerDisconnected("gone")
    ):
        assert services.deliver(row.pk, now=now) == OutboxStatus.QUEUED
        row.refresh_from_db()
        assert (row.attempts, row.next_attempt_at) == (1, now + timedelta(seconds=60))
        assert services.deliver(row.pk, now=now) is None  # not due yet
        moment = now
        for delay in services.RETRY_DELAYS_S[:-1]:
            moment += timedelta(seconds=delay)
            assert services.dispatch_due(now=moment) == 0
        moment += timedelta(seconds=services.RETRY_DELAYS_S[-1])
        services.deliver(row.pk, now=moment)
    row.refresh_from_db()
    assert row.status == OutboxStatus.FAILED
    assert row.attempts == services.MAX_ATTEMPTS
    assert "SMTPServerDisconnected" in row.error


def test_retry_queues_a_failed_message_again(customer: User, owner: User) -> None:
    row = queue(customer)
    NotificationOutbox.objects.filter(pk=row.pk).update(status=OutboxStatus.FAILED, attempts=5)
    retried = services.retry(row, actor=owner, ip=None)
    assert (retried.status, retried.attempts) == (OutboxStatus.QUEUED, 0)
    assert AuditLog.objects.filter(action="notification.retry").exists()
    with pytest.raises(ProblemError):
        services.retry(retried, actor=owner, ip=None)
    assert services.dispatch_due() == 1


def test_recipients_without_email_and_disabled_templates_are_skipped(
    customer: User, make_customer: CustomerFactory
) -> None:
    nobody = make_customer()
    User.objects.filter(pk=nobody.pk).update(email="")
    row = queue(User.objects.get(pk=nobody.pk))
    assert services.deliver(row.pk) == OutboxStatus.SKIPPED
    NotificationTemplate.objects.create(
        key="expired", channel="email", locale="ar", subject="s", body_text="b", enabled=False
    )
    assert services.deliver(queue(customer).pk) == OutboxStatus.SKIPPED


def test_a_broken_template_fails_the_message(customer: User) -> None:
    NotificationTemplate.objects.create(
        key="expired", channel="email", locale="ar", subject="{% if %}", body_text="b"
    )
    row = queue(customer)
    assert services.deliver(row.pk) == OutboxStatus.FAILED
    row.refresh_from_db()
    assert row.error.startswith("Template error")


# --- Password links (customer sign-in, ADR-0013) ------------------------------------------


def link(user: User, purpose: str = "reset") -> SimpleNamespace:
    return SimpleNamespace(
        user=user,
        purpose=purpose,
        url="https://app.example.com/reset-password?uid=MQ&token=s3cret-link-token",
        expires_at=timezone.now() + timedelta(hours=1),
    )


def test_password_links_are_sent_at_once_and_never_stored(customer: User) -> None:
    assert services.send_password_link(link(customer)) is True
    message = mail.outbox[0]
    assert "s3cret-link-token" in message.body
    assert "s3cret-link-token" in str(cast(EmailMultiAlternatives, message).alternatives[0][0])
    row = NotificationOutbox.objects.get()
    assert (row.template_key, row.status) == ("password_reset", OutboxStatus.SENT)
    assert "s3cret-link-token" not in str(row.payload)
    assert "s3cret-link-token" not in row.subject
    NotificationOutbox.objects.filter(pk=row.pk).update(status=OutboxStatus.FAILED)
    with pytest.raises(ProblemError):  # nothing to resend: the link was never kept
        services.retry(row, actor=None, ip=None)


def test_invitations_use_their_own_template(customer: User) -> None:
    assert services.send_password_link(link(customer, "invite")) is True
    assert NotificationOutbox.objects.get().template_key == "password_invite"


def test_password_links_without_smtp_or_with_errors(customer: User, settings: Settings) -> None:
    with mock.patch.object(services, "_send_email", side_effect=OSError("refused")):
        assert services.send_password_link(link(customer)) is False
    failed = NotificationOutbox.objects.get()
    assert (failed.status, failed.error) == (OutboxStatus.FAILED, "OSError")
    settings.EMAIL_BACKEND = SMTP
    settings.EMAIL_HOST = ""
    assert services.send_password_link(link(customer)) is False
    NotificationTemplate.objects.create(
        key="password_reset",
        channel="email",
        locale="ar",
        subject="s",
        body_text="b",
        enabled=False,
    )
    settings.EMAIL_HOST = "mailpit"
    assert services.send_password_link(link(customer)) is False


def test_the_accounts_app_uses_this_sender(customer: User) -> None:
    from apps.accounts import customer_auth  # noqa: PLC0415

    assert customer_auth.send_password_link(link(customer)) is True  # type: ignore[arg-type]
    assert len(mail.outbox) == 1
