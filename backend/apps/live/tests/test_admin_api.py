"""The live TV admin API (`/api/v1/admin/live/...`) and its services (ADR-0017)."""

import io
import json
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.catalog.models import Category, CategoryKind
from apps.conftest import AdminFactory, CustomerFactory
from apps.core.stores import state_redis
from apps.live import layout, services, sources, tasks
from apps.live.models import EpgSource, LiveChannel, LiveIntegration
from apps.live.tests.conftest import SOURCE_URL, ChannelFactory
from apps.playback.concurrency import KickReason
from apps.playback.models import EndReason, PlaybackSession, TitleKind

pytestmark = pytest.mark.django_db

BASE = "/api/v1/admin/live"


def admin_client(user: User) -> APIClient:
    client = APIClient(HTTP_HOST=settings.ADMIN_HOST)
    client.force_authenticate(user)
    return client


@pytest.fixture
def manager(make_admin: AdminFactory) -> APIClient:
    return admin_client(make_admin(permissions=["library.view", "library.manage"]))


@pytest.fixture
def viewer_admin(make_admin: AdminFactory) -> APIClient:
    return admin_client(make_admin(permissions=["library.view"]))


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, tuple[Any, ...], dict[str, Any]]]:
    """Celery sends, recorded instead of queued."""
    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
    for name in (
        "import_epg_source",
        "probe_channel",
        "probe_url",
        "sync_integration",
        "store_channel_logo",
    ):
        task = getattr(tasks, name)
        monkeypatch.setattr(task, "delay", lambda *a, _n=name, **k: calls.append((_n, a, k)))
    return calls


def body_of(response: Any) -> str:
    return json.dumps(response.json(), ensure_ascii=False)


def channel_payload(group: Category, **overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "name": "Studio One",
        "name_ar": "الاستوديو",
        "group": str(group.pk),
        "source_url": SOURCE_URL,
        "rights_holder": "Smart IPTV",
        "license_ref": "own-production",
        "catchup_days": 1,
        "enabled": True,
    }
    values.update(overrides)
    return values


# --- Channels ----------------------------------------------------------------------------------


def test_create_never_echoes_the_source(manager: APIClient, live_group: Category) -> None:
    response = manager.post(f"{BASE}/channels", channel_payload(live_group), format="json")
    assert response.status_code == 201, response.json()
    data = response.json()
    assert data["source_info"] == {"scheme": "rtsp", "host": "encoder.example.net", "port": 8554}
    assert "s3cr3t-source-token" not in body_of(response)
    assert data["status"]["state"] == "idle"
    channel = LiveChannel.objects.get()
    assert sources.decrypt(channel.source_encrypted) == SOURCE_URL
    entry = AuditLog.objects.get(action="live.channel.create")
    assert "s3cr3t-source-token" not in json.dumps(entry.after)
    assert entry.after["source"] == "rtsp://encoder.example.net"
    listed = manager.get(f"{BASE}/channels")
    assert "s3cr3t-source-token" not in body_of(listed)


@pytest.mark.parametrize(
    ("overrides", "field", "code"),
    [
        ({"source_url": "file:///etc/passwd"}, "source_url", "invalid_source_url"),
        ({"source_url": "http://postgres:5432/"}, "source_url", "unsafe_destination"),
        ({"source_url": "http://169.254.169.254/latest"}, "source_url", "unsafe_destination"),
        ({"rights_holder": ""}, "rights_holder", "required"),
        ({"catchup_days": 30}, "catchup_days", "max_value"),
        ({"epg_channel_id": "has space"}, "epg_channel_id", "invalid_epg_channel_id"),
    ],
)
def test_create_validation(
    manager: APIClient, live_group: Category, overrides: dict[str, Any], field: str, code: str
) -> None:
    response = manager.post(
        f"{BASE}/channels", channel_payload(live_group, **overrides), format="json"
    )
    assert response.status_code == 400, response.json()
    assert response.json()["field_error_codes"][field] == [code]


def test_a_vod_category_is_not_a_group(manager: APIClient, category: Category) -> None:
    response = manager.post(f"{BASE}/channels", channel_payload(category), format="json")
    assert response.status_code == 400


def test_reading_needs_library_view_and_writing_library_manage(
    viewer_admin: APIClient, make_admin: AdminFactory, live_group: Category
) -> None:
    assert viewer_admin.get(f"{BASE}/channels").status_code == 200
    denied = viewer_admin.post(f"{BASE}/channels", channel_payload(live_group), format="json")
    assert denied.status_code == 403
    nobody = admin_client(make_admin(permissions=["customers.view"]))
    assert nobody.get(f"{BASE}/channels").status_code == 403
    assert APIClient(HTTP_HOST=settings.ADMIN_HOST).get(f"{BASE}/channels").status_code in (
        401,
        403,
    )


