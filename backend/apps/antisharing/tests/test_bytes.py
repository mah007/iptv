"""G-17's byte flagging (ADR-0024): sessions served far more bytes than their title
explains become `ExcessBytes`, a scoring signal and a flag for the admins, never a lock.
The edges' reports go through `apps.playback.bytes` (Redis), the sweeper and the check."""

import json
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, cast

import pytest
from django.conf import settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Device, User, UserStatus
from apps.antisharing import bytes as excess
from apps.antisharing import scoring, services
from apps.antisharing.models import ExcessBytes, FlagStatus, RiskFlag
from apps.catalog.models import Episode, FileState, MediaFile, Movie, Season, Series
from apps.conftest import CustomerFactory
from apps.core.services import set_setting
from apps.core.stores import state_redis
from apps.edges import services as edges
from apps.edges.models import Edge, EdgeKind
from apps.library.models import Library
from apps.media.models import Rendition, RenditionKind, RenditionStatus
from apps.ops import retention
from apps.playback import bytes as served
from apps.playback import concurrency
from apps.playback import services as playback
from apps.playback.models import EndReason, PlaybackSession, TitleKind

pytestmark = pytest.mark.django_db
ADMIN = {"host": settings.ADMIN_HOST}
MB = 1024 * 1024
#: 90 minutes at 4 Mbit/s: 2.7 GB explained.
RUNTIME_S = 5400
COMPAT_BPS = 4_000_000
EXPLAINED = RUNTIME_S * COMPAT_BPS // 8


@pytest.fixture(autouse=True)
def _clean_state() -> None:
    state_redis().flushdb()


@pytest.fixture
def customer(make_customer: CustomerFactory) -> User:
    return make_customer(name="Huda", devices=1, max_devices=3)


@pytest.fixture
def library(make_library: Callable[..., Library]) -> Library:
    return make_library()


def media_file(library: Library, **owner: Any) -> MediaFile:
    return MediaFile.objects.create(
        library=library,
        storage_key=f"{uuid.uuid4().hex}.mkv",
        state=FileState.MATCHED,
        direct_play=False,
        height=1080,
        duration_ms=RUNTIME_S * 1000,
        **owner,
    )


def rendition(file: MediaFile, kind: str, bitrate: int | None, **extra: Any) -> Rendition:
    return Rendition.objects.create(
        media_file=file,
        kind=kind,
        storage_key=f"{file.pk.hex}-{kind}-{extra.get('name', '')}",
        status=RenditionStatus.READY,
        height=extra.pop("height", 1080),
        bitrate=bitrate,
        duration_s=float(RUNTIME_S),
        **extra,
    )


@pytest.fixture
def movie(library: Library) -> Movie:
    film = Movie.objects.create(title="Lantern", original_title="Lantern", runtime_min=95)
    file = media_file(library, movie=film)
    rendition(file, RenditionKind.COMPAT_MP4, COMPAT_BPS)
    rendition(file, RenditionKind.HLS_VARIANT, 6_000_000, name="v1080")
    rendition(file, RenditionKind.HLS_VARIANT, 1_500_000, name="v480", height=480)
    rendition(file, RenditionKind.SOURCE, 20_000_000)
    return film


def session(
    user: User,
    title: Movie | Episode,
    sent: int,
    *,
    kind: str = TitleKind.MOVIE,
    rendition_kind: str = "compat",
    started: datetime | None = None,
    ended: datetime | None = None,
) -> PlaybackSession:
    begun = started or timezone.now() - timedelta(hours=1)
    device = Device.objects.filter(user=user).first()
    return PlaybackSession.objects.create(
        session_key=uuid.uuid4().hex,
        user=user,
        device=device,
        title_kind=kind,
        title_id=title.pk,
        title_name=str(getattr(title, "title", "Episode")),
        rendition=rendition_kind,
        ip="77.31.4.5",
        started_at=begun,
        last_heartbeat_at=begun,
        ended_at=ended,
        end_reason=EndReason.IDLE if ended else "",
        bytes_sent=sent,
    )


# --- What a title explains ------------------------------------------------------------------


