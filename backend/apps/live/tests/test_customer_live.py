"""Live TV in the customer API (ADR-0023): the Live page's channels with now and next,
a channel's guide with catch-up, and starting a live or catch-up stream from the portal."""

import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from django.conf import settings
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from pytest_django import Settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.catalog.models import Category, CategoryKind
from apps.conftest import CustomerFactory
from apps.core.stores import state_redis
from apps.live import epg, layout
from apps.live.models import EpgSource, LiveChannel
from apps.live.tests.conftest import ChannelFactory
from apps.playback import tokens
from apps.playback.models import PlaybackSession, TitleKind

pytestmark = pytest.mark.django_db

VECTORS = Path(settings.BASE_DIR) / "apps" / "playback" / "tests" / "data" / "token_vectors.json"
EPG_ID = "showcase.example"
CHANNELS = "/api/v1/live/channels"
START = "/api/v1/playback/start"


@pytest.fixture(autouse=True)
def media_keys(settings: Settings, tmp_path: Path) -> Iterator[tokens.KeySet]:
    keys = json.loads(VECTORS.read_text(encoding="utf-8"))["keys"]
    path = tmp_path / "keys.json"
    path.write_text(json.dumps(keys), encoding="utf-8")
    settings.MEDIA_TOKEN_KEYS_FILE = str(path)
    settings.MEDIA_BASE_URL = "https://media.example.test"
    tokens.reset_keyring()
    yield tokens.parse_keyset(keys)
    tokens.reset_keyring()


def guide_document(start: datetime) -> bytes:
    def stamp(moment: datetime) -> str:
        return moment.astimezone(UTC).strftime("%Y%m%d%H%M%S +0000")

    rows = [f'<tv><channel id="{EPG_ID}"><display-name>Showcase</display-name></channel>']
    for index in range(8):
        begin = start + timedelta(minutes=30 * index)
        rows.append(
            f'<programme start="{stamp(begin)}" stop="{stamp(begin + timedelta(minutes=30))}"'
            f' channel="{EPG_ID}"><title lang="en">Show {index}</title>'
            f'<title lang="ar">برنامج {index}</title><desc lang="en">About {index}</desc>'
            "</programme>"
        )
    rows.append("</tv>")
    return "".join(rows).encode()


@pytest.fixture
def hour() -> datetime:
    """The start of the guide: two hours ago, on the hour."""
    return timezone.now().replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)


@pytest.fixture
def showcase(
    make_channel: ChannelFactory, upload_source: Callable[..., EpgSource], hour: datetime
) -> LiveChannel:
    """A channel with a guide from two hours ago and two days of catch-up."""
    channel = make_channel(
        name="Showcase 24/7",
        name_ar="عرض",
        epg_channel_id=EPG_ID,
        catchup_days=2,
        probe={"video": {"codec": "h264", "height": 720}, "audio": {"codec": "aac"}},
    )
    epg.import_source(upload_source(guide_document(hour)))
    return channel


def archive(channel: LiveChannel, start: datetime, end: datetime) -> None:
    state_redis().hset(
        layout.archive_key(channel.storage_key),
        mapping={
            "first": str(int(start.timestamp() * 1000)),
            "last": str(int(end.timestamp() * 1000)),
        },
    )


def portal(user: User, language: str = "en") -> APIClient:
    client = APIClient()
    client.force_login(user)
    client.defaults["HTTP_HOST"] = settings.APP_HOST
    client.defaults["HTTP_ACCEPT_LANGUAGE"] = language
    return client


@pytest.fixture
def viewer(make_customer: CustomerFactory) -> User:
    return make_customer(max_streams=2)


# --- The Live page -------------------------------------------------------------------------------


def test_channels_by_group_with_now_and_next(
    viewer: User, showcase: LiveChannel, make_channel: ChannelFactory, hour: datetime
) -> None:
    hevc = make_channel(
        name="HEVC Sports", probe={"video": {"codec": "hevc"}, "audio": {"codec": "aac"}}
    )
    hidden = make_channel(name="Apps only")
    hidden.group.visible_in_xtream = False  # the IPTV apps' switch; the portal ignores it
    hidden.group.save(update_fields=["visible_in_xtream"])
    make_channel(name="Off air", enabled=False)
    archive(showcase, timezone.now() - timedelta(hours=3), timezone.now())
    client = portal(viewer)
    with CaptureQueriesContext(connection) as queries:
        response = client.get(CHANNELS)
    assert response.status_code == 200, response.content
    assert response["Cache-Control"] == "private, max-age=30"
    body = response.json()
    assert [group["name"] for group in body["groups"]] == ["Showcase"]
    names = [channel["name"] for channel in body["channels"]]
    assert names == ["Showcase 24/7", "HEVC Sports", "Apps only"]
    first = body["channels"][0]
    assert first["number"] == showcase.xc_id
    assert first["catchup_days"] == 2
    assert first["archive_from"] is not None
    assert first["web_playable"] is True
    now_index = int((timezone.now() - hour) / timedelta(minutes=30))
    assert first["now"]["title"] == f"Show {now_index}"
    assert first["next"]["title"] == f"Show {now_index + 1}"
    assert first["now"]["catchup"] is False
    assert body["channels"][1]["id"] == str(hevc.pk)
    assert body["channels"][1]["web_playable"] is False
    assert body["channels"][1]["now"] is None
    assert body["channels"][1]["archive_from"] is None
    # Channels, their guides and every channel's now and next: three queries.
    live = [query["sql"] for query in queries.captured_queries if '"live_' in query["sql"]]
    assert len(live) == 3, live


