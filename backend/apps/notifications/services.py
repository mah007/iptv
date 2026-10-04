"""Notification services (SPEC §7.7, ADR-0012): enqueue, deliver, templates.

`enqueue` writes outbox rows in the caller's transaction and sends them after
the commit (Celery `notify` queue). `deliver` sends one row: rendered from the
admin's template or the code default, in the recipient's language. Failures are
retried with backoff (`RETRY_DELAYS_S`) by the dispatcher beat job, then marked
failed. Without SMTP settings (EMAIL_HOST), messages stay queued, visible in the
admin, and go out once email is configured.
"""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
from types import SimpleNamespace
from typing import Any, Final
from uuid import UUID

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import User
from apps.audit import services as audit
from apps.billing.renewal import zone
from apps.core.errors import ErrorCode, ProblemError, field_error
from apps.core.metrics import NOTIFICATIONS
from apps.core.redaction import redact_text, redact_value
from apps.core.services import get_setting
from apps.notifications import rendering
from apps.notifications.defaults import EVENTS, SECRET_EVENTS, default_for
from apps.notifications.models import (
    Channel,
    NotificationOutbox,
    NotificationTemplate,
    OutboxStatus,
)
from config.origins import origin

logger = logging.getLogger(__name__)

#: Wait before attempt n+1 after n failed attempts; the last entry's attempt is final.
RETRY_DELAYS_S: Final = (60, 300, 900, 3600)
MAX_ATTEMPTS: Final = len(RETRY_DELAYS_S) + 1
DISPATCH_BATCH: Final = 200
_ERROR_LENGTH: Final = 500


class UnknownEventError(ValueError):
    """A notification for an event no template knows (a programming error)."""


# --- Context ------------------------------------------------------------------------------


def portal_url() -> str:
    return origin(settings.PUBLIC_SCHEME, settings.APP_HOST, settings.PUBLIC_PORT)


def local_time(moment: datetime | None, user: User | None) -> str:
    """`2026-11-03 21:00` on the user's clock (Asia/Riyadh by default)."""
    if moment is None:
        return ""
    tz = zone(user.timezone if user is not None else "Asia/Riyadh")
    return moment.astimezone(tz).strftime("%Y-%m-%d %H:%M")


def common_context(user: User | None, locale: str) -> dict[str, Any]:
    """Variables every template may use."""
    return {
        "service_name": str(get_setting(f"branding.service_name_{locale}")),
        "support_email": str(get_setting("branding.support_email")),
        "accent_color": str(get_setting("branding.accent_color")),
        "portal_url": portal_url(),
        "name": user.get_full_name() if user is not None else "",
    }


# --- Templates ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EffectiveTemplate:
    key: str
    channel: str
    locale: str
    subject: str
    body_text: str
    body_html: str
    enabled: bool
    row: NotificationTemplate | None

    @property
    def is_default(self) -> bool:
        return self.row is None


def _effective(
    key: str, channel: str, locale: str, row: NotificationTemplate | None
) -> EffectiveTemplate | None:
    if row is not None:
        return EffectiveTemplate(
            key, channel, locale, row.subject, row.body_text, row.body_html, row.enabled, row
        )
    default = default_for(key, locale)
    if default is None:
        return None
    return EffectiveTemplate(
        key, channel, locale, default.subject, default.body_text, default.body_html, True, None
    )


def effective_template(key: str, channel: str, locale: str) -> EffectiveTemplate | None:
    row = NotificationTemplate.objects.filter(key=key, channel=channel, locale=locale).first()
    return _effective(key, channel, locale, row)


def all_templates() -> list[EffectiveTemplate]:
    """Every (event, channel, locale): the admin's row or the default; one query."""
    rows = {(row.key, row.channel, row.locale): row for row in NotificationTemplate.objects.all()}
    result = []
    for key in EVENTS:
        for channel in Channel.values:
            for locale in ("en", "ar"):
                template = _effective(key, channel, locale, rows.get((key, channel, locale)))
                if template is not None:
                    result.append(template)
    return result


def _check_template(values: Mapping[str, Any]) -> None:
    errors: dict[str, list[Any]] = {}
    for field, html in (("subject", False), ("body_text", False), ("body_html", True)):
        source = values.get(field)
        if source is None:
            continue
        try:
            rendering.check(str(source), html=html)
        except rendering.TemplateError as exc:
            errors[field] = [field_error(str(exc), code="template_invalid")]
    if errors:
        raise ProblemError(ErrorCode.VALIDATION_ERROR, field_errors=errors)


