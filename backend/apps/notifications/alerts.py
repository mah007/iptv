"""Monitoring alerts, delivered by Django (SPEC §14, ADR-0018).

Alertmanager posts every alert group to /internal/alerts (a webhook with a shared
secret). This module sends it to the people the admin chose in Settings, group
"monitoring": by email through the platform's SMTP settings, and by Telegram through
the bot and admins' chat of Settings → Integrations, each filtered by severity (all,
critical only, none).

Delivery is synchronous in the web process, not a Celery task, so the alerts that say
the workers are down still go out. A channel that fails is logged and counted
(`iptv_alert_deliveries_total{channel,result}`); when every configured channel fails,
the webhook answers 503 and Alertmanager tries again. When web itself is down,
Alertmanager's direct fallback channels (from .env) carry the alerts about that.
"""

import html
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

from django.core.mail import EmailMessage

from apps.core.metrics import ALERT_DELIVERIES
from apps.core.redaction import redact_text
from apps.core.services import get_setting
from apps.notifications import mail, telegram
from apps.notifications.services import email_configured

logger = logging.getLogger(__name__)

TELEGRAM_LIMIT: Final = telegram.TEXT_LIMIT
MAX_ALERTS: Final = 50


@dataclass(frozen=True, slots=True)
class Alert:
    status: str
    name: str
    severity: str
    summary: str
    description: str
    runbook: str
    labels: Mapping[str, str]
    starts_at: str
    ends_at: str
    source: str

    @property
    def detail_labels(self) -> str:
        hidden = {"alertname", "severity"}
        return " ".join(f"{k}={v}" for k, v in sorted(self.labels.items()) if k not in hidden)


@dataclass(frozen=True, slots=True)
class Notice:
    """One Alertmanager webhook call: an alert group, firing or resolved."""

    status: str
    name: str
    severity: str
    alerts: tuple[Alert, ...]
    external_url: str


@dataclass(slots=True)
class Outcome:
    """What happened per channel: sent, failed, skipped (not configured or filtered)."""

    channels: dict[str, str] = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        attempted = [result for result in self.channels.values() if result != "skipped"]
        return bool(attempted) and all(result == "failed" for result in attempted)


def _text(value: object, limit: int = 2000) -> str:
    return str(value)[:limit] if value is not None else ""


def parse(payload: Mapping[str, Any]) -> Notice:
    """Alertmanager's webhook payload (version 4) into what the messages need."""
    common = payload.get("commonLabels") or {}
    alerts = []
    for raw in list(payload.get("alerts") or [])[:MAX_ALERTS]:
        if not isinstance(raw, Mapping):
            continue
        labels = {str(k): _text(v, 200) for k, v in (raw.get("labels") or {}).items()}
        annotations = raw.get("annotations") or {}
        alerts.append(
            Alert(
                status=_text(raw.get("status"), 20),
                name=labels.get("alertname", ""),
                severity=labels.get("severity", ""),
                summary=_text(annotations.get("summary")),
                description=_text(annotations.get("description")),
                runbook=_text(annotations.get("runbook"), 300),
                labels=labels,
                starts_at=_text(raw.get("startsAt"), 40),
                ends_at=_text(raw.get("endsAt"), 40),
                source=_text(raw.get("generatorURL"), 1000),
            )
        )
    return Notice(
        status=_text(payload.get("status"), 20) or "firing",
        name=_text(common.get("alertname"), 200) or (alerts[0].name if alerts else "alert"),
        severity=_text(common.get("severity"), 40),
        alerts=tuple(alerts),
        external_url=_text(payload.get("externalURL"), 300),
    )


def wanted(severity: str, policy: str) -> bool:
    """`all` sends everything but the silent Watchdog (severity none); `critical`, critical only."""
    if policy == "all":
        return severity != "none"
    if policy == "critical":
        return severity == "critical"
    return False


def _when(value: str) -> str:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return value


def runbook_link(path: str) -> str:
    """The rules name the runbook by its path in the repository; messages link it."""
    if not path or "://" in path:
        return path
    base = str(get_setting("monitoring.runbook_base_url"))
    if not base:
        return path
    return f"{base.rstrip('/')}/{path.lstrip('/')}"


def subject(notice: Notice, alerts: Sequence[Alert]) -> str:
    firing = sum(1 for alert in alerts if alert.status == "firing")
    count = f":{firing}" if notice.status == "firing" else ""
    severity = f" ({notice.severity})" if notice.severity else ""
    return f"[{notice.status.upper()}{count}] {notice.name}{severity} - Smart IPTV"


def email_body(notice: Notice, alerts: Sequence[Alert]) -> str:
    lines: list[str] = []
    for status in ("firing", "resolved"):
        group = [alert for alert in alerts if alert.status == status]
        if not group:
            continue
        lines.append(f"{status.upper()} ({len(group)})")
        for alert in group:
            lines.append(f"- {alert.summary or alert.name}")
            if status == "firing":
                lines.append(f"  {alert.description}")
            lines.append(f"  Labels: {alert.detail_labels}")
            moment = alert.starts_at if status == "firing" else alert.ends_at
            lines.append(f"  {'Since' if status == 'firing' else 'Resolved'}: {_when(moment)}")
            if alert.runbook:
                lines.append(f"  Runbook: {runbook_link(alert.runbook)}")
            if alert.source and status == "firing":
                lines.append(f"  Source: {alert.source}")
        lines.append("")
    if notice.external_url:
        lines.append(f"Silence or inspect: {notice.external_url}")
    return "\n".join(lines)


