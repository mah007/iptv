"""Fixtures of the operations app's tests."""

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from django.utils import timezone

from apps.core.stores import state_redis
from apps.ops import backups
from apps.ops.alerting import STATE_KEY


@pytest.fixture(autouse=True)
def _clean_ops_state() -> Iterator[None]:
    """The agent's status and the alert states live in redis-state (this lane's index)."""

    def clean() -> None:
        client = state_redis()
        client.delete(backups.AGENT_KEY)
        for key in client.scan_iter(STATE_KEY.format(name="*")):
            client.delete(key)

    clean()
    yield
    clean()


@pytest.fixture
def agent_seen() -> Callable[..., None]:
    """Pretend the backup service just polled, with the given status."""

    def factory(**status: Any) -> None:
        backups.record_agent_status({"archiving": True, "passphrase": True, **status})

    return factory


@pytest.fixture
def delivered(monkeypatch: pytest.MonkeyPatch, settings: Any) -> list[dict[str, Any]]:
    """Alerts Django sends (no monitoring overlay); nothing leaves the process."""
    from apps.notifications import alerts  # noqa: PLC0415

    settings.GRAFANA_URL = ""
    sent: list[dict[str, Any]] = []

    def deliver(payload: dict[str, Any]) -> alerts.Outcome:
        sent.append(payload)
        return alerts.Outcome(channels={"email": "sent"})

    monkeypatch.setattr(alerts, "deliver", deliver)
    return sent


def at(text: str) -> Any:
    """An aware datetime from ISO text (UTC when no offset)."""
    from datetime import UTC, datetime  # noqa: PLC0415

    moment = datetime.fromisoformat(text)
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


__all__ = ["at", "timezone"]