def template_snapshot(row: NotificationTemplate) -> dict[str, Any]:
    return {
        "key": row.key,
        "channel": row.channel,
        "locale": row.locale,
        "subject": row.subject,
        "body_text": row.body_text,
        "body_html": row.body_html,
        "enabled": row.enabled,
    }


def save_template(  # noqa: PLR0913 (keyword-only fields of one template)
    *,
    key: str,
    channel: str,
    locale: str,
    values: Mapping[str, Any],
    actor: User | None,
    ip: str | None = None,
) -> NotificationTemplate:
    """Create or change the admin's version of a template (validated in the sandbox)."""
    if key not in EVENTS:
        raise ProblemError(
            ErrorCode.VALIDATION_ERROR,
            field_errors={"key": [field_error("Unknown event.", code="unknown_event")]},
        )
    _check_template(values)
    with transaction.atomic():
        row = (
            NotificationTemplate.objects.select_for_update()
            .filter(key=key, channel=channel, locale=locale)
            .first()
        )
        before = template_snapshot(row) if row is not None else None
        if row is None:
            default = effective_template(key, channel, locale)
            row = NotificationTemplate(
                key=key,
                channel=channel,
                locale=locale,
                subject=default.subject if default else "",
                body_text=default.body_text if default else "",
                body_html=default.body_html if default else "",
            )
        for field in ("subject", "body_text", "body_html", "enabled"):
            if field in values:
                setattr(row, field, values[field])
        if not row.subject.strip() or not row.body_text.strip():
            raise ProblemError(
                ErrorCode.VALIDATION_ERROR,
                field_errors={
                    name: [field_error("This field may not be blank.", code="blank")]
                    for name in ("subject", "body_text")
                    if not getattr(row, name).strip()
                },
            )
        row.updated_by = actor
        row.save()
        audit.record(
            "notification_template.update" if before else "notification_template.create",
            actor=actor,
            target=row,
            before=before,
            after=template_snapshot(row),
            ip=ip,
        )
    return row


def delete_template(row: NotificationTemplate, *, actor: User | None, ip: str | None) -> None:
    """Drop the admin's version: the code default applies again."""
    with transaction.atomic():
        audit.record(
            "notification_template.delete",
            actor=actor,
            target=row,
            before=template_snapshot(row),
            ip=ip,
        )
        row.delete()


def preview(  # noqa: PLR0913 (keyword-only fields of one template)
    *,
    key: str,
    locale: str,
    subject: str,
    body_text: str,
    body_html: str,
    user: User | None,
) -> rendering.RenderedMessage:
    """Render unsaved template text with the event's sample values."""
    event = EVENTS.get(key)
    if event is None:
        raise ProblemError(
            ErrorCode.VALIDATION_ERROR,
            field_errors={"key": [field_error("Unknown event.", code="unknown_event")]},
        )
    _check_template({"subject": subject, "body_text": body_text, "body_html": body_html})
    context = {**common_context(user, locale), **event.sample}
    return rendering.render_message(subject, body_text, body_html, context, locale=locale)


# --- The outbox -------------------------------------------------------------------------


def _send_after_commit(outbox_id: UUID) -> None:
    from apps.notifications.tasks import send_notification  # noqa: PLC0415 (import cycle)

    send_notification.delay(str(outbox_id))


def enqueue(  # noqa: PLR0913 (keyword-only options)
    user: User,
    template_key: str,
    payload: Mapping[str, Any],
    *,
    channels: Sequence[str] = (Channel.EMAIL,),
    dedupe_key: str | None = None,
    locale: str | None = None,
) -> list[NotificationOutbox]:
    """Queue `template_key` for `user`; sent after the current transaction commits.

    Never put secrets in `payload` (it is redacted anyway). With `dedupe_key`, a
    message already queued under that key is not queued again.
    """
    if template_key not in EVENTS or template_key in SECRET_EVENTS:
        msg = f"{template_key!r} cannot be queued (unknown, or sent at once)"
        raise UnknownEventError(msg)
    language = locale or (user.locale if user.locale in ("ar", "en") else "ar")
    clean = redact_value(dict(payload))
    rows = []
    for channel in channels:
        key = f"{dedupe_key}:{channel}" if dedupe_key else ""
        if key and NotificationOutbox.objects.filter(dedupe_key=key).exists():
            continue
        try:
            with transaction.atomic():
                row = NotificationOutbox.objects.create(
                    user=user,
                    channel=channel,
                    template_key=template_key,
                    locale=language,
                    payload=clean,
                    dedupe_key=key,
                    next_attempt_at=timezone.now(),
                )
        except IntegrityError:  # queued concurrently under the same dedupe key
            continue
        transaction.on_commit(partial(_send_after_commit, row.pk), robust=True)
        rows.append(row)
    return rows