def test_update_keeps_the_source_unless_given(
    manager: APIClient, make_channel: ChannelFactory
) -> None:
    channel = make_channel()
    response = manager.patch(f"{BASE}/channels/{channel.pk}", {"name": "Renamed"}, format="json")
    assert response.status_code == 200
    channel.refresh_from_db()
    assert sources.decrypt(channel.source_encrypted) == SOURCE_URL
    manager.patch(
        f"{BASE}/channels/{channel.pk}",
        {"source_url": "https://cdn.example.com/a.m3u8"},
        format="json",
    )
    channel.refresh_from_db()
    assert sources.decrypt(channel.source_encrypted) == "https://cdn.example.com/a.m3u8"
    entry = AuditLog.objects.filter(action="live.channel.update").latest("at")
    assert entry.after["source_changed"] is True


def open_session(channel: LiveChannel, customer: User) -> PlaybackSession:
    now = timezone.now()
    return PlaybackSession.objects.create(
        session_key=f"{abs(hash(channel.pk)):032x}"[:32],
        user=customer,
        title_kind=TitleKind.LIVE,
        title_id=channel.pk,
        rendition="live",
        started_at=now,
        last_heartbeat_at=now,
    )


def test_disabling_stops_the_channels_sessions(
    manager: APIClient,
    make_channel: ChannelFactory,
    make_customer: CustomerFactory,
    django_capture_on_commit_callbacks: Callable[..., Any],
) -> None:
    channel = make_channel()
    session = open_session(channel, make_customer())
    with django_capture_on_commit_callbacks(execute=True):
        response = manager.post(
            f"{BASE}/channels/bulk", {"ids": [str(channel.pk)], "enabled": False}, format="json"
        )
    assert response.status_code == 200
    session.refresh_from_db()
    assert session.end_reason == EndReason.KICKED
    assert state_redis().get(f"kick:{session.session_key}") == b"kicked"


def test_enabling_needs_a_rights_holder(manager: APIClient, make_channel: ChannelFactory) -> None:
    channel = make_channel(enabled=False, rights_holder="")
    response = manager.post(
        f"{BASE}/channels/bulk", {"ids": [str(channel.pk)], "enabled": True}, format="json"
    )
    assert response.status_code == 400
    assert response.json()["field_error_codes"]["ids"] == ["rights_required"]
    assert not LiveChannel.objects.get(pk=channel.pk).enabled


def test_reorder_within_a_group(
    manager: APIClient, make_channel: ChannelFactory, live_group: Category
) -> None:
    first, second, third = make_channel(), make_channel(), make_channel()
    response = manager.post(
        f"{BASE}/channels/reorder",
        {"group": str(live_group.pk), "ids": [str(third.pk), str(first.pk)]},
        format="json",
    )
    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [
        str(third.pk),
        str(first.pk),
        str(second.pk),
    ]


def test_delete_stops_sessions_and_live_groups_with_channels_stay(
    manager: APIClient,
    owner_client: APIClient,
    make_channel: ChannelFactory,
    make_customer: CustomerFactory,
    live_group: Category,
) -> None:
    channel = make_channel()
    admin = {"host": settings.ADMIN_HOST}
    refused = owner_client.delete(f"/api/v1/admin/categories/{live_group.pk}", headers=admin)
    assert refused.status_code == 409
    session = open_session(channel, make_customer())
    assert manager.delete(f"{BASE}/channels/{channel.pk}").status_code == 204
    session.refresh_from_db()
    assert session.ended_at is not None
    deleted = owner_client.delete(f"/api/v1/admin/categories/{live_group.pk}", headers=admin)
    assert deleted.status_code == 204


def test_status_comes_from_the_packager(manager: APIClient, make_channel: ChannelFactory) -> None:
    channel = make_channel(catchup_days=1)
    state_redis().hset(
        layout.status_key(channel.storage_key),
        mapping={
            "state": "live",
            "since": "1791279220",
            "bitrate_kbps": "812",
            "archive_bytes": "5000",
        },
    )
    state_redis().hset(
        layout.archive_key(channel.storage_key),
        mapping={"first": "1791279220000", "last": "1791279260000"},
    )
    data = manager.get(f"{BASE}/channels/{channel.pk}").json()
    assert data["status"]["state"] == "live"
    assert data["status"]["bitrate_kbps"] == 812
    assert data["status"]["recording"] is True
    assert data["status"]["archive_bytes"] == 5000
    assert data["status"]["archive_from"] is not None
    channel.license_expires_at = timezone.now() - timedelta(days=1)
    channel.save()
    assert manager.get(f"{BASE}/channels/{channel.pk}").json()["status"]["state"] == "unlicensed"


