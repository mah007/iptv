"""Notifications (SPEC §6 ops, §7.7): the outbox and admin-edited templates (ADR-0012).

Every message is a row in `NotificationOutbox` first, written in the same
transaction as the change it reports; a Celery task on the `notify` queue sends
it after the commit, with retries and backoff. Templates have defaults in code
(`apps.notifications.defaults`); a `NotificationTemplate` row overrides one
(event, channel, locale).
"""

from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.accounts.models import Locale
from apps.core.models import BaseModel


class Channel(models.TextChoices):
    EMAIL = "email", "Email"


class OutboxStatus(models.TextChoices):
    # Waiting to be sent: new, retrying after a failure, or no channel configured yet.
    QUEUED = "queued", "Queued"
    SENT = "sent", "Sent"
    # Gave up after the last attempt.
    FAILED = "failed", "Failed"
    # Nothing to send: the template is switched off or the user has no address.
    SKIPPED = "skipped", "Skipped"


class NotificationTemplate(BaseModel):
    """An admin's version of one event's message in one channel and language.

    `subject`, `body_text` and `body_html` use the sandboxed template language of
    `apps.notifications.rendering`. Without a row, the code default applies.
    `enabled=False` stops the event's messages in this channel and language.
    """

    key = models.CharField(max_length=64)
    channel = models.CharField(max_length=16, choices=Channel.choices, default=Channel.EMAIL)
    locale = models.CharField(max_length=2, choices=Locale.choices)
    subject = models.CharField(max_length=200)
    body_text = models.TextField()
    body_html = models.TextField(blank=True)
    enabled = models.BooleanField(default=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        ordering = ("key", "channel", "locale")
        constraints = (
            models.UniqueConstraint(
                fields=("key", "channel", "locale"), name="notifications_template_unique"
            ),
        )

    def __str__(self) -> str:
        return f"{self.key}:{self.channel}:{self.locale}"


class NotificationOutbox(BaseModel):
    """One message to one user in one channel (SPEC §6 ops).

    `payload` holds the template variables, never secrets (redacted on the way in).
    `dedupe_key` makes an event at most one message, e.g. one T-7 reminder per
    subscription period. `to_address` and `subject` record what was sent.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="notifications",
    )
    channel = models.CharField(max_length=16, choices=Channel.choices, default=Channel.EMAIL)
    template_key = models.CharField(max_length=64)
    locale = models.CharField(max_length=2, choices=Locale.choices)
    payload = models.JSONField(default=dict)
    status = models.CharField(
        max_length=16, choices=OutboxStatus.choices, default=OutboxStatus.QUEUED
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    to_address = models.CharField(max_length=254, blank=True)
    subject = models.CharField(max_length=255, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)
    dedupe_key = models.CharField(max_length=128, blank=True, default="")

    class Meta:
        ordering = ("-created_at",)
        constraints = (
            models.UniqueConstraint(
                fields=("dedupe_key",),
                condition=~Q(dedupe_key=""),
                name="notifications_outbox_dedupe_unique",
            ),
        )
        indexes = (
            models.Index(
                fields=("status", "next_attempt_at"),
                condition=Q(status="queued"),
                name="notif_outbox_queued",
            ),
            models.Index(fields=("user", "-created_at"), name="notif_outbox_user_created"),
            models.Index(fields=("-created_at",), name="notif_outbox_created"),
        )

    def __str__(self) -> str:
        return f"{self.template_key}:{self.channel}:{self.pk}"
