"""The client's network is part of the session identity (threat model G-13, ADR-0017).

Two households on one login and one title used to share a session, so one stream
slot and one long-lived token. Now each network gets its own session, and the
one-stream-per-device rule makes the newer one replace the older.
"""

from datetime import timedelta

import pytest

from apps.accounts.models import User
from apps.conftest import CustomerFactory
from apps.core.ids import uuid7
from apps.playback import concurrency, services
from apps.playback.models import EndReason, PlaybackSession, TitleKind
from apps.playback.services import PlayableRendition, PlayableTitle, RenditionKind
from apps.playback.tests.test_services import T0, device_of, start

pytestmark = pytest.mark.django_db


@pytest.fixture
def title() -> PlayableTitle:
    return PlayableTitle(
        kind=TitleKind.LIVE,
        id=uuid7(),
        renditions=(
            PlayableRendition(
                str(uuid7()), RenditionKind.LIVE, 720, container="ts", path="live.ts"
            ),
        ),
        name="Test Pattern",
    )


@pytest.fixture
def one_stream(make_customer: CustomerFactory) -> User:
    return make_customer(devices=1, max_streams=1)


def test_client_network_is_the_24_or_the_64() -> None:
    assert services.client_network("203.0.113.7") == services.client_network("203.0.113.200")
    assert services.client_network("203.0.113.7") != services.client_network("203.0.114.7")
    assert services.client_network("2001:db8:1:2::7") == services.client_network("2001:db8:1:2::9")
    assert services.client_network(None) == services.client_network("not an address") == ""


def test_two_networks_get_two_session_keys() -> None:
    first = services.session_key("u", "d", "live:x", services.client_network("203.0.113.7"))
    second = services.session_key("u", "d", "live:x", services.client_network("198.51.100.7"))
    assert first != second


def test_an_unknown_address_keeps_the_spec_key() -> None:
    assert services.session_key("u", "d", "movie:x", "") == services.session_key(
        "u", "d", "movie:x"
    )


def test_a_second_network_replaces_the_first(one_stream: User, title: PlayableTitle) -> None:
    first = start(one_stream, title, client_ip="203.0.113.7")
    second = start(one_stream, title, client_ip="198.51.100.7", now=T0 + timedelta(seconds=5))
    assert second.session_key != first.session_key
    assert not second.reused
    old = PlaybackSession.objects.get(pk=first.session.pk)
    assert old.ended_at is not None
    assert old.end_reason == EndReason.STOPPED  # replaced on the same device
    assert (
        concurrency.active_streams(one_stream.pk, now=(T0 + timedelta(seconds=6)).timestamp()) == 1
    )


def test_the_same_network_reuses_the_session(one_stream: User, title: PlayableTitle) -> None:
    first = start(one_stream, title, client_ip="203.0.113.7")
    again = start(one_stream, title, client_ip="203.0.113.99", now=T0 + timedelta(seconds=5))
    assert again.session_key == first.session_key
    assert again.reused
    assert again.session.pk == first.session.pk


def test_unknown_addresses_share_the_spec_session(one_stream: User, title: PlayableTitle) -> None:
    first = start(one_stream, title, client_ip=None)
    again = start(one_stream, title, client_ip=None, now=T0 + timedelta(seconds=5))
    device = device_of(one_stream)
    assert first.session_key == services.session_key(one_stream.pk, device.pk, title.ref)
    assert again.reused