def test_a_movie_explains_its_runtime_times_the_kind_it_played(movie: Movie) -> None:
    title = excess.explained(TitleKind.MOVIE, movie.pk)
    assert title is not None
    assert title.runtime_s == RUNTIME_S  # the renditions' duration wins over runtime_min
    assert title.expected_bytes("compat") == EXPLAINED
    assert title.expected_bytes("hls") == RUNTIME_S * 6_000_000 // 8  # the top rung
    assert title.expected_bytes("source") == RUNTIME_S * 20_000_000 // 8
    # No UHD rendition: the most generous of what there is.
    assert title.expected_bytes("uhd") == RUNTIME_S * 20_000_000 // 8
    assert excess.explained(TitleKind.MOVIE, uuid.uuid4()) is None
    assert excess.explained(TitleKind.LIVE, movie.pk) is None


def test_an_episode_falls_back_to_the_series_runtime(library: Library) -> None:
    show = Series.objects.create(title="Keepers", original_title="Keepers", episode_run_time=45)
    season = Season.objects.create(series=show, number=1)
    episode = Episode.objects.create(season=season, number=1)
    file = media_file(library)
    file.episodes.add(episode)
    Rendition.objects.create(
        media_file=file,
        kind=RenditionKind.COMPAT_MP4,
        storage_key=file.pk.hex,
        status=RenditionStatus.READY,
        height=720,
        bitrate=2_000_000,
    )
    title = excess.explained(TitleKind.EPISODE, episode.pk)
    assert title is not None
    assert title.runtime_s == 45 * 60
    assert title.expected_bytes("compat") == 45 * 60 * 2_000_000 // 8
    # Without a bitrate there is nothing to compare with.
    bare = Movie.objects.create(title="Bare", original_title="Bare", runtime_min=90)
    title = excess.explained(TitleKind.MOVIE, bare.pk)
    assert title is not None
    assert title.expected_bytes("compat") is None


# --- The check, the signal and the flag -------------------------------------------------------


def test_a_session_far_over_its_title_is_recorded_and_flags(
    customer: User, movie: Movie, monkeypatch: pytest.MonkeyPatch
) -> None:
    notices: list[dict[str, Any]] = []
    monkeypatch.setattr(services, "_notify_admins", lambda flag, opened: notices.append({}))
    over = session(customer, movie, 5 * EXPLAINED)
    session(customer, movie, 2 * EXPLAINED)  # seeking and buffering: under 4x
    session(customer, movie, 200 * MB, rendition_kind="hls")  # under the minimum
    now = timezone.now()
    with_sessions = services.score_recent(now)
    row = ExcessBytes.objects.get()
    assert row.session == over
    assert (row.bytes_sent, row.expected_bytes, row.ratio) == (5 * EXPLAINED, EXPLAINED, 5.0)
    assert (row.title_name, row.rendition, row.network) == ("Lantern", "compat", "77.31.4.0/24")
    assert row.device is not None
    assert with_sessions == {"assessed": 1, "flagged": 1}
    flag = RiskFlag.objects.get(user=customer, status=FlagStatus.OPEN)
    signal = next(s for s in flag.signals["signals"] if s["name"] == "excess_bytes_24h")
    assert (signal["value"], signal["limit"], signal["points"]) == (1, 0, 4)
    customer.refresh_from_db()
    assert customer.status == UserStatus.ACTIVE  # a flag never locks


def test_a_growing_session_keeps_its_first_detection(customer: User, movie: Movie) -> None:
    over = session(customer, movie, 5 * EXPLAINED)
    first = timezone.now()
    assert excess.check(first) == 1
    PlaybackSession.objects.filter(pk=over.pk).update(bytes_sent=8 * EXPLAINED)
    assert excess.check(first + timedelta(minutes=5)) == 1
    row = ExcessBytes.objects.get()
    assert (row.ratio, row.detected_at) == (8.0, first)
    # A day after its detection it no longer counts towards the score.
    assert excess.recent_count(customer.pk, first + timedelta(hours=25)) == 0


def test_the_limits_are_settings(customer: User, movie: Movie) -> None:
    session(customer, movie, 2 * EXPLAINED)
    assert excess.check() == 0
    set_setting("antisharing.bytes_ratio", value=1.5, actor=None)
    assert excess.check() == 1
    ExcessBytes.objects.all().delete()
    set_setting("antisharing.bytes_min_mb", value=100_000, actor=None)
    assert excess.check() == 0