def email_configured() -> bool:
    """SMTP needs a host; Django's other backends (tests, console) always send."""
    if settings.EMAIL_BACKEND == "django.core.mail.backends.smtp.EmailBackend":
        return bool(settings.EMAIL_HOST)
    return True


def _finish(row: NotificationOutbox, status: OutboxStatus, error: str = "") -> str:
    row.status = status
    row.error = redact_text(error)[:_ERROR_LENGTH]
    if status != OutboxStatus.QUEUED:
        row.next_attempt_at = None
    row.save(
        update_fields=[
            "status",
            "error",
            "attempts",
            "next_attempt_at",
            "to_address",
            "subject",
            "sent_at",
            "updated_at",
        ]
    )
    NOTIFICATIONS.labels(row.channel, status).inc()
    return status


def _send_email(row: NotificationOutbox, message: rendering.RenderedMessage) -> None:
    email = EmailMultiAlternatives(
        subject=message.subject,
        body=message.text,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[row.to_address],
    )
    email.attach_alternative(message.html, "text/html")
    email.send(fail_silently=False)


def deliver(outbox_id: UUID | str, *, now: datetime | None = None) -> str | None:
    """Send one queued message when it is due; returns its new status (None: not due).

    The row stays locked while it is sent, so the task and the dispatcher never
    both send it.
    """
    moment = now or timezone.now()
    if not email_configured():
        _note_unconfigured(outbox_id)
        return OutboxStatus.QUEUED
    with transaction.atomic():
        row = (
            NotificationOutbox.objects.select_for_update(skip_locked=True, of=("self",))
            .select_related("user")
            .filter(pk=outbox_id, status=OutboxStatus.QUEUED)
            .filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=moment))
            .first()
        )
        if row is None:
            return None
        return _deliver_locked(row, moment)


def _note_unconfigured(outbox_id: UUID | str) -> None:
    NotificationOutbox.objects.filter(pk=outbox_id, status=OutboxStatus.QUEUED).update(
        error="Email is not configured yet (EMAIL_HOST); the message waits in the queue."
    )


def _deliver_locked(  # noqa: PLR0911 (one return per outcome)
    row: NotificationOutbox, moment: datetime
) -> str:
    user = row.user
    row.to_address = (user.email if user is not None else "") or row.to_address
    if not row.to_address:
        return _finish(row, OutboxStatus.SKIPPED, "The recipient has no email address.")
    template = effective_template(row.template_key, row.channel, row.locale)
    if template is None:
        return _finish(row, OutboxStatus.FAILED, "No template for this event.")
    if not template.enabled:
        return _finish(row, OutboxStatus.SKIPPED, "The template is switched off.")
    context = {**common_context(user, row.locale), **row.payload}
    try:
        message = rendering.render_message(
            template.subject, template.body_text, template.body_html, context, locale=row.locale
        )
    except rendering.TemplateError as exc:
        return _finish(row, OutboxStatus.FAILED, f"Template error: {exc}")
    row.subject = message.subject
    row.attempts += 1
    try:
        _send_email(row, message)
    except Exception as exc:  # SMTP and network errors: retry later
        logger.warning("notification %s not sent (attempt %d)", row.pk, row.attempts)
        if row.attempts >= MAX_ATTEMPTS:
            return _finish(row, OutboxStatus.FAILED, f"{type(exc).__name__}: {exc}")
        row.next_attempt_at = moment + timedelta(seconds=RETRY_DELAYS_S[row.attempts - 1])
        return _finish(row, OutboxStatus.QUEUED, f"{type(exc).__name__}: {exc}")
    row.sent_at = moment
    return _finish(row, OutboxStatus.SENT)


def dispatch_due(*, now: datetime | None = None, limit: int = DISPATCH_BATCH) -> int:
    """Send queued messages that are due (retries, and everything once SMTP is set up)."""
    if not email_configured():
        return 0
    moment = now or timezone.now()
    due = list(
        NotificationOutbox.objects.filter(status=OutboxStatus.QUEUED)
        .filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=moment))
        .order_by("created_at")
        .values_list("pk", flat=True)[:limit]
    )
    sent = 0
    for outbox_id in due:
        if deliver(outbox_id, now=moment) == OutboxStatus.SENT:
            sent += 1
    return sent


