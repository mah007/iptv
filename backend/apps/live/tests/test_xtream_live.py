"""Live TV over the Xtream API, against the compat/ contract (SPEC §7.5, ADR-0017).

The real live source and playback run here: categories and streams, the two EPG
actions with base64 text, xmltv.php, live entries in get.php, and the play URLs
(`/live/...`, the short `/{u}/{p}/{id}`, `/timeshift/...`).
"""

import base64
import gzip
import itertools
import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from django.conf import settings
from django.test import Client
from django.utils import timezone
from pytest_django import Settings

from apps.accounts import services as account_services
from apps.catalog.models import Category, CategoryKind
from apps.core.stores import state_redis
from apps.live import epg, layout
from apps.live.models import EpgSource, LiveChannel
from apps.live.tests.conftest import ChannelFactory
from apps.playback import tokens
from apps.playback.models import PlaybackSession, TitleKind
from apps.xtream_api.auth import server_info
from apps.xtream_api.source import CatalogScope
from apps.xtream_api.tests import contract as contract_module
from apps.xtream_api.tests.conftest import Subscriber, subscribe

pytestmark = pytest.mark.django_db

VECTORS = Path(settings.BASE_DIR) / "apps" / "playback" / "tests" / "data" / "token_vectors.json"
MEDIA = "https://media.example.test"
EPG_ID = "showcase.example"


@pytest.fixture(autouse=True)
def media_keys(settings: Settings, tmp_path: Path) -> Iterator[tokens.KeySet]:
    keys = json.loads(VECTORS.read_text(encoding="utf-8"))["keys"]
    path = tmp_path / "keys.json"
    path.write_text(json.dumps(keys), encoding="utf-8")
    settings.MEDIA_TOKEN_KEYS_FILE = str(path)
    settings.MEDIA_BASE_URL = MEDIA
    tokens.reset_keyring()
    yield tokens.parse_keyset(keys)
    tokens.reset_keyring()


@pytest.fixture
def tv() -> Client:
    return Client(headers={"host": settings.TV_HOST})


@pytest.fixture
def contract() -> contract_module.Contract:
    suite = contract_module.load()
    if suite is None:
        pytest.skip(contract_module.MISSING)
    return suite


_NUMBERS = itertools.count(1)


def customer(**access: object) -> Subscriber:
    number = next(_NUMBERS)
    user, _ = account_services.create_customer(
        {"name": "Live Viewer", "email": f"live{number}@example.com", "locale": "en"},
        access={"max_streams": 2, **access},
        actor=None,
    )
    return subscribe(user)


@pytest.fixture
def viewer(db: None) -> Subscriber:
    return customer()


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
def showcase(make_channel: ChannelFactory, upload_source: Callable[..., EpgSource]) -> LiveChannel:
    """A channel with a guide from two hours ago and catch-up, plus a guide-less one."""
    channel = make_channel(
        name="Showcase 24/7", name_ar="عرض", epg_channel_id=EPG_ID, catchup_days=2
    )
    make_channel(name="No Guide")
    start = timezone.now().replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)
    epg.import_source(upload_source(guide_document(start)))
    return channel


def archive(channel: LiveChannel, start: datetime, end: datetime) -> None:
    state_redis().hset(
        layout.archive_key(channel.storage_key),
        mapping={
            "first": str(int(start.timestamp() * 1000)),
            "last": str(int(end.timestamp() * 1000)),
        },
    )


def api(tv: Client, who: Subscriber, **params: str) -> bytes:
    response = tv.get("/player_api.php", who.params(**params))
    assert response.status_code == 200
    return response.content


# --- Catalog actions ---------------------------------------------------------------------------


def test_live_categories_and_streams_match_the_contract(
    tv: Client, viewer: Subscriber, showcase: LiveChannel, contract: contract_module.Contract
) -> None:
    categories = contract.check(
        "get_live_categories", api(tv, viewer, action="get_live_categories")
    )
    assert [item["category_name"] for item in categories] == ["Showcase"]
    streams = contract.check("get_live_streams", api(tv, viewer, action="get_live_streams"))
    assert [item["name"] for item in streams] == ["Showcase 24/7", "No Guide"]
    first = streams[0]
    assert first["stream_id"] == showcase.xc_id
    assert first["epg_channel_id"] == EPG_ID
    assert (first["tv_archive"], first["tv_archive_duration"]) == (1, 2)
    assert first["direct_source"] == ""
    assert first["category_id"] == categories[0]["category_id"]
    assert streams[1]["epg_channel_id"] == ""
    assert streams[1]["tv_archive"] == 0
    filtered = contract.check(
        "get_live_streams",
        api(tv, viewer, action="get_live_streams", category_id=first["category_id"]),
    )
    assert len(filtered) == 2
    assert api(tv, viewer, action="get_live_streams", category_id="999999") == b"[]"


