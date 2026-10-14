"""start_playback picks an edge (ADR-0022): today's single edge by default, a standalone
edge for published renditions, the control host for live TV, canaries by customer."""

import time
from datetime import UTC, datetime
from typing import Any

import pytest

from apps.accounts.models import User
from apps.conftest import CustomerFactory
from apps.core.ids import uuid7
from apps.edges import services
from apps.edges.models import Audience, Edge, EdgeKind, EdgeStorage
from apps.playback import services as playback
from apps.playback import tokens
from apps.playback.models import TitleKind
from apps.playback.services import PlayableRendition, PlayableTitle, RenditionKind
from apps.playback.tests.conftest import MEDIA_BASE_URL

pytestmark = pytest.mark.django_db

T0 = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
ASSET = uuid7().hex


def title(*, published: str = "", kind: TitleKind = TitleKind.MOVIE) -> PlayableTitle:
    rendition = PlayableRendition(ASSET, RenditionKind.COMPAT, 1080, published_key=published)
    return PlayableTitle(kind=kind, id=uuid7(), renditions=(rendition,), runtime_s=600, name="X")


def start(user: User, playable: PlayableTitle) -> playback.PlaybackGrant:
    device = user.devices.order_by("created_at").first()
    assert device is not None
    return playback.start_playback(user, device, playable, client_ip="203.0.113.7", now=T0)


def claims_of(url: str) -> tokens.Claims:
    token = url.split("/v/", 1)[1].split("/", 1)[0]
    return tokens.parse(token)[0]


def healthy_edge(media_keys: tokens.KeySet, **fields: Any) -> Edge:
    values: dict[str, Any] = {
        "edge_id": "media2",
        "name": "Media 2",
        "kind": EdgeKind.STANDALONE,
        "storage": EdgeStorage.ORIGIN,
        "base_url": "https://media2.example.test",
        "weight": 1000,
    }
    values.update(fields)
    edge = Edge.objects.create(**values)
    services.record_report(edge, {"kids": [media_keys.current.kid]}, now=time.time())
    services.reset_memo()
    return edge


@pytest.fixture
def customer(make_customer: CustomerFactory) -> User:
    return make_customer(devices=1)


def test_with_only_the_local_edge_urls_are_unchanged(customer: User) -> None:
    grant = start(customer, title(published="asset-g1"))
    assert grant.url.startswith(f"{MEDIA_BASE_URL}/v/")
    assert grant.edge == "local"
    assert claims_of(grant.url).title == ASSET


def test_a_published_rendition_can_go_to_a_standalone_edge(
    customer: User, media_keys: tokens.KeySet
) -> None:
    Edge.objects.filter(edge_id="local").update(weight=0)
    healthy_edge(media_keys)
    grant = start(customer, title(published=f"{ASSET}-g2"))
    assert grant.url.startswith("https://media2.example.test/v/")
    assert grant.edge == "media2"
    # The S3 origin's prefix is the token's title there.
    assert claims_of(grant.url).title == f"{ASSET}-g2"


def test_an_unpublished_rendition_stays_on_the_control_host(
    customer: User, media_keys: tokens.KeySet
) -> None:
    Edge.objects.filter(edge_id="local").update(weight=0)
    healthy_edge(media_keys)
    grant = start(customer, title())
    assert (grant.edge, claims_of(grant.url).title) == ("local", ASSET)


def test_live_tv_stays_on_the_control_host(customer: User, media_keys: tokens.KeySet) -> None:
    Edge.objects.filter(edge_id="local").update(weight=0)
    healthy_edge(media_keys, storage=EdgeStorage.DISK)
    rendition = PlayableRendition(
        uuid7().hex, RenditionKind.LIVE, 720, container="ts", path="live.ts"
    )
    channel = PlayableTitle(
        kind=TitleKind.LIVE, id=uuid7(), renditions=(rendition,), runtime_s=0, name="Ch"
    )
    from apps.playback import entitlements  # noqa: PLC0415

    entitlement = entitlements.get(customer.pk)
    assert entitlement is not None
    if not entitlement["allow_live"]:
        pytest.skip("the test customer's plan has no live TV")
    assert start(customer, channel).edge == "local"


def test_a_canary_edge_serves_its_customers_only(
    customer: User, make_customer: CustomerFactory, media_keys: tokens.KeySet
) -> None:
    Edge.objects.filter(edge_id="local").update(weight=0)
    edge = healthy_edge(media_keys, storage=EdgeStorage.DISK, audience=Audience.CANARY)
    edge.canary_users.add(customer)
    services.reset_memo()
    other = make_customer(devices=1)
    assert start(customer, title()).edge == "media2"
    assert start(other, title()).edge == "local"


def test_an_edge_missing_the_signing_key_gets_nothing(
    customer: User, media_keys: tokens.KeySet
) -> None:
    Edge.objects.filter(edge_id="local").update(weight=0)
    edge = healthy_edge(media_keys, storage=EdgeStorage.DISK)
    services.record_report(edge, {"kids": ["zz"]}, now=time.time())
    services.reset_memo()
    assert start(customer, title()).edge == "local"