def retry(row: NotificationOutbox, *, actor: User | None, ip: str | None) -> NotificationOutbox:
    """Queue a failed or skipped message again, with a fresh set of attempts."""
    with transaction.atomic():
        row = NotificationOutbox.objects.select_for_update().get(pk=row.pk)
        if row.status not in (OutboxStatus.FAILED, OutboxStatus.SKIPPED):
            raise ProblemError(
                ErrorCode.CONFLICT, "Only failed or skipped messages can be retried."
            )
        if row.template_key in SECRET_EVENTS:
            raise ProblemError(
                ErrorCode.CONFLICT,
                "Password links are not stored; the customer asks for a new one.",
            )
        before = {"status": row.status, "attempts": row.attempts}
        row.status = OutboxStatus.QUEUED
        row.attempts = 0
        row.error = ""
        row.next_attempt_at = timezone.now()
        row.save(update_fields=["status", "attempts", "error", "next_attempt_at", "updated_at"])
        audit.record(
            "notification.retry",
            actor=actor,
            target=row,
            before=before,
            after={"status": row.status},
            ip=ip,
        )
        transaction.on_commit(partial(_send_after_commit, row.pk), robust=True)
    return row


def send_test(
    key: str, locale: str, *, actor: User, ip: str | None = None
) -> list[NotificationOutbox]:
    """The event's current template, with sample values, to the acting admin."""
    event = EVENTS.get(key)
    if event is None:
        raise ProblemError(
            ErrorCode.VALIDATION_ERROR,
            field_errors={"key": [field_error("Unknown event.", code="unknown_event")]},
        )
    if not actor.email:
        raise ProblemError(
            ErrorCode.CONFLICT, "Add an email address to your admin account to receive tests."
        )
    if key in SECRET_EVENTS:  # sent at once, with the sample link
        sample = SimpleNamespace(
            user=actor,
            purpose="invite" if key == "password_invite" else "reset",
            url=str(event.sample["link_url"]),
            expires_at=timezone.now() + timedelta(hours=1),
        )
        send_password_link(sample, locale=locale)
        row = NotificationOutbox.objects.filter(user=actor, template_key=key).first()
        audit.record(
            "notification.test",
            actor=actor,
            target=row,
            after={"key": key, "locale": locale},
            ip=ip,
        )
        return [row] if row is not None else []
    with transaction.atomic():
        rows = enqueue(actor, key, event.sample, locale=locale)
        audit.record(
            "notification.test",
            actor=actor,
            target=rows[0] if rows else None,
            after={"key": key, "locale": locale},
            ip=ip,
        )
    return rows


# --- Password links (customer sign-in, ADR-0013) ----------------------------------------


def send_password_link(link: Any, *, locale: str | None = None) -> bool:
    """Email a password reset or invitation link at once (`customer_auth`'s sender).

    The link is a secret: it is rendered and sent here, never written to the
    outbox, a log or the broker. The outbox gets a row without it, so the delivery
    shows in the admin's log. False when the email could not be sent.
    """
    user: User = link.user
    key = "password_invite" if link.purpose == "invite" else "password_reset"
    locale = locale or (user.locale if user.locale in ("ar", "en") else "ar")
    expires = local_time(link.expires_at, user)
    row = NotificationOutbox(
        user=user,
        channel=Channel.EMAIL,
        template_key=key,
        locale=locale,
        payload={"expires_at": expires},
        to_address=user.email,
        attempts=1,
    )
    if not email_configured():
        row.status = OutboxStatus.FAILED
        row.error = "Email is not configured (EMAIL_HOST)."
        row.attempts = 0
        row.save()
        return False
    template = effective_template(key, Channel.EMAIL, locale)
    if template is None or not template.enabled:
        row.status = OutboxStatus.SKIPPED
        row.error = "The template is switched off."
        row.save()
        return False
    context = {**common_context(user, locale), "link_url": link.url, "expires_at": expires}
    try:
        message = rendering.render_message(
            template.subject, template.body_text, template.body_html, context, locale=locale
        )
        row.subject = message.subject
        _send_email(row, message)
    except Exception as exc:
        logger.warning("password link email to user %s not sent", user.pk)
        row.status = OutboxStatus.FAILED
        # Never the message text: a template error could quote the link.
        row.error = type(exc).__name__
        row.save()
        NOTIFICATIONS.labels(row.channel, row.status).inc()
        return False
    row.status = OutboxStatus.SENT
    row.sent_at = timezone.now()
    row.save()
    NOTIFICATIONS.labels(row.channel, row.status).inc()
    return True