def test_arabic_viewers_get_arabic_names(
    tv: Client, showcase: LiveChannel, contract: contract_module.Contract
) -> None:
    arabic = customer()
    arabic.user.locale = "ar"
    arabic.user.save(update_fields=["locale"])
    streams = contract.check("get_live_streams", api(tv, arabic, action="get_live_streams"))
    assert streams[0]["name"] == "عرض"


@pytest.mark.parametrize(
    "change",
    ["disabled", "licence", "hidden_group", "no_live", "other_category"],
)
def test_channels_the_viewer_may_not_see_are_not_listed(
    tv: Client, showcase: LiveChannel, change: str, live_group: Category
) -> None:
    viewer = customer()
    if change == "disabled":
        LiveChannel.objects.update(enabled=False)
    elif change == "licence":
        LiveChannel.objects.update(license_expires_at=timezone.now() - timedelta(minutes=1))
    elif change == "hidden_group":
        live_group.visible_in_xtream = False
        live_group.save()
    elif change == "no_live":
        viewer = customer(allow_live=False)
    else:
        other = Category.objects.create(
            kind=CategoryKind.LIVE, slug="other", name_en="O", name_ar="O"
        )
        viewer = customer(category_ids=[other.pk])
    assert api(tv, viewer, action="get_live_streams") == b"[]"
    assert api(tv, viewer, action="get_live_categories") == b"[]"


def test_short_epg_starts_with_the_programme_on_air(
    tv: Client, viewer: Subscriber, showcase: LiveChannel, contract: contract_module.Contract
) -> None:
    archive(showcase, timezone.now() - timedelta(hours=3), timezone.now())
    body = api(tv, viewer, action="get_short_epg", stream_id=str(showcase.xc_id), limit="3")
    listings = contract.check("get_short_epg", body)["epg_listings"]
    assert len(listings) == 3
    assert listings[0]["now_playing"] == 1
    assert base64.b64decode(listings[0]["title"]).decode().startswith("Show ")
    assert listings[0]["channel_id"] == EPG_ID
    assert {item["has_archive"] for item in listings} == {0}  # nothing has ended yet


def test_the_data_table_marks_archived_programmes(
    tv: Client, viewer: Subscriber, showcase: LiveChannel, contract: contract_module.Contract
) -> None:
    archive(showcase, timezone.now() - timedelta(minutes=70), timezone.now())
    body = api(tv, viewer, action="get_simple_data_table", stream_id=str(showcase.xc_id))
    listings = contract.check("get_simple_data_table", body)["epg_listings"]
    assert len(listings) == 8
    ended = [item for item in listings if int(item["stop_timestamp"]) <= timezone.now().timestamp()]
    archived = [item for item in ended if item["has_archive"] == 1]
    # Only finished programmes that started within the archive.
    assert archived
    assert len(archived) < len(ended)
    ids = [item["id"] for item in listings]
    # Listing ids are stable across imports.
    epg.import_source(EpgSource.objects.get())
    again = json.loads(
        api(tv, viewer, action="get_simple_data_table", stream_id=str(showcase.xc_id))
    )
    assert [item["id"] for item in again["epg_listings"]] == ids


@pytest.mark.parametrize("stream_id", ["", "abc", "999999"])
def test_unknown_or_hidden_channels_have_no_guide(
    tv: Client, viewer: Subscriber, showcase: LiveChannel, stream_id: str
) -> None:
    for action in ("get_short_epg", "get_simple_data_table"):
        assert api(tv, viewer, action=action, stream_id=stream_id) == b'{"epg_listings":[]}'


def test_arabic_listings_use_the_arabic_title(tv: Client, showcase: LiveChannel) -> None:
    arabic = customer()
    arabic.user.locale = "ar"
    arabic.user.save(update_fields=["locale"])
    body = json.loads(api(tv, arabic, action="get_short_epg", stream_id=str(showcase.xc_id)))
    first = body["epg_listings"][0]
    assert base64.b64decode(first["title"]).decode().startswith("برنامج")
    assert first["lang"] == "ar"


def test_live_catalog_calls_have_a_fixed_query_count(
    tv: Client,
    viewer: Subscriber,
    make_channel: ChannelFactory,
    django_assert_max_num_queries: Callable[..., object],
) -> None:
    from apps.live.xtream import DjangoLiveSource  # noqa: PLC0415

    for index in range(6):
        make_channel(name=f"Extra {index}", epg_channel_id=f"x{index}.example")
    source = DjangoLiveSource()
    with django_assert_max_num_queries(1):  # type: ignore[operator]
        assert len(source.channels(_scope())) == 6
    with django_assert_max_num_queries(1):  # type: ignore[operator]
        assert len(source.categories(_scope())) == 1
    with django_assert_max_num_queries(2):  # type: ignore[operator]
        source.guides(_scope(), now=timezone.now())