def test_live_old_and_switched_off_sessions_are_left_alone(customer: User, movie: Movie) -> None:
    session(customer, movie, 50 * EXPLAINED, kind=TitleKind.LIVE)
    session(
        customer,
        movie,
        50 * EXPLAINED,
        started=timezone.now() - timedelta(days=3),
        ended=timezone.now() - timedelta(days=2),
    )
    assert excess.check() == 0
    session(customer, movie, 50 * EXPLAINED)
    set_setting("antisharing.enabled", value=False, actor=None)
    assert services.score_recent() == {"assessed": 0, "flagged": 0}
    assert not ExcessBytes.objects.exists()


def test_scoring_counts_each_session() -> None:
    now = timezone.now()
    assessment = scoring.assess([], now, scoring.Limits(), excess_sessions=2)
    signal = next(s for s in assessment.signals if s.name == "excess_bytes_24h")
    assert (signal.value, signal.points, assessment.score, assessment.flagged) == (2, 8, 8, True)
    assert not scoring.assess([], now, scoring.Limits()).flagged


def test_the_admin_sees_the_sessions_on_the_security_tab(
    customer: User, movie: Movie, owner_client: APIClient
) -> None:
    session(customer, movie, 6 * EXPLAINED)
    excess.check()
    body = owner_client.get(f"/api/v1/admin/customers/{customer.pk}/risk", headers=ADMIN).json()
    (row,) = body["excess_bytes"]
    assert row["title_name"] == "Lantern"
    assert row["ratio"] == 6.0
    assert row["bytes_sent"] == 6 * EXPLAINED
    assert row["expected_bytes"] == EXPLAINED
    assert row["device_name"] == "Device 1"
    assert row["ended_at"] is None
    names = {signal["name"]: signal for signal in body["assessment"]["signals"]}
    assert names["excess_bytes_24h"]["value"] == 1


def test_excess_rows_follow_the_sightings_retention(customer: User, movie: Movie) -> None:
    session(customer, movie, 6 * EXPLAINED)
    excess.check()
    later = timezone.now() + timedelta(days=91)
    assert retention.antisharing_sightings(later)["excess_bytes_deleted"] == 1


# --- From the edges to the rows ---------------------------------------------------------------


def test_reports_reach_live_records_and_late_bytes_reach_closed_rows(
    customer: User, movie: Movie
) -> None:
    live = session(customer, movie, 0)
    state_redis().hset(concurrency.sess_key(live.session_key), mapping={"bytes": 10})
    closed = session(customer, movie, 1000, ended=timezone.now())
    unknown = uuid.uuid4().hex
    added = served.add({live.session_key: 500, closed.session_key: 700, unknown: 9})
    assert added == (1, 2)
    raw = cast("bytes", state_redis().hget(concurrency.sess_key(live.session_key), "bytes"))
    assert raw == b"510"
    playback.sweep()
    closed.refresh_from_db()
    assert closed.bytes_sent == 1700
    assert served.drain_late() == {}


def test_reports_are_cleaned() -> None:
    session_id = "0" * 32
    assert served.clean({session_id: 5, "short": 5, "f" * 32: 0, "e" * 32: True}) == {session_id: 5}
    assert served.clean({"a" * 32: 1 << 60}) == {}
    assert served.clean(["not", "a", "dict"]) == {}
    many = {f"{n:032x}": 1 for n in range(served.MAX_SESSIONS + 10)}
    assert len(served.clean(many)) == served.MAX_SESSIONS


def test_a_standalone_edge_reports_bytes_in_its_poll(
    customer: User, movie: Movie, client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = edges.new_secret()
    edge = Edge.objects.create(
        edge_id="edge-1",
        name="Edge 1",
        kind=EdgeKind.STANDALONE,
        enabled=True,
        secret_hash=edges.hash_secret(secret),
    )
    monkeypatch.setattr(edges, "poll_answer", lambda _edge: {"keys": {}})
    live = session(customer, movie, 0)
    state_redis().hset(concurrency.sess_key(live.session_key), mapping={"bytes": 0})
    response = client.post(
        "/edge/v1/poll",
        data=json.dumps({"kids": ["k1"], "session_bytes": {live.session_key: 4096}}),
        content_type="application/json",
        headers={
            "host": settings.EDGES_HOST,
            "Authorization": f"Bearer {secret}",
            "X-Edge-Id": edge.edge_id,
        },
    )
    assert response.status_code == 200
    raw = cast("bytes", state_redis().hget(concurrency.sess_key(live.session_key), "bytes"))
    assert raw == b"4096"
