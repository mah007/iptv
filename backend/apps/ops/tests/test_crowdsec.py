"""Traefik's CrowdSec middleware file, rendered from the admin's settings (apps/ops/crowdsec.py)."""

import json
from pathlib import Path
from typing import Any, cast

import pytest
from django.db import transaction

from apps.core.services import set_setting
from apps.ops import crowdsec, tasks

pytestmark = pytest.mark.django_db


def parsed(path: Path) -> dict[str, Any]:
    text = path.read_text()
    assert text.startswith("# Written by Smart IPTV")
    return cast("dict[str, Any]", json.loads(text.split("\n", 1)[1]))


def plugin(config: dict[str, Any]) -> dict[str, Any]:
    return cast("dict[str, Any]", config["http"]["middlewares"]["crowdsec"]["plugin"]["crowdsec"])


def test_the_file_follows_the_settings(tmp_path: Path, settings: Any) -> None:
    settings.CROWDSEC_TRAEFIK_DIR = str(tmp_path)
    assert crowdsec.write() is True
    options = plugin(parsed(tmp_path / "crowdsec.yml"))
    assert options["enabled"] is True
    assert options["crowdsecLapiKeyFile"] == "/run/secrets/crowdsec_bouncer_key"
    assert options["updateMaxFailure"] == -1  # fail open
    assert options["clientTrustedIPs"] == []
    assert crowdsec.write() is False  # unchanged
    set_setting("security.crowdsec_enforce", False, actor=None)
    set_setting("security.crowdsec_allowlist", "203.0.113.0/24, 2001:db8::1", actor=None)
    assert crowdsec.write() is True
    options = plugin(parsed(tmp_path / "crowdsec.yml"))
    # Switched off, the middleware still exists: routers never point at a missing one.
    assert options["enabled"] is False
    assert options["clientTrustedIPs"] == ["203.0.113.0/24", "2001:db8::1"]
    assert oct((tmp_path / "crowdsec.yml").stat().st_mode & 0o777) == "0o644"


def test_without_the_overlay_nothing_is_written(settings: Any, tmp_path: Path) -> None:
    settings.CROWDSEC_TRAEFIK_DIR = ""
    assert crowdsec.write() is False
    assert crowdsec.write(tmp_path / "missing") is False


def test_the_allowlist_is_validated() -> None:
    from django.core.exceptions import ValidationError  # noqa: PLC0415

    with pytest.raises(ValidationError):
        set_setting("security.crowdsec_allowlist", "not-an-ip", actor=None)


def test_a_setting_change_renders_at_once(
    monkeypatch: pytest.MonkeyPatch, django_capture_on_commit_callbacks: Any
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        tasks.render_crowdsec, "apply_async", lambda **kwargs: calls.append(f"render {kwargs}")
    )
    with django_capture_on_commit_callbacks(execute=True):
        set_setting("security.crowdsec_enforce", False, actor=None)
    with django_capture_on_commit_callbacks(execute=True):
        set_setting("backups.files_keep_daily", 9, actor=None)
    assert calls == ["render {'countdown': 2}"]


def test_the_task(settings: Any, tmp_path: Path) -> None:
    settings.CROWDSEC_TRAEFIK_DIR = str(tmp_path)
    with transaction.atomic():
        tasks.render_crowdsec()
    assert (tmp_path / "crowdsec.yml").exists()