def test_channel_list_query_count(
    manager: APIClient,
    make_channel: ChannelFactory,
    django_assert_max_num_queries: Callable[..., Any],
) -> None:
    for _ in range(5):
        make_channel()
    with django_assert_max_num_queries(8):
        response = manager.get(f"{BASE}/channels")
    assert response.json()["count"] == 5


def test_source_tests_are_queued_and_polled(
    manager: APIClient, make_channel: ChannelFactory, queued: list[Any]
) -> None:
    channel = make_channel()
    response = manager.post(f"{BASE}/channels/{channel.pk}/test")
    assert response.status_code == 202
    request_id = response.json()["request_id"]
    assert queued == [("probe_channel", (str(channel.pk), request_id), {})]
    pending = manager.get(f"{BASE}/source-tests/{request_id}").json()
    assert pending == {"status": "pending", "result": None}
    tasks.store_probe(request_id, {"ok": True, "height": 720, "copy_ok": True})
    done = manager.get(f"{BASE}/source-tests/{request_id}").json()
    assert done["status"] == "done"
    assert done["result"]["height"] == 720
    url_test = manager.post(f"{BASE}/source-tests", {"url": SOURCE_URL}, format="json")
    assert url_test.status_code == 202
    name, args, _ = queued[-1]
    assert name == "probe_url"
    assert "s3cr3t" not in args[0]  # the task payload carries the URL encrypted
    assert sources.decrypt(args[0]) == SOURCE_URL


def test_logo_upload_is_stored_on_the_worker(
    manager: APIClient, make_channel: ChannelFactory, queued: list[Any]
) -> None:
    channel = make_channel()
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), (15, 118, 110)).save(buffer, format="PNG")
    png = buffer.getvalue()
    upload = SimpleUploadedFile("logo.png", png, content_type="image/png")
    response = manager.post(
        f"{BASE}/channels/{channel.pk}/logo", {"file": upload}, format="multipart"
    )
    assert response.status_code == 202, response.content
    assert queued[0][0] == "store_channel_logo"


def test_programme_preview(
    manager: APIClient, make_channel: ChannelFactory, upload_source: Callable[..., EpgSource]
) -> None:
    from apps.live import epg  # noqa: PLC0415
    from apps.live.tests.test_epg import guide  # noqa: PLC0415

    channel = make_channel(epg_channel_id="one.example")
    now = timezone.now().replace(minute=0, second=0, microsecond=0)
    epg.import_source(upload_source(guide(now, count=4)))
    rows = manager.get(f"{BASE}/channels/{channel.pk}/programmes").json()
    assert [row["title"] for row in rows] == ["Show 0", "Show 1", "Show 2", "Show 3"]
    assert manager.get(f"{BASE}/channels/{make_channel().pk}/programmes").json() == []


# --- Guide sources -------------------------------------------------------------------------


