"""Integration syncs, source probes, tasks and the management commands (ADR-0017)."""

import base64
import io
import json
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone
from PIL import Image

from apps.catalog.models import Category
from apps.core import healthcheck
from apps.live import integrations, probe, sources, tasks
from apps.live.management.commands import live_demo, run_live
from apps.live.models import EpgChannel, EpgSource, LiveChannel, LiveIntegration
from apps.live.tests.conftest import ChannelFactory
from apps.live.xmltv import read_xmltv
from apps.live.xtream import logo_url

pytestmark = pytest.mark.django_db


def ersatztv(**overrides: Any) -> LiveIntegration:
    values: dict[str, Any] = {
        "kind": "ersatztv",
        "name": "Home ETV",
        "base_url": "http://ersatztv.lan:8409",
        "rights_holder": "Our own library",
        "license_ref": "own",
        "credentials_encrypted": integrations.encrypt_credentials({"access_token": "etv-token-1"}),
    }
    values.update(overrides)
    return LiveIntegration.objects.create(**values)


def etv_transport(channels: list[dict[str, Any]], seen: list[httpx.Request]) -> httpx.MockTransport:
    guide = (
        b'<tv><channel id="C1.ersatztv.org"><display-name>Movies</display-name></channel>'
        b'<channel id="C2.ersatztv.org"><display-name>Kids</display-name></channel></tv>'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/channels":
            return httpx.Response(200, json=channels)
        if request.url.path == "/iptv/xmltv.xml":
            return httpx.Response(200, content=guide)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_ersatztv_sync_creates_disabled_channels_on_its_own_origin() -> None:
    integration = ersatztv()
    seen: list[httpx.Request] = []
    channels = [
        {"id": 1, "number": "1", "name": "Movies"},
        {"id": 2, "number": "2", "name": "Kids"},
        {"id": 3, "number": "../evil", "name": "Bad"},
        {"id": 4, "number": "4", "name": ""},
    ]
    client = httpx.Client(transport=etv_transport(channels, seen))
    result = integrations.sync(integration, client=client)
    assert (result.created, result.total, result.guide) == (2, 2, True)
    rows = list(LiveChannel.objects.order_by("origin_ref"))
    assert [(row.name, row.enabled, row.origin_ref) for row in rows] == [
        ("Movies", False, "1"),
        ("Kids", False, "2"),
    ]
    assert rows[0].rights_holder == "Our own library"
    url = sources.decrypt(rows[0].source_encrypted)
    assert url.startswith("http://ersatztv.lan:8409/iptv/channel/1.ts")
    assert "access_token=etv-token-1" in url
    assert seen[0].headers["Authorization"] == "Bearer etv-token-1"
    # The guide source was made and the channels mapped to ErsatzTV's ids.
    assert {row.epg_channel_id for row in rows} == {"C1.ersatztv.org", "C2.ersatztv.org"}
    assert EpgChannel.objects.count() == 2
    integration.refresh_from_db()
    assert integration.last_error == ""
    assert integration.group is not None
    assert integration.last_result["created"] == 2


def test_a_second_sync_updates_and_disables_what_is_gone() -> None:
    integration = ersatztv()
    client = httpx.Client(transport=etv_transport([{"number": "1", "name": "Movies"}], []))
    integrations.sync(integration, client=client)
    LiveChannel.objects.update(enabled=True)
    client = httpx.Client(transport=etv_transport([{"number": "2", "name": "Kids"}], []))
    result = integrations.sync(integration, client=client)
    assert (result.created, result.missing) == (1, 1)
    assert not LiveChannel.objects.get(origin_ref="1").enabled
    assert LiveChannel.objects.count() == 2


def test_mediamtx_sync_builds_rtsp_urls_with_the_reader() -> None:
    integration = LiveIntegration.objects.create(
        kind="mediamtx",
        name="Studio",
        base_url="http://mediamtx:9997",
        stream_base_url="rtsp://mediamtx:8554",
        rights_holder="Our studio",
        credentials_encrypted=integrations.encrypt_credentials(
            {"username": "reader", "password": "p@ss/word"}
        ),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v3/paths/list"
        assert request.headers["Authorization"].startswith("Basic ")
        items = [{"name": "cam1"}, {"name": "studio/main"}, {"name": "../x"}, {"name": "a b"}]
        return httpx.Response(200, json={"items": items})

    result = integrations.sync(
        integration, client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    assert result.created == 2
    urls = sorted(sources.decrypt(row.source_encrypted) for row in LiveChannel.objects.all())
    assert urls == [
        "rtsp://reader:p%40ss%2Fword@mediamtx:8554/cam1",
        "rtsp://reader:p%40ss%2Fword@mediamtx:8554/studio/main",
    ]


def test_sync_errors_are_recorded_without_secrets() -> None:
    integration = ersatztv()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"refused {request.url}")

    result = integrations.sync(
        integration, client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    assert result.errors
    integration.refresh_from_db()
    assert "ConnectError" in integration.last_error
    assert "etv-token-1" not in integration.last_error
    assert not LiveChannel.objects.exists()


def test_sync_refuses_internal_hosts() -> None:
    integration = ersatztv(base_url="http://web:8000")
    result = integrations.sync(integration)
    assert result.errors
    assert "internal_host" in result.errors[0]


# --- Probes --------------------------------------------------------------------------------


def test_summaries_say_whether_a_source_can_be_copied() -> None:
    document = {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1280,
                "height": 720,
                "avg_frame_rate": "25/1",
            },
            {"codec_type": "audio", "codec_name": "aac", "channels": 2},
        ],
        "format": {"bit_rate": "2500000"},
    }
    summary = probe.summarise(document)
    assert summary["copy_ok"] is True
    assert (summary["height"], summary["bitrate_kbps"]) == (720, 2500)
    assert summary["video"]["fps"] == 25.0
    document["streams"][1]["codec_name"] = "opus"
    assert probe.summarise(document)["copy_ok"] is False
    assert probe.summarise({"streams": []})["error"] == "no_video"


def test_probes_refuse_internal_destinations() -> None:
    result = probe.probe("http://postgres:5432/")
    assert result["ok"] is False
    assert result["error"] == "unsafe_destination"


def test_a_real_probe_of_an_unreachable_source_is_redacted() -> None:
    result = probe.probe("http://192.0.2.1:9/stream.ts?token=probe-secret-5", timeout_s=4)
    assert result["ok"] is False
    assert result["error"] in {"unreachable", "timeout", "ffprobe_missing"}
    assert "probe-secret-5" not in json.dumps(result)


def test_probe_tasks_store_their_result(
    make_channel: ChannelFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    channel = make_channel()
    seen: list[str] = []

    def fake_probe(url: str, **_: Any) -> dict[str, Any]:
        seen.append(url)
        return {"ok": True, "height": 1080, "copy_ok": True}

    monkeypatch.setattr(probe, "probe", fake_probe)
    assert tasks.probe_channel(str(channel.pk), "req1")
    assert tasks.read_probe("req1") == {"ok": True, "height": 1080, "copy_ok": True}
    channel.refresh_from_db()
    assert channel.height == 1080
    assert tasks.probe_url(sources.encrypt("rtsp://cam.lan/x"), "req2")
    assert seen == [sources.decrypt(channel.source_encrypted), "rtsp://cam.lan/x"]
    assert not tasks.probe_url("garbage", "req3")
    assert tasks.read_probe("req3") == {"ok": False, "error": "unreadable_source", "detail": ""}


def test_logos_are_stored_as_webp(make_channel: ChannelFactory, data_root: Path) -> None:
    channel = make_channel()
    buffer = io.BytesIO()
    Image.new("RGB", (200, 200), (10, 20, 30)).save(buffer, format="PNG")
    assert tasks.store_channel_logo(
        str(channel.pk), upload_b64=base64.b64encode(buffer.getvalue()).decode()
    )
    channel.refresh_from_db()
    assert logo_url(channel.logo).endswith(".webp")
    assert not tasks.store_channel_logo(
        str(channel.pk), upload_b64=base64.b64encode(b"nope").decode()
    )


def test_the_beat_tasks(
    make_channel: ChannelFactory,
    upload_source: Callable[..., EpgSource],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queued: list[str] = []
    monkeypatch.setattr(tasks.import_epg_source, "delay", queued.append)
    source = upload_source(b"<tv/>")
    assert tasks.refresh_due_epg_sources() == 1
    assert queued == [str(source.pk)]
    assert tasks.import_epg_source(str(source.pk)) == 0
    assert tasks.import_epg_source("01920000-0000-7000-8000-000000000000") == 0
    make_channel(license_expires_at=timezone.now() - timedelta(minutes=5))
    assert tasks.enforce_licences() == 1


def test_sync_task(monkeypatch: pytest.MonkeyPatch) -> None:
    integration = ersatztv()
    monkeypatch.setattr(integrations, "sync", lambda item: integrations.SyncResult(created=3))
    assert tasks.sync_integration(str(integration.pk))["created"] == 3
    assert tasks.sync_integration("01920000-0000-7000-8000-000000000000") == {}


# --- Management commands ---------------------------------------------------------------------


def test_live_demo_needs_debug(settings: Any) -> None:
    settings.DEBUG = False
    with pytest.raises(CommandError, match="DEBUG"):
        call_command("live_demo")


def test_live_demo_makes_the_channel_and_its_guide(settings: Any) -> None:
    settings.DEBUG = True
    call_command("live_demo")
    call_command("live_demo")  # idempotent
    channel = LiveChannel.objects.get()
    assert channel.enabled
    assert channel.catchup_days == 1
    assert sources.decrypt(channel.source_encrypted) == live_demo.SOURCE_URL
    assert Category.objects.get(slug="showcase").kind == "live"
    assert channel.epg_source is not None
    assert channel.epg_source.stats["programmes"] > 0
    with pytest.raises(CommandError, match="catch-up"):
        call_command("live_demo", wait=1)


def test_the_demo_guide_is_valid_xmltv() -> None:
    channels: list[Any] = []
    programmes: list[Any] = []
    read_xmltv(
        [live_demo.demo_guide(timezone.now())],
        max_bytes=10**7,
        on_channel=channels.append,
        on_programme=programmes.append,
    )
    assert [channel.xmltv_id for channel in channels] == [live_demo.EPG_ID]
    assert len(programmes) == 5 * 48


def test_run_live_loads_enabled_licensed_channels(
    make_channel: ChannelFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run_live, "close_old_connections", lambda: None)  # the test's transaction
    on = make_channel(catchup_days=2, transcode="h264")
    make_channel(enabled=False)
    make_channel(license_expires_at=timezone.now() - timedelta(days=1))
    broken = make_channel()
    LiveChannel.objects.filter(pk=broken.pk).update(source_encrypted="not-a-token")
    specs = run_live.load_specs()
    assert list(specs) == [on.storage_key]
    spec = specs[on.storage_key]
    assert (spec.catchup_days, spec.transcode) == (2, True)
    limits = run_live.load_limits()
    assert limits.max_running == 20
    with pytest.raises(ValueError, match="internal_host"):
        run_live.guard("http://web:8000/internal/stream-auth")


def test_healthchecks(monkeypatch: pytest.MonkeyPatch) -> None:
    assert healthcheck.main(["nonsense"]) == 2
    monkeypatch.setattr(healthcheck, "_fresh", lambda key, age: key == "hb:live")
    assert healthcheck.check_live()
    assert not healthcheck.check_relay()  # nothing listens on 8090 in the test container