def test_arabic_names(make_customer: CustomerFactory, showcase: LiveChannel) -> None:
    body = portal(make_customer(), "ar").get(CHANNELS).json()
    assert body["channels"][0]["name"] == "عرض"
    assert body["channels"][0]["now"]["title"].startswith("برنامج")


def test_plans_without_live_see_no_channels(
    make_customer: CustomerFactory, showcase: LiveChannel
) -> None:
    body = portal(make_customer(allow_live=False)).get(CHANNELS).json()
    assert body == {"groups": [], "channels": []}
    refused = portal(make_customer(allow_live=False)).get(f"{CHANNELS}/{showcase.pk}/guide")
    assert refused.status_code == 404


def test_adult_groups_only_when_granted(
    make_customer: CustomerFactory, make_channel: ChannelFactory, live_group: Category
) -> None:
    adult = Category.objects.create(
        kind=CategoryKind.LIVE, slug="late", name_en="Late", name_ar="ليلي", is_adult=True
    )
    make_channel(name="Late Night", group=adult)
    make_channel(name="Family")
    everyone = portal(make_customer()).get(CHANNELS).json()
    assert [channel["name"] for channel in everyone["channels"]] == ["Family"]
    granted = make_customer(category_ids=[adult.pk])
    names = [channel["name"] for channel in portal(granted).get(CHANNELS).json()["channels"]]
    assert names == ["Late Night"]


def test_the_guide_marks_recorded_programmes(
    viewer: User, showcase: LiveChannel, hour: datetime
) -> None:
    archive(showcase, timezone.now() - timedelta(minutes=70), timezone.now())
    response = portal(viewer).get(f"{CHANNELS}/{showcase.pk}/guide")
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["channel"]["name"] == "Showcase 24/7"
    programmes = body["programmes"]
    assert [item["title"] for item in programmes] == [f"Show {index}" for index in range(8)]
    recorded = [item for item in programmes if item["catchup"]]
    # Only finished programmes that lie inside the archive.
    assert recorded
    for item in recorded:
        assert datetime.fromisoformat(item["stop"]) <= timezone.now()
        assert datetime.fromisoformat(item["start"]) >= timezone.now() - timedelta(minutes=70)
    assert len({item["id"] for item in programmes}) == 8


def test_the_guide_window_is_bounded(viewer: User, showcase: LiveChannel, hour: datetime) -> None:
    client = portal(viewer)
    late = client.get(
        f"{CHANNELS}/{showcase.pk}/guide",
        {
            "from": (hour + timedelta(hours=3)).isoformat(),
            "to": (hour + timedelta(hours=9)).isoformat(),
        },
    )
    assert [item["title"] for item in late.json()["programmes"]] == ["Show 6", "Show 7"]
    assert client.get(f"{CHANNELS}/{showcase.pk}/guide", {"from": "soon"}).status_code == 400
    assert client.get(f"{CHANNELS}/{LiveChannel().pk}/guide").status_code == 404


# --- Playing -------------------------------------------------------------------------------------


def start(client: APIClient, **body: Any) -> Any:
    return client.post(START, body, format="json")


def test_live_playback_from_the_portal(
    viewer: User, showcase: LiveChannel, media_keys: tokens.KeySet
) -> None:
    response = start(portal(viewer), title_type="live", title_id=str(showcase.pk))
    assert response.status_code == 200, response.content
    grant = response.json()
    assert grant["live"] is True
    assert grant["title_type"] == "live"
    assert grant["delivery"] == "segmented"
    assert grant["url"].endswith("/live/index.m3u8")
    assert grant["hls_master_url"] == grant["url"]
    assert grant["duration_ms"] == 0
    token, _, tail = urlsplit(grant["url"]).path[3:].partition("/")
    claims = tokens.verify(media_keys, token, now=int(timezone.now().timestamp()), tail=tail)
    assert claims.title == showcase.storage_key
    assert claims.rendition == "live"
    session = PlaybackSession.objects.get(pk=grant["session_id"])
    assert session.title_kind == TitleKind.LIVE


def test_catch_up_playback_from_the_portal(viewer: User, showcase: LiveChannel) -> None:
    now = timezone.now()
    archive(showcase, now - timedelta(hours=3), now)
    begin = (now - timedelta(hours=1)).replace(second=0, microsecond=0)
    response = start(
        portal(viewer),
        title_type="catchup",
        title_id=str(showcase.pk),
        start=begin.isoformat(),
        duration_min=30,
    )
    assert response.status_code == 200, response.content
    grant = response.json()
    assert grant["live"] is False
    assert grant["title_type"] == "catchup"
    assert grant["duration_ms"] == 30 * 60_000
    assert grant["url"].endswith(f"/archive/{int(begin.timestamp())}-1800.m3u8")


@pytest.mark.parametrize("case", ["no_start", "unrecorded", "hidden", "no_live"])
def test_what_the_portal_cannot_play(
    make_customer: CustomerFactory, showcase: LiveChannel, case: str
) -> None:
    now = timezone.now()
    viewer = make_customer(allow_live=case != "no_live")
    body: dict[str, Any] = {"title_type": "catchup", "title_id": str(showcase.pk)}
    if case == "no_start":
        response = start(portal(viewer), **body)
        assert response.status_code == 400
        assert response.json()["field_error_codes"] == {"start": ["required"]}
        return
    body |= {"start": (now - timedelta(hours=1)).isoformat(), "duration_min": 30}
    if case == "hidden":
        showcase.enabled = False
        showcase.save(update_fields=["enabled"])
    if case == "no_live":
        body = {"title_type": "live", "title_id": str(showcase.pk)}
    response = start(portal(viewer), **body)  # "unrecorded": no archive at all
    assert response.status_code == 404
    assert not PlaybackSession.objects.exists()