def test_url_sources_are_write_only(
    manager: APIClient, queued: list[Any], django_capture_on_commit_callbacks: Callable[..., Any]
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        response = manager.post(
            f"{BASE}/epg/sources",
            {"name": "Feed", "url": "https://guide.example.com/x.xml?key=feed-secret-9"},
            format="json",
        )
    assert response.status_code == 201, response.json()
    assert response.json()["url"] == {"scheme": "https", "host": "guide.example.com", "port": None}
    assert "feed-secret-9" not in body_of(response)
    assert queued[0][0] == "import_epg_source"
    assert "feed-secret-9" not in body_of(manager.get(f"{BASE}/epg/sources"))


def test_uploaded_sources_and_refresh(
    manager: APIClient, queued: list[Any], django_capture_on_commit_callbacks: Callable[..., Any]
) -> None:
    upload = SimpleUploadedFile("guide.xml", b"<tv/>", content_type="application/xml")
    with django_capture_on_commit_callbacks(execute=True):
        response = manager.post(
            f"{BASE}/epg/sources", {"name": "File", "file": upload}, format="multipart"
        )
    assert response.status_code == 201, response.json()
    source_id = response.json()["id"]
    assert response.json()["kind"] == "upload"
    assert manager.post(f"{BASE}/epg/sources/{source_id}/refresh").status_code == 202
    assert [name for name, *_ in queued] == ["import_epg_source", "import_epg_source"]
    bad = manager.patch(f"{BASE}/epg/sources/{source_id}", {"refresh_cron": "nope"}, format="json")
    assert bad.status_code == 400
    assert manager.delete(f"{BASE}/epg/sources/{source_id}").status_code == 204


@pytest.mark.parametrize(
    "url", ["http://web:8000/x", "http://127.0.0.1/guide.xml", "ftp://x.example/a"]
)
def test_guide_urls_that_are_refused(manager: APIClient, url: str) -> None:
    response = manager.post(f"{BASE}/epg/sources", {"name": "Bad", "url": url}, format="json")
    assert response.status_code == 400


def test_unmatched_channels_and_guide_search(
    manager: APIClient, make_channel: ChannelFactory, upload_source: Callable[..., EpgSource]
) -> None:
    from apps.live import epg  # noqa: PLC0415
    from apps.live.tests.test_epg import guide  # noqa: PLC0415

    mapped = make_channel(name="One", epg_channel_id="one.example")
    missing = make_channel(name="Other", epg_channel_id="nowhere.example")
    blank = make_channel(name="One HD")
    epg.import_source(upload_source(guide(timezone.now(), count=1)))
    data = manager.get(f"{BASE}/epg/unmatched").json()
    reasons = {item["id"]: item["reason"] for item in data["channels"]}
    assert str(mapped.pk) not in reasons
    assert reasons[str(missing.pk)] == "not_in_guide"
    assert reasons[str(blank.pk)] == "no_id"
    suggestions = next(item for item in data["channels"] if item["id"] == str(blank.pk))[
        "suggestions"
    ]
    assert suggestions[0]["xmltv_id"] == "one.example"
    assert [item["xmltv_id"] for item in data["guide_channels"]] == ["other.example"]
    found = manager.get(f"{BASE}/epg/channels", {"q": "one"}).json()
    assert [item["xmltv_id"] for item in found] == ["one.example"]


# --- Integrations and the overview ---------------------------------------------------------


def test_integrations_keep_secrets_write_only(manager: APIClient, queued: list[Any]) -> None:
    response = manager.post(
        f"{BASE}/integrations",
        {
            "kind": "mediamtx",
            "name": "Studio",
            "base_url": "http://mediamtx:9997",
            "stream_base_url": "rtsp://mediamtx:8554",
            "rights_holder": "Our studio",
            "username": "reader",
            "password": "integration-pass-42",
        },
        format="json",
    )
    assert response.status_code == 201, response.json()
    data = response.json()
    assert data["has_credentials"] is True
    assert "integration-pass-42" not in body_of(response)
    integration_id = data["id"]
    assert manager.post(f"{BASE}/integrations/{integration_id}/sync").status_code == 202
    assert queued[-1] == ("sync_integration", (integration_id,), {})
    patched = manager.patch(
        f"{BASE}/integrations/{integration_id}", {"name": "Studio 2"}, format="json"
    )
    assert patched.json()["has_credentials"] is True
    for bad in ({"base_url": "http://user:pw@mediamtx:9997"}, {"base_url": "http://web:8000"}):
        assert (
            manager.patch(f"{BASE}/integrations/{integration_id}", bad, format="json").status_code
            == 400
        )
    missing = manager.post(f"{BASE}/integrations", {"kind": "mediamtx", "name": "X"}, format="json")
    assert missing.status_code == 400


def test_overview(manager: APIClient, make_channel: ChannelFactory) -> None:
    make_channel(catchup_days=1)
    make_channel(enabled=False)
    state_redis().set(
        layout.PACKAGER_KEY,
        json.dumps({"running": 1, "archive_bytes": 42, "archive_budget_bytes": 1000}),
    )
    data = manager.get(f"{BASE}/overview").json()
    assert data == {
        "packager_running": True,
        "running_channels": 1,
        "channels": 2,
        "enabled_channels": 1,
        "catchup_channels": 1,
        "archive_bytes": 42,
        "archive_budget_bytes": 1000,
        "viewers": 0,
    }


# --- Licences -------------------------------------------------------------------------------


def test_expired_licences_stop_sessions_once(
    make_channel: ChannelFactory, make_customer: CustomerFactory
) -> None:
    channel = make_channel(license_expires_at=timezone.now() - timedelta(minutes=1))
    session = open_session(channel, make_customer())
    assert services.enforce_licences() == 1
    assert services.enforce_licences() == 0
    session.refresh_from_db()
    assert session.end_reason == EndReason.KICKED
    assert (
        state_redis().get(f"kick:{session.session_key}")
        == KickReason.LICENSE_EXPIRED.value.encode()
    )
    channel.refresh_from_db()
    assert channel.license_lapsed
    services.update_channel(
        channel, {"license_expires_at": timezone.now() + timedelta(days=30)}, actor=None, ip=None
    )
    channel.refresh_from_db()
    assert not channel.license_lapsed


def test_integration_model_str() -> None:
    integration = LiveIntegration(kind="ersatztv", name="Home")
    assert str(integration) == "ersatztv:Home"
    assert Category.objects.filter(kind=CategoryKind.LIVE).count() == 0
