"""start_playback: the SPEC §7.4 checks in order, slots, sessions and signed URLs."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from django.utils import timezone

from apps.accounts import services as accounts
from apps.accounts.models import (
    AccessRule,
    AccessRuleType,
    ConcurrencyPolicy,
    CustomerAccess,
    Device,
    MaxQuality,
    User,
    UserStatus,
)
from apps.audit.models import AuditLog
from apps.catalog.models import Category
from apps.conftest import CustomerFactory
from apps.core.errors import ErrorCode, ProblemError
from apps.core.ids import uuid7
from apps.core.services import set_setting
from apps.core.stores import state_redis
from apps.playback import concurrency, entitlements, records, services, tokens
from apps.playback.concurrency import KickReason
from apps.playback.models import EndReason, PlaybackSession, TitleKind
from apps.playback.services import (
    Denial,
    PlayableRendition,
    PlayableTitle,
    PlaybackDenied,
    PlaybackGrant,
    Prefer,
    RenditionKind,
)
from apps.playback.tests.conftest import MEDIA_BASE_URL

pytestmark = pytest.mark.django_db

T0 = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
type TitleFactory = Callable[..., PlayableTitle]


def device_of(user: User) -> Device:
    return user.devices.order_by("created_at").first() or pytest.fail("no device")


@pytest.fixture
def customer(make_customer: CustomerFactory) -> User:
    return make_customer(devices=2)


@pytest.fixture
def make_title() -> TitleFactory:
    def factory(**overrides: Any) -> PlayableTitle:
        values: dict[str, Any] = {
            "kind": TitleKind.MOVIE,
            "id": uuid7(),
            "renditions": (PlayableRendition(uuid7().hex, RenditionKind.COMPAT, 1080),),
            "runtime_s": 6000,
            "name": "The Matrix (1999)",
        }
        values.update(overrides)
        return PlayableTitle(**values)

    return factory


def start(
    user: User, title: PlayableTitle, *, device: Device | None = None, **kwargs: Any
) -> PlaybackGrant:
    kwargs.setdefault("client_ip", "203.0.113.7")
    kwargs.setdefault("now", T0)
    return services.start_playback(user, device or device_of(user), title, **kwargs)


def denial(user: User, title: PlayableTitle, **kwargs: Any) -> Denial:
    with pytest.raises(PlaybackDenied) as excinfo:
        start(user, title, **kwargs)
    return excinfo.value.denial


def token_of(grant: PlaybackGrant) -> tuple[str, str]:
    prefix = f"{MEDIA_BASE_URL}/v/"
    assert grant.url.startswith(prefix)
    token, _slash, tail = grant.url[len(prefix) :].partition("/")
    return token, tail


# --- A successful start ---------------------------------------------------------------------


def test_start_returns_a_signed_edge_url(
    customer: User, make_title: TitleFactory, media_keys: tokens.KeySet
) -> None:
    title = make_title()
    grant = start(customer, title)
    token, tail = token_of(grant)
    assert tail == "compat.mp4"
    claims = tokens.verify(
        media_keys, token, now=int(T0.timestamp()), client_ip="198.51.100.1", tail=tail
    )
    network = services.client_network("203.0.113.7")
    key = services.session_key(customer.pk, device_of(customer).pk, title.ref, network)
    assert claims.session == key == grant.session_key
    assert claims.title == title.renditions[0].storage_key
    assert claims.rendition == "compat"
    assert claims.exp == grant.expires_at == int(T0.timestamp()) + 6000 + 7200
    assert claims.net is None  # IP binding is off by default
    assert not grant.reused


def test_start_opens_the_session_row_and_record(customer: User, make_title: TitleFactory) -> None:
    title = make_title()
    grant = start(customer, title, user_agent="IPTVSmartersPlayer", country="sa")
    row = PlaybackSession.objects.get()
    assert row == grant.session
    assert (row.user, row.device, row.title_kind, row.title_id) == (
        customer,
        device_of(customer),
        TitleKind.MOVIE,
        title.id,
    )
    assert (row.rendition, row.ip, row.country, row.user_agent) == (
        "compat",
        "203.0.113.7",
        "SA",
        "IPTVSmartersPlayer",
    )
    assert (row.started_at, row.ended_at, row.end_reason) == (T0, None, "")
    assert row.title_name == "The Matrix (1999)"
    record = records.read_record(grant.session_key)
    assert record is not None
    assert (record.row, record.delivery, record.idle) == (str(row.pk), "progressive", 6600)
    assert record.title == title.ref


def test_a_reconnect_within_the_window_reuses_the_session(
    customer: User, make_title: TitleFactory
) -> None:
    title = make_title()
    first = start(customer, title)
    again = start(customer, title, now=T0 + timedelta(seconds=60))
    assert again.reused
    assert again.session == first.session
    assert again.session_key == first.session_key
    assert PlaybackSession.objects.count() == 1
    record = records.read_record(first.session_key)
    assert record is not None
    assert record.started == T0.timestamp()


def test_a_start_after_the_window_is_a_new_playback(
    customer: User, make_title: TitleFactory
) -> None:
    title = make_title()
    first = start(customer, title)
    later = start(customer, title, now=T0 + timedelta(seconds=300))
    assert not later.reused
    assert later.session != first.session
    first.session.refresh_from_db()
    assert first.session.end_reason == EndReason.IDLE
    assert first.session.ended_at == T0


def test_hls_preference_and_quality_ceiling(
    make_customer: CustomerFactory, make_title: TitleFactory
) -> None:
    user = make_customer(devices=1, max_quality=MaxQuality.HD)
    asset = uuid7().hex
    title = make_title(
        renditions=(
            PlayableRendition(asset, RenditionKind.COMPAT, 1080),
            PlayableRendition(asset, RenditionKind.COMPAT, 720),
            PlayableRendition(asset, RenditionKind.HLS, 720),
        )
    )
    assert token_of(start(user, title, prefer=Prefer.HLS))[1] == "hls/master.m3u8"
    grant = start(user, title, prefer=Prefer.MP4)
    assert (grant.rendition.kind, grant.rendition.height) == (RenditionKind.COMPAT, 720)


def test_ip_binding_setting_binds_the_token(
    customer: User, make_title: TitleFactory, media_keys: tokens.KeySet
) -> None:
    set_setting("playback.ip_binding", value=True, actor=None)
    token, tail = token_of(start(customer, make_title()))
    claims = tokens.verify(
        media_keys, token, now=int(T0.timestamp()), client_ip="203.0.113.200", tail=tail
    )
    assert claims.net == "cb0071"


def test_renditions_never_take_paths() -> None:
    with pytest.raises(ValueError, match="not a path"):
        PlayableRendition("movies/The Matrix", RenditionKind.COMPAT, 1080)
    with pytest.raises(ValueError, match="extension"):
        PlayableRendition("asset", RenditionKind.SOURCE, 1080, container="m k v")


def test_missing_keys_fail_before_a_slot_is_taken(
    customer: User, make_title: TitleFactory, settings: Any, tmp_path: Any
) -> None:
    settings.MEDIA_TOKEN_KEYS_FILE = str(tmp_path / "absent.json")
    tokens.reset_keyring()
    with pytest.raises(tokens.KeySetError):
        start(customer, make_title())
    assert state_redis().exists(concurrency.conc_key(customer.pk)) == 0
    assert not PlaybackSession.objects.exists()


# --- The checks, in order --------------------------------------------------------------------


def test_suspended_account(customer: User, make_title: TitleFactory) -> None:
    User.objects.filter(pk=customer.pk).update(status=UserStatus.SUSPENDED)
    customer.refresh_from_db()
    device = device_of(customer)
    Device.objects.filter(pk=device.pk).update(blocked=True)  # check 1 comes first
    assert denial(customer, make_title()) is Denial.ACCOUNT_SUSPENDED


def test_expired_access(make_customer: CustomerFactory, make_title: TitleFactory) -> None:
    user = make_customer(devices=1)
    CustomerAccess.objects.filter(user=user).update(
        expires_at=timezone.now() - timedelta(minutes=1)
    )
    assert denial(user, make_title(), now=timezone.now()) is Denial.SUBSCRIPTION_EXPIRED


def test_access_ending_before_now(make_customer: CustomerFactory, make_title: TitleFactory) -> None:
    user = make_customer(devices=1, expires_at=timezone.now() + timedelta(hours=1))
    later = timezone.now() + timedelta(hours=2)
    assert denial(user, make_title(), now=later) is Denial.SUBSCRIPTION_EXPIRED


def test_staff_without_access_profile(staff_user: User, make_title: TitleFactory) -> None:
    device = Device.objects.create(user=staff_user, name="Test box")
    assert denial(staff_user, make_title(), device=device) is Denial.SUBSCRIPTION_EXPIRED


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"blocked": True}, Denial.DEVICE_BLOCKED),
        ({"revoked_at": T0}, Denial.DEVICE_BLOCKED),
        ({"approved": False}, Denial.DEVICE_NOT_APPROVED),
    ],
)
def test_device_checks(
    customer: User, make_title: TitleFactory, changes: dict[str, Any], expected: Denial
) -> None:
    device = device_of(customer)
    Device.objects.filter(pk=device.pk).update(**changes)
    device.refresh_from_db()
    assert denial(customer, make_title(), device=device) is expected


def test_device_of_another_user_is_a_programming_error(
    make_customer: CustomerFactory, make_title: TitleFactory
) -> None:
    one, other = make_customer(devices=1), make_customer(devices=1)
    with pytest.raises(ValueError, match="another user"):
        start(one, make_title(), device=device_of(other))


@pytest.mark.parametrize(
    ("rule", "client_ip", "country", "expected"),
    [
        ((AccessRuleType.IP_DENY, "203.0.113.7", True), "203.0.113.7", None, Denial.IP_BLOCKED),
        ((AccessRuleType.CIDR_DENY, "203.0.113.0/24", False), "203.0.113.7", None,
         Denial.IP_BLOCKED),
        ((AccessRuleType.CIDR_DENY, "203.0.113.0/24", False), "::ffff:203.0.113.9", None,
         Denial.IP_BLOCKED),
        ((AccessRuleType.COUNTRY_DENY, "EG", True), "203.0.113.7", "eg", Denial.GEO_BLOCKED),
        ((AccessRuleType.COUNTRY_ALLOW, "SA", False), "203.0.113.7", None, Denial.GEO_BLOCKED),
        ((AccessRuleType.COUNTRY_ALLOW, "SA", True), "203.0.113.7", "AE", Denial.GEO_BLOCKED),
    ],
)  # fmt: skip
def test_ip_and_country_rules(
    *,
    customer: User,
    make_title: TitleFactory,
    rule: tuple[str, str, bool],
    client_ip: str,
    country: str | None,
    expected: Denial,
) -> None:
    rule_type, value, own = rule
    AccessRule.objects.create(user=customer if own else None, type=rule_type, value=value)
    entitlements.refresh(customer.pk)
    assert denial(customer, make_title(), client_ip=client_ip, country=country) is expected


def test_allowed_ip_overrides_denies(customer: User, make_title: TitleFactory) -> None:
    AccessRule.objects.create(type=AccessRuleType.CIDR_DENY, value="203.0.113.0/24")
    AccessRule.objects.create(type=AccessRuleType.COUNTRY_ALLOW, value="SA")
    AccessRule.objects.create(type=AccessRuleType.IP_ALLOW, value="203.0.113.7")
    assert start(customer, make_title(), country="EG").url


def test_expired_rules_do_not_apply(customer: User, make_title: TitleFactory) -> None:
    AccessRule.objects.create(
        type=AccessRuleType.IP_DENY, value="203.0.113.7", expires_at=T0 - timedelta(seconds=1)
    )
    assert start(customer, make_title()).url


def test_content_type(make_customer: CustomerFactory, make_title: TitleFactory) -> None:
    user = make_customer(devices=1, allow_series=False)
    assert start(user, make_title()).url
    episode = make_title(kind=TitleKind.EPISODE)
    assert denial(user, episode) is Denial.CONTENT_TYPE_NOT_ALLOWED


def test_categories(
    make_customer: CustomerFactory, make_title: TitleFactory, category: Category
) -> None:
    user = make_customer(devices=1)
    accounts.update_access(user, {}, category_ids=[category.pk], actor=None)
    assert denial(user, make_title()) is Denial.CATEGORY_NOT_ALLOWED
    other = uuid7()
    assert denial(user, make_title(category_ids=frozenset({other}))) is (
        Denial.CATEGORY_NOT_ALLOWED
    )
    assert start(user, make_title(category_ids=frozenset({category.pk, other}))).url


def test_nothing_playable_yet(customer: User, make_title: TitleFactory) -> None:
    assert denial(customer, make_title(renditions=())) is Denial.TITLE_PREPARING


def test_quality_ceiling(make_customer: CustomerFactory, make_title: TitleFactory) -> None:
    user = make_customer(devices=1, max_quality=MaxQuality.SD)
    assert denial(user, make_title()) is Denial.QUALITY_NOT_ALLOWED


def test_licence(customer: User, make_title: TitleFactory) -> None:
    expired = make_title(license_expires_at=T0)
    assert denial(customer, expired) is Denial.LICENSE_EXPIRED
    assert start(customer, make_title(license_expires_at=T0 + timedelta(days=1))).url


def test_denials_carry_problem_codes() -> None:
    limit = PlaybackDenied(Denial.CONCURRENCY_LIMIT)
    assert (limit.problem_code, limit.status_code) == (ErrorCode.CONCURRENCY_LIMIT, 409)
    for code in Denial:
        problem = PlaybackDenied(code)
        expected = ErrorCode.__members__.get(code.value, ErrorCode.PERMISSION_DENIED)
        assert problem.problem_code is expected
        assert problem.denial is code


# --- Concurrency --------------------------------------------------------------------------------


def test_second_stream_is_refused_at_the_limit(customer: User, make_title: TitleFactory) -> None:
    phone, tv = customer.devices.order_by("created_at")
    start(customer, make_title(), device=phone)
    assert denial(customer, make_title(), device=tv) is Denial.CONCURRENCY_LIMIT
    assert PlaybackSession.objects.count() == 1


def test_kick_oldest_stops_the_older_stream(
    make_customer: CustomerFactory, make_title: TitleFactory
) -> None:
    user = make_customer(devices=2, concurrency_policy=ConcurrencyPolicy.KICK_OLDEST)
    phone, tv = user.devices.order_by("created_at")
    first = start(user, make_title(), device=phone)
    start(user, make_title(), device=tv, now=T0 + timedelta(seconds=5))
    first.session.refresh_from_db()
    assert first.session.end_reason == EndReason.LIMIT
    assert state_redis().get(concurrency.kick_key(first.session_key)) == b"stream_limit"
    assert PlaybackSession.objects.filter(ended_at__isnull=True).count() == 1


def test_another_title_on_the_same_device_replaces_the_stream(
    customer: User, make_title: TitleFactory
) -> None:
    first = start(customer, make_title())
    second = start(customer, make_title(), now=T0 + timedelta(seconds=20))
    first.session.refresh_from_db()
    assert first.session.end_reason == EndReason.STOPPED
    assert second.session.ended_at is None


# --- Stopping and sweeping ------------------------------------------------------------------


def test_kill_session_stops_and_audits(
    customer: User, make_title: TitleFactory, owner: User
) -> None:
    grant = start(customer, make_title())
    killed = services.kill_session(grant.session, actor=owner, ip="192.0.2.1")
    assert killed.end_reason == EndReason.KICKED
    assert killed.ended_at is not None
    assert state_redis().get(concurrency.kick_key(grant.session_key)) == b"kicked"
    assert records.read_record(grant.session_key) is None
    entry = AuditLog.objects.get(action="session.kill")
    assert (entry.actor, entry.target_id, entry.actor_ip) == (owner, str(killed.pk), "192.0.2.1")
    with pytest.raises(ProblemError) as excinfo:
        services.kill_session(grant.session, actor=owner)
    assert excinfo.value.problem_code is ErrorCode.CONFLICT


def test_stop_user_sessions(customer: User, make_title: TitleFactory) -> None:
    grant = start(customer, make_title())
    assert services.stop_user_sessions(customer.pk, KickReason.ACCESS_EXPIRED) == 1
    grant.session.refresh_from_db()
    assert grant.session.end_reason == EndReason.EXPIRED
    assert state_redis().get(concurrency.kick_key(grant.session_key)) == b"access_expired"
    assert services.stop_user_sessions(customer.pk, KickReason.ACCESS_EXPIRED) == 0


def test_sweeper_closes_idle_segmented_sessions(customer: User, make_title: TitleFactory) -> None:
    hls = make_title(renditions=(PlayableRendition(uuid7().hex, RenditionKind.HLS, 1080),))
    grant = start(customer, hls)
    now = T0.timestamp()
    assert services.sweep(now=now + 100).reaped == 0
    result = services.sweep(now=now + 121)
    assert (result.reaped, result.live) == (1, 0)
    grant.session.refresh_from_db()
    assert (grant.session.end_reason, grant.session.ended_at) == (EndReason.IDLE, T0)
    assert not state_redis().exists(concurrency.conc_key(customer.pk))


def test_sweeper_lets_long_progressive_responses_run(
    customer: User, make_title: TitleFactory
) -> None:
    grant = start(customer, make_title(runtime_s=3600))
    now = T0.timestamp()
    result = services.sweep(now=now + 1800)
    assert (result.reaped, result.live) == (0, 1)
    assert services.sweep(now=now + 3600 + 600 + 1).reaped == 1
    grant.session.refresh_from_db()
    assert grant.session.end_reason == EndReason.IDLE


def test_sweeper_copies_live_state_into_open_rows(customer: User, make_title: TitleFactory) -> None:
    grant = start(customer, make_title())
    seen = T0.timestamp() + 500
    key = concurrency.sess_key(grant.session_key)
    state_redis().hset(key, mapping={"seen": repr(seen), "bytes": "123456", "ip": "198.51.100.4"})
    services.sweep(now=seen + 10)
    grant.session.refresh_from_db()
    assert grant.session.last_heartbeat_at == datetime.fromtimestamp(seen, tz=UTC)
    assert (grant.session.bytes_sent, grant.session.ip) == (123456, "198.51.100.4")


def test_sweeper_closes_rows_that_lost_their_record(
    customer: User, make_title: TitleFactory
) -> None:
    grant = start(customer, make_title())
    state_redis().delete(concurrency.sess_key(grant.session_key))
    assert services.sweep(now=T0.timestamp() + 60).orphans == 0  # may still be starting
    assert services.sweep(now=T0.timestamp() + 121).orphans == 1
    grant.session.refresh_from_db()
    assert (grant.session.end_reason, grant.session.ended_at) == (EndReason.IDLE, T0)


def test_a_lapsed_session_of_the_device_ends_when_it_starts_another_title(
    customer: User, make_title: TitleFactory
) -> None:
    first = start(customer, make_title())
    # Five minutes of one long response: the slot lapsed, the record lives on.
    second = start(customer, make_title(), now=T0 + timedelta(minutes=5))
    first.session.refresh_from_db()
    assert first.session.end_reason == EndReason.STOPPED
    assert records.read_record(first.session_key) is None
    assert state_redis().get(concurrency.kick_key(first.session_key)) == b"replaced"
    assert second.session.ended_at is None
