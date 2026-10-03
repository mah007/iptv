"""Sessions stop when access ends (accounts signals), the sweeper task, and settings."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from django.utils import timezone
from pytest_django import Settings

from apps.accounts import services as accounts
from apps.accounts.models import CustomerAccess, Device, User
from apps.conftest import CustomerFactory
from apps.core.ids import uuid7
from apps.core.stores import state_redis
from apps.playback import concurrency, conf, services, tasks
from apps.playback.models import EndReason, PlaybackSession, TitleKind
from apps.playback.services import PlayableRendition, PlayableTitle, RenditionKind

pytestmark = pytest.mark.django_db

type Capture = Callable[..., Any]


def play(user: User, device: Device | None = None) -> PlaybackSession:
    device = device or user.devices.order_by("created_at").first()
    assert device is not None
    title = PlayableTitle(
        kind=TitleKind.EPISODE,
        id=uuid7(),
        renditions=(PlayableRendition(uuid7().hex, RenditionKind.COMPAT, 720),),
        runtime_s=2700,
    )
    return services.start_playback(user, device, title, client_ip="203.0.113.7").session


def kick_reason(session: PlaybackSession) -> bytes | None:
    value = state_redis().get(concurrency.kick_key(session.session_key))
    assert value is None or isinstance(value, bytes)
    return value


def test_expired_access_stops_sessions(
    make_customer: CustomerFactory, django_capture_on_commit_callbacks: Capture
) -> None:
    user = make_customer(devices=1)
    session = play(user)
    CustomerAccess.objects.filter(user=user).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    with django_capture_on_commit_callbacks(execute=True):
        assert accounts.process_expired_access() == 1
    session.refresh_from_db()
    assert session.end_reason == EndReason.EXPIRED
    assert kick_reason(session) == b"access_expired"


def test_suspension_stops_sessions(
    make_customer: CustomerFactory, django_capture_on_commit_callbacks: Capture
) -> None:
    user = make_customer(devices=1)
    session = play(user)
    with django_capture_on_commit_callbacks(execute=True):
        accounts.suspend_customer(user, actor=None, reason="sharing")
    session.refresh_from_db()
    assert session.end_reason == EndReason.KICKED
    assert kick_reason(session) == b"access_suspended"


def test_blocked_device_stops_only_its_sessions(
    make_customer: CustomerFactory, django_capture_on_commit_callbacks: Capture
) -> None:
    user = make_customer(devices=2, max_streams=2)
    phone, tv = user.devices.order_by("created_at")
    on_phone, on_tv = play(user, phone), play(user, tv)
    with django_capture_on_commit_callbacks(execute=True):
        accounts.block_device(tv, reason="lost", actor=None)
    on_phone.refresh_from_db()
    on_tv.refresh_from_db()
    assert (on_phone.ended_at, on_tv.end_reason) == (None, EndReason.KICKED)
    assert kick_reason(on_tv) == b"device_disabled"


def test_sweeper_task_closes_idle_sessions(make_customer: CustomerFactory) -> None:
    user = make_customer(devices=1)
    session = play(user)
    state_redis().delete(concurrency.sess_key(session.session_key))
    PlaybackSession.objects.filter(pk=session.pk).update(
        last_heartbeat_at=timezone.now() - timedelta(minutes=10)
    )
    assert tasks.sweep_sessions() == 1
    session.refresh_from_db()
    assert session.end_reason == EndReason.IDLE


def test_session_row_helpers(make_customer: CustomerFactory) -> None:
    session = play(make_customer(devices=1))
    assert session.title_ref == f"episode:{session.title_id}"
    assert session.is_active
    assert str(session).startswith(f"episode:{session.title_id} (")


def test_media_origin_follows_the_public_hosts(settings: Settings) -> None:
    settings.MEDIA_BASE_URL = None
    settings.DOMAIN, settings.PUBLIC_SCHEME, settings.PUBLIC_PORT = "example.com", "https", 443
    assert conf.media_base_url() == "https://media.example.com"
    settings.DOMAIN, settings.PUBLIC_SCHEME, settings.PUBLIC_PORT = "localhost", "http", 8080
    assert conf.media_base_url() == "http://media.localhost:8080"
    settings.MEDIA_BASE_URL = "https://cdn.example.net/"
    assert conf.media_base_url() == "https://cdn.example.net"


def test_settings_fall_back_to_the_environment(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    del settings.PLAYBACK_SLOT_WINDOW_S
    assert conf.slot_window_s() == 90
    monkeypatch.setenv("PLAYBACK_SLOT_WINDOW_S", "45")
    assert conf.slot_window_s() == 45
    settings.PLAYBACK_SLOT_WINDOW_S = 30
    assert conf.slot_window_s() == 30
    assert conf.feed_interval_s() == 2.0


def test_timestamps_are_utc() -> None:
    assert services._aware(0) == datetime(1970, 1, 1, tzinfo=UTC)