def _scope() -> CatalogScope:
    return CatalogScope()


# --- get.php and xmltv.php ---------------------------------------------------------------------


def test_the_playlist_lists_live_channels_first(
    tv: Client, viewer: Subscriber, showcase: LiveChannel, contract: contract_module.Contract
) -> None:
    for output, ext in (("ts", "ts"), ("m3u8", "m3u8"), ("mp4", "ts")):
        response = tv.get("/get.php", viewer.params(type="m3u_plus", output=output))
        assert response.status_code == 200
        playlist, problems = contract.check_m3u(
            response.content,
            live_ext=ext,
            origin=server_info().origin,
            credentials=(viewer.username, viewer.password),
        )
        assert not problems, problems
        live = [entry for entry in playlist.entries if entry.kind == "live"]
        assert [entry.name for entry in live] == ["Showcase 24/7", "No Guide"]
        assert playlist.entries[0].kind == "live"
        assert f'tvg-id="{EPG_ID}"' in response.content.decode()


def test_live_entries_stay_when_vod_is_left_out(
    tv: Client, viewer: Subscriber, showcase: LiveChannel
) -> None:
    from apps.core.services import set_setting  # noqa: PLC0415

    set_setting("features.include_vod_in_m3u", False, actor=None)
    body = tv.get("/get.php", viewer.params(type="m3u_plus")).content.decode()
    assert body.count("/live/") == 2


def test_xmltv_lists_the_viewers_guide(
    tv: Client, viewer: Subscriber, showcase: LiveChannel, contract: contract_module.Contract
) -> None:
    response = tv.get("/xmltv.php", viewer.params(), HTTP_ACCEPT_ENCODING="gzip")
    assert response.status_code == 200
    assert response["Content-Encoding"] == "gzip"
    body = gzip.decompress(response.content)
    assert not contract.check_xmltv(body)
    text = body.decode()
    assert f'<channel id="{EPG_ID}">' in text
    assert text.count("<programme ") == 8  # catch-up of two days covers the whole guide
    assert '<title lang="ar">برنامج 0</title>' in text
    assert "+0000" in text


def test_xmltv_is_empty_without_live_access(tv: Client, showcase: LiveChannel) -> None:
    viewer = customer(allow_live=False)
    body = tv.get("/xmltv.php", viewer.params()).content
    assert b"<channel" not in body
    assert body.startswith(b'<?xml version="1.0" encoding="UTF-8"?>')


# --- Play URLs ---------------------------------------------------------------------------------


def location(response: object) -> tuple[str, str]:
    url = response["Location"]  # type: ignore[index]
    assert url.startswith(f"{MEDIA}/v/")
    token, _, tail = urlsplit(url).path[3:].partition("/")
    return token, tail


@pytest.mark.parametrize(
    ("path", "tail"),
    [
        ("live/{u}/{p}/{id}.ts", "live.ts"),
        ("live/{u}/{p}/{id}.m3u8", "live/index.m3u8"),
        ("{u}/{p}/{id}", "live.ts"),
        ("{u}/{p}/{id}.m3u8", "live/index.m3u8"),
    ],
)
def test_live_play_urls_redirect_to_the_edge(
    *,
    tv: Client,
    viewer: Subscriber,
    showcase: LiveChannel,
    media_keys: tokens.KeySet,
    path: str,
    tail: str,
) -> None:
    url = "/" + path.format(u=viewer.username, p=viewer.password, id=showcase.xc_id)
    response = tv.get(url, REMOTE_ADDR="198.51.100.4")
    assert response.status_code == 302, response.get("X-Reason")
    token, got_tail = location(response)
    assert got_tail == tail
    claims = tokens.verify(media_keys, token, now=int(timezone.now().timestamp()), tail=got_tail)
    assert claims.title == showcase.storage_key
    assert claims.rendition == "live"
    assert claims.exp - timezone.now().timestamp() > 6 * 3600 - 60  # SPEC §7.4: 6 h
    session = PlaybackSession.objects.get(ended_at__isnull=True)
    assert (session.title_kind, session.title_id) == (TitleKind.LIVE, showcase.pk)


def test_the_short_url_uses_the_channels_default(
    tv: Client, viewer: Subscriber, showcase: LiveChannel
) -> None:
    showcase.output = "hls"
    showcase.save()
    response = tv.get(f"/{viewer.username}/{viewer.password}/{showcase.xc_id}")
    assert location(response)[1] == "live/index.m3u8"


