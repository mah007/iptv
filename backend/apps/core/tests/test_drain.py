"""Draining a web replica before a rollout stops it (ADR-0022)."""

from pathlib import Path
from typing import Any

from django.test import Client


def test_liveness_answers_503_while_draining(settings: Any, tmp_path: Path) -> None:
    settings.WEB_DRAIN_FILE = str(tmp_path / "draining")
    client = Client()
    assert client.get("/internal/health/live", headers={"host": "web"}).status_code == 200
    Path(settings.WEB_DRAIN_FILE).touch()
    response = client.get("/internal/health/live", headers={"host": "web"})
    assert (response.status_code, response.json()) == (503, {"status": "draining"})