def telegram_text(notice: Notice, alerts: Sequence[Alert]) -> str:
    escape = html.escape
    severity = f" ({escape(notice.severity)})" if notice.severity else ""
    lines = [f"<b>{escape(notice.status.upper())}: {escape(notice.name)}</b>{severity}"]
    for alert in alerts:
        lines.append(f"- {escape(alert.summary or alert.name)}")
        if alert.status == "firing":
            lines.append(f"  <i>{escape(alert.description)}</i>")
            if alert.runbook:
                link = escape(runbook_link(alert.runbook))
                lines.append(f'  <a href="{link}">Runbook</a>')
    text = "\n".join(lines)
    return text if len(text) <= TELEGRAM_LIMIT else text[: TELEGRAM_LIMIT - 1] + "…"


def recipients() -> list[str]:
    raw = str(get_setting("monitoring.alert_emails"))
    return [address.strip() for address in raw.split(",") if address.strip()]


def _send_email(notice: Notice, alerts: Sequence[Alert], to: list[str]) -> None:
    EmailMessage(
        subject=subject(notice, alerts),
        body=email_body(notice, alerts),
        from_email=mail.from_address(),
        to=to,
    ).send(fail_silently=False)


def _record(outcome: Outcome, channel: str, result: str, error: Exception | None = None) -> None:
    outcome.channels[channel] = result
    ALERT_DELIVERIES.labels(channel, result).inc()
    if error is not None:
        logger.warning(
            "alert delivery failed",
            extra={"channel": channel, "error": redact_text(f"{type(error).__name__}: {error}")},
        )


def deliver(payload: Mapping[str, Any]) -> Outcome:
    """Send one alert group to the configured channels; never raises."""
    outcome = Outcome()
    if not get_setting("monitoring.alerts_enabled"):
        _record(outcome, "email", "skipped")
        _record(outcome, "telegram", "skipped")
        return outcome
    notice = parse(payload)

    emailed = [
        a
        for a in notice.alerts
        if wanted(a.severity, str(get_setting("monitoring.alert_email_severities")))
    ]
    to = recipients()
    if emailed and to and email_configured():
        try:
            _send_email(notice, emailed, to)
        except Exception as error:
            _record(outcome, "email", "failed", error)
        else:
            _record(outcome, "email", "sent")
    else:
        _record(outcome, "email", "skipped")

    token, chat_id = telegram.bot_token(), telegram.admin_chat_id()
    texted = [
        a
        for a in notice.alerts
        if wanted(a.severity, str(get_setting("monitoring.telegram_severities")))
    ]
    if texted and token and chat_id:
        try:
            telegram.send_message(telegram_text(notice, texted), chat_id=chat_id, token=token)
        except Exception as error:
            _record(outcome, "telegram", "failed", error)
        else:
            _record(outcome, "telegram", "sent")
    else:
        _record(outcome, "telegram", "skipped")
    return outcome


# --- Alertmanager's own fallback channels (CFG, ADR-0019) -----------------------------------

#: What Alertmanager's fallback receiver needs, in the order the entrypoint reads it.
FALLBACK_FIELDS: Final = (
    "email_to",
    "smarthost",
    "from",
    "username",
    "require_tls",
    "smtp_password",
    "telegram_bot_token",
    "telegram_chat_id",
)


def fallback_config() -> dict[str, str]:
    """The settings Alertmanager's fallback receiver uses for the alerts that mean
    Django can't deliver (web, PostgreSQL or Valkey down). Alertmanager fetches them
    from /internal/alert-fallback, keeps the last copy, and re-renders its
    configuration when they change, so they are edited in the admin like everything
    else; a channel the admin switched off (or set to "none") is empty here."""
    from apps.notifications import mail  # noqa: PLC0415

    values = dict.fromkeys(FALLBACK_FIELDS, "")
    values["require_tls"] = "true"
    if not get_setting("monitoring.alerts_enabled"):
        return values
    smtp = mail.smtp_config()
    if (
        smtp.host
        and recipients()
        and str(get_setting("monitoring.alert_email_severities")) != "none"
    ):
        values.update(
            email_to=", ".join(recipients()),
            smarthost=f"{smtp.host}:{smtp.port}",
            # Alertmanager wants a plain sender; "Name <address>" is fine too.
            **{"from": mail.from_address()},
            username=smtp.username,
            # Port 465 is implicit TLS for Alertmanager; require_tls is about STARTTLS.
            require_tls="true" if smtp.security == "starttls" else "false",
            smtp_password=smtp.password if smtp.username else "",
        )
    if telegram.configured() and str(get_setting("monitoring.telegram_severities")) != "none":
        values.update(
            telegram_bot_token=telegram.bot_token(), telegram_chat_id=telegram.admin_chat_id()
        )
    return values


def fallback_status() -> dict[str, bool]:
    """Whether Alertmanager's fallback has an email and a Telegram channel."""
    values = fallback_config()
    return {
        "email": bool(values["email_to"] and values["smarthost"]),
        "telegram": bool(values["telegram_bot_token"] and values["telegram_chat_id"]),
    }