def test_a_live_start_wakes_the_packager(
    tv: Client, viewer: Subscriber, showcase: LiveChannel
) -> None:
    pubsub = state_redis().pubsub(ignore_subscribe_messages=True)
    pubsub.subscribe(layout.WAKE_CHANNEL)
    pubsub.get_message(timeout=0.5)
    tv.get(f"/live/{viewer.username}/{viewer.password}/{showcase.xc_id}.ts")
    message = pubsub.get_message(timeout=2)
    pubsub.close()
    assert message is not None
    assert message["data"].decode() == showcase.storage_key


@pytest.mark.parametrize(
    ("change", "status", "reason"),
    [
        ("disabled", 404, "NOT_FOUND"),
        ("unknown", 404, "NOT_FOUND"),
        ("licence", 403, "LICENSE_EXPIRED"),
        ("no_live", 403, "CONTENT_TYPE_NOT_ALLOWED"),
        ("category", 403, "CATEGORY_NOT_ALLOWED"),
        ("quality", 403, "QUALITY_NOT_ALLOWED"),
    ],
)
def test_live_refusals(
    tv: Client, showcase: LiveChannel, change: str, status: int, reason: str
) -> None:
    viewer = customer()
    xc_id = showcase.xc_id
    if change == "disabled":
        LiveChannel.objects.update(enabled=False)
    elif change == "unknown":
        xc_id = 987654
    elif change == "licence":
        LiveChannel.objects.update(license_expires_at=timezone.now() - timedelta(minutes=1))
    elif change == "no_live":
        viewer = customer(allow_live=False)
    elif change == "category":
        other = Category.objects.create(kind=CategoryKind.LIVE, slug="o", name_en="O", name_ar="O")
        viewer = customer(category_ids=[other.pk])
    else:
        LiveChannel.objects.update(probe={"height": 2160})
        viewer = customer(max_quality=1080)
    response = tv.get(f"/live/{viewer.username}/{viewer.password}/{xc_id}.ts")
    assert response.status_code == status
    assert response["X-Reason"] == reason


def test_bad_credentials_get_an_empty_404(
    tv: Client, viewer: Subscriber, showcase: LiveChannel
) -> None:
    response = tv.get(f"/live/{viewer.username}/wrong-password/{showcase.xc_id}.ts")
    assert response.status_code == 404
    assert response.content == b""
    response = tv.get(f"/{viewer.username}/wrong-password/{showcase.xc_id}")
    assert response.status_code == 404


def timeshift_path(
    who: Subscriber, channel: LiveChannel, start: datetime, minutes: int, ext: str = "ts"
) -> str:
    stamp = start.astimezone(UTC).strftime("%Y-%m-%d:%H-%M")
    return f"/timeshift/{who.username}/{who.password}/{minutes}/{stamp}/{channel.xc_id}.{ext}"


def test_timeshift_redirects_to_the_archive_window(
    tv: Client, viewer: Subscriber, showcase: LiveChannel, media_keys: tokens.KeySet
) -> None:
    now = timezone.now()
    archive(showcase, now - timedelta(hours=3), now)
    start = (now - timedelta(minutes=45)).replace(second=0, microsecond=0)
    for ext in ("ts", "m3u8"):
        response = tv.get(timeshift_path(viewer, showcase, start, 30, ext))
        assert response.status_code == 302, response.get("X-Reason")
        token, tail = location(response)
        assert tail == f"archive/{int(start.timestamp())}-1800.{ext}"
        claims = tokens.verify(media_keys, token, now=int(now.timestamp()), tail=tail)
        assert claims.rendition == "archive"
    session = PlaybackSession.objects.get(ended_at__isnull=True)
    assert session.title_kind == TitleKind.CATCHUP


@pytest.mark.parametrize(
    "case", ["no_catchup", "no_archive", "too_old", "future", "too_long", "bad_start"]
)
def test_timeshift_outside_the_archive_is_404(
    tv: Client, viewer: Subscriber, showcase: LiveChannel, case: str
) -> None:
    now = timezone.now()
    archive(showcase, now - timedelta(hours=1), now)
    start, minutes = now - timedelta(minutes=30), 20
    if case == "no_catchup":
        LiveChannel.objects.update(catchup_days=0)
    elif case == "no_archive":
        start = now - timedelta(hours=5)
    elif case == "too_old":
        start = now - timedelta(days=3)
    elif case == "future":
        start = now + timedelta(minutes=5)
    elif case == "too_long":
        minutes = 2000
    path = timeshift_path(viewer, showcase, start, minutes)
    if case == "bad_start":
        path = path.replace(":", "T", 1)
    response = tv.get(path)
    assert response.status_code == 404
