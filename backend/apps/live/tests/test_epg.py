"""Guide imports into the partitioned programme table (apps.live.epg, ADR-0017)."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from django.utils import timezone

from apps.core.partitions import (
    add_months,
    drop_monthly_before,
    ensure_monthly,
    list_partitions,
    month_start,
    partition_name,
)
from apps.live import epg, sources
from apps.live.models import EpgChannel, EpgProgram, EpgSource, EpgSourceKind, LiveChannel
from apps.live.tests.conftest import ChannelFactory

pytestmark = pytest.mark.django_db


def guide(
    start: datetime, count: int = 4, channel: str = "one.example", step_min: int = 30
) -> bytes:
    def stamp(moment: datetime) -> str:
        return moment.astimezone(UTC).strftime("%Y%m%d%H%M%S +0000")

    rows = [
        '<?xml version="1.0" encoding="UTF-8"?><tv>',
        f'<channel id="{channel}"><display-name lang="en">One</display-name></channel>',
        '<channel id="other.example"><display-name>Other</display-name></channel>',
    ]
    for index in range(count):
        begin = start + timedelta(minutes=step_min * index)
        end = begin + timedelta(minutes=step_min)
        rows.append(
            f'<programme start="{stamp(begin)}" stop="{stamp(end)}" channel="{channel}">'
            f'<title lang="en">Show {index}</title><title lang="ar">برنامج {index}</title>'
            f'<desc lang="en">About {index}</desc></programme>'
        )
        rows.append(
            f'<programme start="{stamp(begin)}" channel="other.example">'
            "<title>X</title></programme>"
        )
    rows.append("</tv>")
    return "".join(rows).encode()


def now_hour() -> datetime:
    return timezone.now().replace(minute=0, second=0, microsecond=0)


def test_imports_only_mapped_channels_and_keeps_every_channel(
    make_channel: ChannelFactory, upload_source: Callable[..., EpgSource]
) -> None:
    make_channel(epg_channel_id="one.example")
    source = upload_source(guide(now_hour()))
    result = epg.import_source(source)
    assert result is not None
    assert (result.channels, result.programmes, result.mapped) == (2, 4, 1)
    assert set(EpgChannel.objects.values_list("xmltv_id", flat=True)) == {
        "one.example",
        "other.example",
    }
    first = EpgProgram.objects.order_by("start").first()
    assert first is not None
    assert (first.title, first.title_ar, first.lang, first.description) == (
        "Show 0",
        "برنامج 0",
        "en",
        "About 0",
    )
    source.refresh_from_db()
    assert source.last_ok_at is not None
    assert source.last_error == ""
    assert source.stats["programmes"] == 4


def test_a_reimport_replaces_from_the_first_new_start(
    make_channel: ChannelFactory, upload_source: Callable[..., EpgSource]
) -> None:
    make_channel(epg_channel_id="one.example")
    start = now_hour()
    source = upload_source(guide(start, count=4))
    epg.import_source(source)
    source.upload = epg.compress_upload(guide(start + timedelta(hours=1), count=4))
    source.save()
    epg.import_source(source)
    starts = list(EpgProgram.objects.order_by("start").values_list("start", flat=True))
    # The first hour of the old guide stays; the rest is the new guide.
    assert starts[0] == start
    assert len(starts) == 2 + 4
    assert len(set(starts)) == len(starts)


def test_programmes_outside_the_window_and_overlaps_are_dropped(
    make_channel: ChannelFactory, upload_source: Callable[..., EpgSource]
) -> None:
    make_channel(epg_channel_id="one.example")
    source = upload_source(guide(now_hour() - timedelta(days=30), count=2))
    result = epg.import_source(source)
    assert result is not None
    assert result.programmes == 0


def test_a_broken_upload_keeps_the_old_guide_and_records_the_error(
    make_channel: ChannelFactory, upload_source: Callable[..., EpgSource]
) -> None:
    make_channel(epg_channel_id="one.example")
    source = upload_source(guide(now_hour()))
    epg.import_source(source)
    source.upload = epg.compress_upload(b"<tv><broken")
    source.save()
    assert epg.import_source(source) is None
    source.refresh_from_db()
    assert "not well-formed" in source.last_error
    assert EpgProgram.objects.count() == 4


def test_url_sources_use_conditional_requests(make_channel: ChannelFactory) -> None:
    make_channel(epg_channel_id="one.example")
    url = "https://guide.example.com/xmltv.xml?key=feed-secret-123"
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.headers.get("If-None-Match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, content=guide(now_hour()), headers={"ETag": '"v1"'})

    source = EpgSource.objects.create(
        name="Feed", kind=EpgSourceKind.URL, url_encrypted=sources.encrypt(url)
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    first = epg.import_source(source, client=client)
    second = epg.import_source(source, client=client)
    assert first is not None
    assert first.programmes == 4
    assert second is not None
    assert second.not_modified
    assert seen[1].headers["If-None-Match"] == '"v1"'
    assert EpgProgram.objects.count() == 4


def test_fetch_errors_never_name_the_url(make_channel: ChannelFactory) -> None:
    url = "https://guide.example.com/xmltv.xml?key=feed-secret-123"
    source = EpgSource.objects.create(
        name="Feed", kind=EpgSourceKind.URL, url_encrypted=sources.encrypt(url)
    )

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url}")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert epg.import_source(source, client=client) is None
    source.refresh_from_db()
    assert "feed-secret-123" not in source.last_error
    assert "ConnectError" in source.last_error


def test_deleting_a_source_cascades_in_the_database(
    make_channel: ChannelFactory, upload_source: Callable[..., EpgSource]
) -> None:
    make_channel(epg_channel_id="one.example")
    source = upload_source(guide(now_hour()))
    epg.import_source(source)
    source.delete()
    assert EpgProgram.objects.count() == 0
    assert EpgChannel.objects.count() == 0


@pytest.mark.parametrize(
    ("cron", "ok"),
    [("0 */6 * * *", True), ("*/5 * * * *", True), ("0 * *", False), ("x * * * *", False)],
)
def test_validate_cron(cron: str, ok: bool) -> None:
    if ok:
        assert epg.validate_cron(cron) == cron
    else:
        with pytest.raises(Exception, match="cron"):
            epg.validate_cron(cron)


def test_is_due(upload_source: Callable[..., EpgSource]) -> None:
    source = upload_source(b"<tv/>", refresh_cron="0 */6 * * *")
    assert epg.is_due(source)  # never ran
    now = datetime(2026, 10, 4, 12, 30, tzinfo=UTC)
    source.last_run_at = datetime(2026, 10, 4, 12, 5, tzinfo=UTC)
    assert not epg.is_due(source, now)
    source.last_run_at = datetime(2026, 10, 4, 11, 55, tzinfo=UTC)
    assert epg.is_due(source, now)
    source.enabled = False
    assert not epg.is_due(source, now)


# --- Partitions ----------------------------------------------------------------------------


def test_partitions_are_created_listed_and_dropped() -> None:
    table = epg.PROGRAM_TABLE
    far = month_start(datetime(2031, 1, 15, tzinfo=UTC))
    created = ensure_monthly(table, far, 2)
    assert created == [partition_name(table, far), partition_name(table, add_months(far, 1))]
    assert ensure_monthly(table, far, 2) == []  # idempotent
    names = {partition.name for partition in list_partitions(table)}
    assert set(created) <= names
    # A row in the new month lands in its partition.
    channel_source = EpgSource.objects.create(name="S", kind=EpgSourceKind.UPLOAD)
    guide_channel = EpgChannel.objects.create(source=channel_source, xmltv_id="x")
    EpgProgram.objects.create(
        channel=guide_channel, start=datetime(2031, 1, 20, tzinfo=UTC),
        stop=datetime(2031, 1, 20, 1, tzinfo=UTC), title="Far",
    )  # fmt: skip
    assert EpgProgram.objects.filter(title="Far").count() == 1
    dropped = drop_monthly_before(table, add_months(far, 2))
    assert set(created) <= set(dropped)
    assert EpgProgram.objects.filter(title="Far").count() == 0


def test_partition_names_are_checked() -> None:
    with pytest.raises(ValueError, match="partitioned table"):
        ensure_monthly("live_epgprogram; DROP TABLE x", datetime(2031, 1, 1, tzinfo=UTC))


def test_the_maintenance_task_keeps_the_coming_months() -> None:
    from apps.live.tasks import maintain_epg_partitions  # noqa: PLC0415

    maintain_epg_partitions()
    names = {partition.name for partition in list_partitions(epg.PROGRAM_TABLE)}
    this_month = month_start(timezone.now())
    for offset in range(3):
        assert partition_name(epg.PROGRAM_TABLE, add_months(this_month, offset)) in names


def test_mapping_a_channel_queues_its_guide(
    make_channel: ChannelFactory,
    upload_source: Callable[..., EpgSource],
    django_capture_on_commit_callbacks: Callable[..., object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.live import services  # noqa: PLC0415

    source = upload_source(guide(now_hour()))
    epg.import_source(source)  # nothing mapped yet: channels only
    assert EpgProgram.objects.count() == 0
    queued: list[str] = []
    monkeypatch.setattr("apps.live.tasks.import_epg_source.delay", queued.append)
    channel = make_channel()
    with django_capture_on_commit_callbacks(execute=True):  # type: ignore[operator]
        services.update_channel(channel, {"epg_channel_id": "one.example"}, actor=None, ip=None)
    assert queued == [str(source.pk)]
    assert LiveChannel.objects.get(pk=channel.pk).epg_channel_id == "one.example"
