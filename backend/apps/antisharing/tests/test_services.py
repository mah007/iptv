"""Sightings, flags, the admin's ladder and the GeoIP admin endpoints (G-18, G-26, G-27;
ADR-0021). GeoIP answers come from small generated databases, never a download."""

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Device, DeviceKind, User, UserStatus
from apps.antisharing import services, streams
from apps.antisharing.models import FlagStatus, RiskFlag, Sighting, SightingSource
from apps.audit.models import AuditLog
from apps.conftest import AdminFactory, CustomerFactory
from apps.core import geoip
from apps.core.services import set_setting
from apps.core.stores import state_redis
from apps.core.tests import geoip_data
from apps.notifications import alerts
from apps.notifications.models import NotificationOutbox
from apps.ops import retention
from apps.playback.models import PlaybackSession

pytestmark = pytest.mark.django_db
ADMIN = {"host": settings.ADMIN_HOST}

#: Public addresses the generated databases know (country, ASN).
RIYADH, MOBILE_SA, CAIRO, DUBAI, LONDON = (
    "77.31.4.5",
    "77.32.9.1",
    "41.32.1.9",
    "5.30.2.7",
    "81.2.69.142",
)


@pytest.fixture(autouse=True)
def _geoip(geoip_dir: Path) -> None:
    geoip_data.install(geoip_dir)
    state_redis().flushdb()


@pytest.fixture
def customer(make_customer: CustomerFactory) -> User:
    return make_customer(name="Omar", devices=1, max_devices=3, email="omar@example.com")


@pytest.fixture
def tv(customer: User) -> Device:
    return Device.objects.get(user=customer, kind=DeviceKind.XTREAM)


@pytest.fixture
def notices(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    def deliver(payload: dict[str, Any]) -> alerts.Outcome:
        sent.append(payload)
        return alerts.Outcome()

    monkeypatch.setattr(alerts, "deliver", deliver)
    return sent


def test_a_sighting_per_device_network_source_and_hour(customer: User, tv: Device) -> None:
    first = services.record(customer, tv, RIYADH, SightingSource.XTREAM)
    assert first is not None
    assert (first.network, first.country, first.asn) == ("77.31.4.0/24", "SA", 25019)
    # Within the interval the same network isn't written again.
    assert services.record(customer, tv, "77.31.4.99", SightingSource.XTREAM) is None
    again = services.record(customer, tv, RIYADH, SightingSource.XTREAM, throttle=False)
    assert again is not None
    assert again.hits == 2
    assert Sighting.objects.count() == 1
    tv.refresh_from_db()
    assert tv.last_country == "SA"
    local = services.record(customer, tv, "192.168.1.20", SightingSource.XTREAM)
    assert local is not None
    assert local.network == "local"
    assert services.network_of("2a00:1450:4001:81a::200e") == "2a00:1450:4001:81a::/64"


def test_switched_off_records_nothing(customer: User, tv: Device) -> None:
    set_setting("antisharing.enabled", value=False, actor=None)
    assert services.record(customer, tv, RIYADH, SightingSource.XTREAM) is None


def test_stream_networks_become_sightings(customer: User, tv: Device) -> None:
    import asyncio  # noqa: PLC0415

    now = timezone.now()
    PlaybackSession.objects.create(
        session_key="a" * 32,
        user=customer,
        device=tv,
        title_kind="movie",
        title_id=customer.pk,
        rendition="compat",
        started_at=now,
        last_heartbeat_at=now,
    )
    asyncio.run(streams.note_network("a" * 32, CAIRO))
    asyncio.run(streams.note_network("a" * 32, "10.0.0.5"))  # local: not noted
    assert services.collect_stream_networks(now) == 1
    sighting = Sighting.objects.get(source=SightingSource.STREAM)
    assert (sighting.network, sighting.country) == ("41.32.1.0/24", "EG")


def _spread(customer: User, tv: Device) -> None:
    for address in (RIYADH, CAIRO, DUBAI, LONDON):
        services.record(customer, tv, address, SightingSource.XTREAM, throttle=False)


def test_scoring_flags_and_tells_the_admins_once(
    customer: User,
    tv: Device,
    notices: list[dict[str, Any]],
    django_capture_on_commit_callbacks: Any,
) -> None:
    services.record(customer, tv, RIYADH, SightingSource.XTREAM)
    services.record(customer, None, MOBILE_SA, SightingSource.PORTAL)
    with django_capture_on_commit_callbacks(execute=True):
        assert services.score_recent() == {"assessed": 1, "flagged": 0}
    assert not RiskFlag.objects.exists()
    _spread(customer, tv)
    with django_capture_on_commit_callbacks(execute=True):
        assert services.score_recent()["flagged"] == 1
    flag = RiskFlag.objects.get(user=customer)
    assert flag.status == FlagStatus.OPEN
    assert flag.score >= 4
    assert {item["country"] for item in flag.evidence} >= {"SA", "EG", "AE", "GB"}
    assert len(notices) == 1
    assert "Possible credential sharing" in notices[0]["alerts"][0]["annotations"]["summary"]
    # Scoring again without a higher score says nothing new; and never locks anything.
    with django_capture_on_commit_callbacks(execute=True):
        services.score_recent()
    assert len(notices) == 1
    customer.refresh_from_db()
    assert customer.status == UserStatus.ACTIVE
    assert AuditLog.objects.filter(action="customer.risk_flag").count() == 1


def test_the_ladder(
    customer: User, tv: Device, make_admin: AdminFactory, django_capture_on_commit_callbacks: Any
) -> None:
    _spread(customer, tv)
    services.score_recent()
    admin = make_admin("support")
    client = APIClient()
    client.force_authenticate(admin)
    base = f"/api/v1/admin/customers/{customer.pk}/risk"
    overview = client.get(base, headers=ADMIN).json()
    assert overview["flag"]["status"] == "open"
    assert overview["assessment"]["flagged"] is True
    assert overview["geoip_installed"] is True
    assert overview["attribution_url"] == "https://db-ip.com"
    assert {row["country"] for row in overview["sightings"]} >= {"SA", "EG"}
    # 1. Warn.
    assert client.post(f"{base}/warn", headers=ADMIN).status_code == 204
    assert NotificationOutbox.objects.filter(template_key="sharing_warning").count() == 1
    # 2. Reset every IPTV login: the customer makes new ones; nobody learns a password.
    owner = make_admin("owner")
    owner_client = APIClient()
    owner_client.force_authenticate(owner)
    old_hash = tv.credential.password_hash
    with django_capture_on_commit_callbacks(execute=True):
        reset = owner_client.post(f"{base}/reset-credentials", headers=ADMIN)
    assert reset.json() == {"logins": 1}
    tv.credential.refresh_from_db()
    assert tv.credential.password_hash != old_hash
    sharing = NotificationOutbox.objects.get(template_key="sharing_reset")
    assert sharing.payload == {"count": 1}
    assert not NotificationOutbox.objects.filter(template_key="credentials_reset").exists()
    # 3. Suspend.
    assert client.post(f"{base}/suspend", headers=ADMIN).status_code == 204
    customer.refresh_from_db()
    assert customer.status == UserStatus.SUSPENDED
    flag = RiskFlag.objects.get(user=customer)
    assert [item["action"] for item in flag.actions] == ["warn", "reset", "suspend"]
    # Reviewed: the flag closes, with the note.
    reviewed = client.post(
        f"{base}/review", {"note": "Talked to them"}, format="json", headers=ADMIN
    )
    assert reviewed.json()["status"] == "reviewed"
    assert client.post(f"{base}/review", headers=ADMIN).status_code == 404
    # The list filter finds flagged customers (open flags only).
    assert client.get("/api/v1/admin/customers?risk=flagged", headers=ADMIN).json()["count"] == 0


def test_flag_filter_and_badge(customer: User, tv: Device, make_admin: AdminFactory) -> None:
    _spread(customer, tv)
    services.score_recent()
    client = APIClient()
    client.force_authenticate(make_admin("viewer"))
    page = client.get("/api/v1/admin/customers?risk=flagged", headers=ADMIN).json()
    assert [row["id"] for row in page["results"]] == [str(customer.pk)]
    assert page["results"][0]["risk_flagged"] is True


def test_permissions(customer: User, make_admin: AdminFactory) -> None:
    viewer = APIClient()
    viewer.force_authenticate(make_admin("viewer"))
    base = f"/api/v1/admin/customers/{customer.pk}/risk"
    assert viewer.get(base, headers=ADMIN).status_code == 200
    assert viewer.post(f"{base}/warn", headers=ADMIN).status_code == 403
    assert viewer.post(f"{base}/reset-credentials", headers=ADMIN).status_code == 403


def test_geoip_admin_endpoints(make_admin: AdminFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    client = APIClient()
    client.force_authenticate(make_admin("owner"))
    status = client.get("/api/v1/admin/geoip", headers=ADMIN).json()
    assert status["databases"]["country"]["installed"] is True
    found = client.post("/api/v1/admin/geoip/lookup", {"ip": LONDON}, format="json", headers=ADMIN)
    assert found.json() == {
        "ip": LONDON,
        "country": "GB",
        "asn": 20712,
        "asn_org": "Example Broadband GB",
        "local": False,
    }
    queued: list[dict[str, Any]] = []
    from apps.antisharing import tasks  # noqa: PLC0415

    monkeypatch.setattr(tasks.update_geoip, "delay", lambda **kwargs: queued.append(kwargs))
    assert client.post("/api/v1/admin/geoip/update", headers=ADMIN).status_code == 202
    assert queued == [{"force": True}]


def test_missing_database_alert(
    geoip_dir: Path, notices: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in geoip.FILES.values():
        (geoip_dir / name).unlink()
    geoip.reset_readers()
    now = timezone.now()
    assert services.check_geoip(now) is False  # a new installation gets an hour
    assert services.check_geoip(now + timedelta(hours=2)) is True
    assert notices[-1]["status"] == "firing"
    assert services.check_geoip(now + timedelta(hours=3)) is False  # once
    geoip_data.install(geoip_dir)
    geoip.reset_readers()
    assert services.check_geoip(now + timedelta(hours=4)) is True
    assert notices[-1]["status"] == "resolved"


def test_retention_removes_old_sightings(customer: User, tv: Device) -> None:
    old = services.record(customer, tv, RIYADH, SightingSource.XTREAM, throttle=False)
    assert old is not None
    Sighting.objects.filter(pk=old.pk).update(last_seen=timezone.now() - timedelta(days=200))
    services.record(customer, tv, CAIRO, SightingSource.XTREAM, throttle=False)
    counts = retention.apply()
    assert counts["sightings_deleted"] == 1
    assert Sighting.objects.count() == 1


def test_revoke_by_app_after_a_vendor_breach(
    make_customer: CustomerFactory, make_admin: AdminFactory
) -> None:
    first = make_customer(devices=2, max_devices=3, email="a@example.com")
    second = make_customer(devices=1, email="b@example.com")
    Device.objects.filter(user__in=[first, second]).update(app_hint="ibo")
    client = APIClient()
    client.force_authenticate(make_admin("owner"))
    url = "/api/v1/admin/devices/revoke-by-app"
    assert client.post(url, {"app_hint": "ibo"}, format="json", headers=ADMIN).json() == {
        "devices": 3,
        "dry_run": True,
    }
    done = client.post(url, {"app_hint": "ibo", "dry_run": False}, format="json", headers=ADMIN)
    assert done.json() == {"devices": 3, "dry_run": False}
    assert not Device.objects.filter(app_hint="ibo", revoked_at__isnull=True).exists()
    told = NotificationOutbox.objects.filter(template_key="devices_revoked")
    assert {row.user_id for row in told} == {first.pk, second.pk}
    assert told.first().payload == {"app": "IBO Player"}  # type: ignore[union-attr]
    entry = AuditLog.objects.get(action="devices.revoke_by_app")
    assert entry.after == {"app_hint": "ibo", "devices": 3, "customers": 2}
    bad = client.post(url, {"app_hint": "nope"}, format="json", headers=ADMIN)
    assert bad.status_code == 400
