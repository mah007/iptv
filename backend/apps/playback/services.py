"""Playback services (SPEC §7.4): start a stream, stop sessions, sweep idle ones.

`start_playback` runs the entitlement checks in SPEC's fixed fail-fast order, each
failure with a stable code (`Denial`):

1. user active                 ACCOUNT_SUSPENDED
2. access period active        SUBSCRIPTION_EXPIRED
3. device usable               DEVICE_BLOCKED, DEVICE_NOT_APPROVED
4. IP and country rules        IP_BLOCKED, GEO_BLOCKED (apps.playback.rules)
5. content type allowed        CONTENT_TYPE_NOT_ALLOWED
6. category allowed            CATEGORY_NOT_ALLOWED
7. quality ceiling             TITLE_PREPARING (nothing playable yet), QUALITY_NOT_ALLOWED
8. licence not expired         LICENSE_EXPIRED
9. a concurrency slot          CONCURRENCY_LIMIT

then opens (or, within the 90 s reuse window, reuses) the session and returns the
signed edge URL `<media>/v/<token>/<file>` (ADR-0007). The caller describes the
title with a `PlayableTitle`: the catalogue lookup that builds one belongs to the
callers (Xtream play URLs, the portal API), so this module never reads catalogue
models and never sees a storage path.
"""

import hashlib
import ipaddress
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

import structlog
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from apps.accounts.models import Device, User, UserStatus
from apps.audit import services as audit
from apps.core.errors import ErrorCode, ProblemError
from apps.core.metrics import ACTIVE_STREAMS, CONCURRENCY_REJECTIONS, KICKS, STREAM_STARTS
from apps.core.services import get_setting
from apps.core.stores import state_redis
from apps.playback import concurrency, conf, entitlements, records, rules, tokens
from apps.playback.concurrency import KickReason, SlotStatus
from apps.playback.entitlements import Entitlement, EntitlementStatus
from apps.playback.models import EndReason, PlaybackSession, TitleKind
from apps.playback.records import SessionRecord

logger = structlog.get_logger(__name__)

# How long a session record outlives the newest token's expiry (covers a sweep).
RECORD_GRACE_S = 300
# Open rows younger than this may still be waiting for their record.
ORPHAN_GRACE_S = 120


# --- What callers describe ------------------------------------------------------------


class RenditionKind(StrEnum):
    """Rendition names, the token's `rendition` field (ADR-0007 storage layout)."""

    COMPAT = "compat"  # H.264/AAC progressive MP4: plays everywhere
    SOURCE = "source"  # a direct-play-compatible original
    UHD = "uhd"  # 4K progressive MP4
    HLS = "hls"  # segmented: hls/master.m3u8 and everything under hls/
    LIVE = "live"  # a live channel: live.ts (relay) or live/index.m3u8 (ADR-0017)
    ARCHIVE = "archive"  # a live channel's catch-up: archive/<start>-<seconds>.<ts|m3u8>


class Delivery(StrEnum):
    PROGRESSIVE = "progressive"  # one file, ranges: one response can last a whole film
    SEGMENTED = "segmented"  # playlists and segments: requests keep flowing


class Prefer(StrEnum):
    MP4 = "mp4"
    HLS = "hls"


# Tie-break between renditions of the same height: lower is better.
_KIND_RANK = {
    RenditionKind.COMPAT: 0,
    RenditionKind.SOURCE: 1,
    RenditionKind.UHD: 2,
    RenditionKind.HLS: 3,
    RenditionKind.LIVE: 4,
    RenditionKind.ARCHIVE: 5,
}

#: Kinds whose requests keep flowing (playlists, segments, or the live relay's own
#: stream-auth calls every 30 s), so a session idles out after the heartbeat TTL.
_SEGMENTED_KINDS = frozenset({RenditionKind.HLS, RenditionKind.LIVE, RenditionKind.ARCHIVE})
#: Title kinds of live TV: `allow_live` gates them and their tokens live
#: `playback.token_ttl_live_s` (SPEC §7.4: 6 h).
LIVE_KINDS = frozenset({TitleKind.LIVE, TitleKind.CATCHUP})


@dataclass(frozen=True, slots=True)
class PlayableRendition:
    """One playable form of a title.

    `storage_key` is the asset's directory key under the media root (the token's
    `title` field, `[A-Za-z0-9_-]{1,64}`, e.g. the media file's UUID), never a path:
    the edge serves `<root>/<storage_key>/<kind>.<container>` or, for HLS,
    `<root>/<storage_key>/<name>/...`.

    `name` is the token's rendition when it differs from the kind: an HLS presentation
    other than the full ladder (`hls720` capped at 720p, `hls2160` with the UHD rung;
    ADR-0014), so a token for a capped presentation cannot reach a taller rung.
    `height` is the presentation's ceiling.

    `path` names the entry file when it is not derived from the kind: a live channel's
    `live.ts` or `live/index.m3u8`, or a catch-up window `archive/<start>-<s>.ts`
    (ADR-0017). It must stay inside the token's scope.
    """

    storage_key: str
    kind: RenditionKind
    height: int
    container: str = "mp4"
    name: str = ""
    path: str = ""

    def __post_init__(self) -> None:
        if not tokens.TITLE.fullmatch(self.storage_key):
            msg = "storage_key must be an asset key ([A-Za-z0-9_-]{1,64}), not a path"
            raise ValueError(msg)
        if not tokens.EXTENSION.fullmatch(self.container):
            msg = "container must be a file extension such as mp4"
            raise ValueError(msg)
        if self.name and not tokens.RENDITION.fullmatch(self.name):
            msg = "name must be a token rendition ([A-Za-z0-9_-]{1,32})"
            raise ValueError(msg)
        if self.path and not (
            tokens.valid_tail(self.path) and tokens.in_scope(self.token_rendition, self.path)
        ):
            msg = "path must be a media path inside the token's scope"
            raise ValueError(msg)

    @property
    def delivery(self) -> Delivery:
        return Delivery.SEGMENTED if self.kind in _SEGMENTED_KINDS else Delivery.PROGRESSIVE

    @property
    def token_rendition(self) -> str:
        """The token's `rendition` field: the file stem or folder the token reaches."""
        return self.name or self.kind.value

    @property
    def entry(self) -> str:
        """The file the playback URL names, relative to the asset."""
        if self.path:
            return self.path
        if self.kind is RenditionKind.HLS:
            return f"{self.token_rendition}/master.m3u8"
        return f"{self.token_rendition}.{self.container}"


@dataclass(frozen=True, slots=True)
class PlayableTitle:
    """A movie or episode as playback needs it (built by the catalogue lookup)."""

    kind: TitleKind
    id: UUID
    renditions: tuple[PlayableRendition, ...]
    category_ids: frozenset[UUID] = field(default_factory=frozenset)
    adult: bool = False  # carried for parental controls; no SPEC §7.4 check reads it
    runtime_s: int = 0
    license_expires_at: datetime | None = None
    name: str = ""

    @property
    def ref(self) -> str:
        return f"{self.kind.value}:{self.id}"


@dataclass(frozen=True, slots=True)
class PlaybackGrant:
    url: str
    session: PlaybackSession
    session_key: str
    rendition: PlayableRendition
    expires_at: int  # Unix seconds: the token's exp
    reused: bool  # True within the reuse window (seek, reconnect)


# --- Denials --------------------------------------------------------------------------


class Denial(StrEnum):
    """Stable codes of refused playback starts, in check order."""

    ACCOUNT_SUSPENDED = "ACCOUNT_SUSPENDED"
    SUBSCRIPTION_EXPIRED = "SUBSCRIPTION_EXPIRED"
    DEVICE_BLOCKED = "DEVICE_BLOCKED"
    DEVICE_NOT_APPROVED = "DEVICE_NOT_APPROVED"
    IP_BLOCKED = "IP_BLOCKED"
    GEO_BLOCKED = "GEO_BLOCKED"
    CONTENT_TYPE_NOT_ALLOWED = "CONTENT_TYPE_NOT_ALLOWED"
    CATEGORY_NOT_ALLOWED = "CATEGORY_NOT_ALLOWED"
    TITLE_PREPARING = "TITLE_PREPARING"
    QUALITY_NOT_ALLOWED = "QUALITY_NOT_ALLOWED"
    LICENSE_EXPIRED = "LICENSE_EXPIRED"
    CONCURRENCY_LIMIT = "CONCURRENCY_LIMIT"


_DENIALS: dict[Denial, tuple[int, str]] = {
    Denial.ACCOUNT_SUSPENDED: (403, "The account is suspended."),
    Denial.SUBSCRIPTION_EXPIRED: (403, "There is no active subscription."),
    Denial.DEVICE_BLOCKED: (403, "This device is blocked."),
    Denial.DEVICE_NOT_APPROVED: (403, "This device is waiting for approval."),
    Denial.IP_BLOCKED: (403, "Playback is not allowed from this network."),
    Denial.GEO_BLOCKED: (403, "Playback is not available in this country."),
    Denial.CONTENT_TYPE_NOT_ALLOWED: (403, "This kind of content is not in the plan."),
    Denial.CATEGORY_NOT_ALLOWED: (403, "This category is not in the plan."),
    Denial.TITLE_PREPARING: (409, "The title is being prepared."),
    Denial.QUALITY_NOT_ALLOWED: (403, "No version of this title fits the plan's quality."),
    Denial.LICENSE_EXPIRED: (403, "This title is no longer available."),
    Denial.CONCURRENCY_LIMIT: (409, "The stream limit is reached."),
}


class PlaybackDenied(ProblemError):
    """A refused start: `denial` is the stable code.

    The problem+json `code` is the ErrorCode of the same name. Codes core does not
    define yet answer PERMISSION_DENIED (with the right status) until they are added.
    """

    def __init__(self, denial: Denial, detail: str | None = None) -> None:
        status, message = _DENIALS[denial]
        code = ErrorCode.__members__.get(denial.value, ErrorCode.PERMISSION_DENIED)
        super().__init__(code, detail or message, status=status)
        self.denial = denial


# --- Session identity -------------------------------------------------------------------


def session_key(user_id: UUID | str, device_id: UUID | str, title_ref: str) -> str:
    """SPEC's `sha256(user_id|device_id|title_ref)`, as the 32 hex digits tokens carry."""
    digest = hashlib.sha256(f"{user_id}|{device_id}|{title_ref}".encode()).hexdigest()
    return digest[:32]


def _valid_ip(value: str | None) -> str | None:
    try:
        return str(ipaddress.ip_address(value)) if value else None
    except ValueError:
        return None


def _aware(seconds: float) -> datetime:
    return datetime.fromtimestamp(seconds, tz=UTC)


# --- The checks -------------------------------------------------------------------------


def _within_period(entitlement: Entitlement, now: datetime) -> bool:
    ends_at, grace_until = entitlement["ends_at"], entitlement["grace_until"]
    if ends_at is None or datetime.fromisoformat(ends_at) > now:
        return True
    return grace_until is not None and datetime.fromisoformat(grace_until) > now


def _check_account(user: User, now: datetime) -> Entitlement:
    """Checks 1 and 2."""
    if user.status != UserStatus.ACTIVE or not user.is_active:
        raise PlaybackDenied(Denial.ACCOUNT_SUSPENDED)
    entitlement = entitlements.get(user.pk)
    if entitlement is None:
        raise PlaybackDenied(Denial.SUBSCRIPTION_EXPIRED)
    status = entitlement["status"]
    if status in {EntitlementStatus.SUSPENDED, EntitlementStatus.DISABLED}:
        raise PlaybackDenied(Denial.ACCOUNT_SUSPENDED)
    if status != EntitlementStatus.ACTIVE or not _within_period(entitlement, now):
        raise PlaybackDenied(Denial.SUBSCRIPTION_EXPIRED)
    return entitlement


def _check_device(user: User, device: Device) -> None:
    """Check 3."""
    if device.user_id != user.pk:
        msg = "the device belongs to another user"
        raise ValueError(msg)
    if device.revoked_at is not None or device.blocked:
        raise PlaybackDenied(Denial.DEVICE_BLOCKED)
    if not device.approved:
        raise PlaybackDenied(Denial.DEVICE_NOT_APPROVED)


def _check_rules(
    entitlement: Entitlement, *, client_ip: str | None, country: str | None, now: datetime
) -> None:
    """Check 4."""
    applicable = [
        *rules.customer_rules(entitlement["ip_rules"], now),
        *rules.customer_rules(entitlement["country_rules"], now),
        *rules.global_rules(now),
    ]
    verdict = rules.evaluate(applicable, client_ip=client_ip, country=country)
    if verdict is rules.RuleVerdict.IP_BLOCKED:
        raise PlaybackDenied(Denial.IP_BLOCKED)
    if verdict is rules.RuleVerdict.GEO_BLOCKED:
        raise PlaybackDenied(Denial.GEO_BLOCKED)


def _check_content(entitlement: Entitlement, title: PlayableTitle) -> None:
    """Checks 5 and 6."""
    if title.kind in LIVE_KINDS:
        allowed = entitlement["allow_live"]
    elif title.kind == TitleKind.MOVIE:
        allowed = entitlement["allow_movies"]
    else:
        allowed = entitlement["allow_series"]
    if not allowed:
        raise PlaybackDenied(Denial.CONTENT_TYPE_NOT_ALLOWED)
    categories = entitlement["categories"]
    if categories is not None and not {UUID(value) for value in categories} & title.category_ids:
        raise PlaybackDenied(Denial.CATEGORY_NOT_ALLOWED)


def _choose_rendition(
    entitlement: Entitlement, title: PlayableTitle, prefer: Prefer | None
) -> PlayableRendition:
    """Check 7: the best rendition within the quality ceiling, by preference."""
    if not title.renditions:
        raise PlaybackDenied(Denial.TITLE_PREPARING)
    within = [r for r in title.renditions if r.height <= entitlement["max_quality"]]
    if not within:
        raise PlaybackDenied(Denial.QUALITY_NOT_ALLOWED)
    if prefer is not None:
        wanted = Delivery.SEGMENTED if prefer is Prefer.HLS else Delivery.PROGRESSIVE
        within = [r for r in within if r.delivery is wanted] or within
    return max(within, key=lambda r: (r.height, -_KIND_RANK[r.kind]))


def _check_license(title: PlayableTitle, now: datetime) -> None:
    """Check 8."""
    if title.license_expires_at is not None and title.license_expires_at <= now:
        raise PlaybackDenied(Denial.LICENSE_EXPIRED)


# --- Starting a stream --------------------------------------------------------------------


def _token_net(client_ip: str | None) -> str | None:
    if not client_ip or get_setting("playback.ip_binding") is not True:
        return None
    try:
        return tokens.client_net(client_ip)
    except ValueError:
        return None


def _idle_s(rendition: PlayableRendition, title: PlayableTitle) -> int:
    heartbeat = conf.heartbeat_ttl_s()
    if rendition.delivery is Delivery.SEGMENTED:
        return heartbeat
    return max(heartbeat, max(0, title.runtime_s) + conf.progressive_slack_s())


@dataclass(frozen=True, slots=True)
class _Start:
    user: User
    device: Device
    title: PlayableTitle
    rendition: PlayableRendition
    entitlement: Entitlement
    client_ip: str | None
    country: str
    user_agent: str
    moment: datetime


def _close_row(
    row: PlaybackSession,
    reason: EndReason,
    *,
    ended_at: datetime,
    last_seen: float | None,
    bytes_sent: int,
) -> bool:
    """Close an open row (no-op if something closed it first)."""
    last_heartbeat = max(row.last_heartbeat_at, _aware(last_seen)) if last_seen else None
    changes = {
        "ended_at": max(ended_at, row.started_at),
        "end_reason": reason,
        "bytes_sent": max(row.bytes_sent, bytes_sent),
        "last_heartbeat_at": last_heartbeat or row.last_heartbeat_at,
        "updated_at": timezone.now(),
    }
    updated = PlaybackSession.objects.filter(pk=row.pk, ended_at__isnull=True).update(**changes)
    if updated:
        for name, value in changes.items():
            setattr(row, name, value)
    return bool(updated)


_EVICTION_END = {
    KickReason.STREAM_LIMIT: EndReason.LIMIT,
    KickReason.REPLACED: EndReason.STOPPED,
}


def _session_row(
    start: _Start, key: str, slot: concurrency.SlotResult, previous: SessionRecord | None
) -> tuple[PlaybackSession, bool]:
    """The open row of this playback: reused within the window, else a new one."""
    if slot.status is SlotStatus.REFRESHED and previous is not None:
        row = PlaybackSession.objects.filter(pk=previous.row, ended_at__isnull=True).first()
        if row is not None:
            return row, True
    if slot.status is SlotStatus.ADDED:
        # Whatever is still open under this key belongs to an earlier playback.
        for stale in PlaybackSession.objects.filter(session_key=key, ended_at__isnull=True):
            if previous is not None and previous.row == str(stale.pk):
                _close_row(
                    stale,
                    EndReason.IDLE,
                    ended_at=_aware(previous.seen),
                    last_seen=previous.seen,
                    bytes_sent=previous.bytes,
                )
            else:
                _close_row(
                    stale,
                    EndReason.IDLE,
                    ended_at=stale.last_heartbeat_at,
                    last_seen=None,
                    bytes_sent=0,
                )
    row, created = PlaybackSession.objects.get_or_create(
        session_key=key,
        ended_at=None,
        defaults={
            "user": start.user,
            "device": start.device,
            "title_kind": start.title.kind,
            "title_id": start.title.id,
            "title_name": start.title.name[:255],
            "rendition": start.rendition.kind.value,
            "ip": start.client_ip,
            "country": start.country,
            "user_agent": start.user_agent[:256],
            "player": start.device.app_hint,
            "started_at": start.moment,
            "last_heartbeat_at": start.moment,
        },
    )
    return row, not created


def _close_evicted(evicted: Iterable[concurrency.Eviction], moment: datetime) -> None:
    for eviction in evicted:
        KICKS.labels(reason=eviction.reason.value).inc()
        rows = PlaybackSession.objects.filter(session_key=eviction.session, ended_at__isnull=True)
        for row in rows:
            _close_row(
                row,
                _EVICTION_END.get(eviction.reason, EndReason.KICKED),
                ended_at=moment,
                last_seen=eviction.last_seen,
                bytes_sent=eviction.bytes_sent,
            )


def _open_session(start: _Start) -> PlaybackGrant:
    """Check 9 and the session: slot, row, record and token."""
    keyset = tokens.keyring()  # fail before taking a slot when keys are missing
    key = session_key(start.user.pk, start.device.pk, start.title.ref)
    now_s = start.moment.timestamp()
    slot = concurrency.acquire(
        user_id=start.user.pk,
        session=key,
        device_id=start.device.pk,
        max_streams=start.entitlement["max_streams"],
        policy=start.entitlement["policy"],
        now=now_s,
    )
    if slot.status is SlotStatus.REJECTED:
        raise PlaybackDenied(Denial.CONCURRENCY_LIMIT)
    if start.title.kind in LIVE_KINDS:
        lifetime = int(get_setting("playback.token_ttl_live_s"))
    else:
        lifetime = max(0, start.title.runtime_s) + int(get_setting("playback.token_ttl_vod_s"))
    # Verifiers refuse an exp further than MAX_TTL_S ahead (ADR-0007 §2).
    exp = int(now_s) + min(lifetime, tokens.MAX_TTL_S - 3600)
    try:
        previous = records.read_record(key)
        with transaction.atomic():
            row, reused = _session_row(start, key, slot, previous)
            _close_evicted(slot.evicted, start.moment)
        token = tokens.sign(
            keyset,
            session=key,
            title=start.rendition.storage_key,
            rendition=start.rendition.token_rendition,
            exp=exp,
            net=_token_net(start.client_ip),
        )
        # Within the reuse window the record carries on; otherwise it starts afresh.
        carried = previous if reused and previous and previous.row == str(row.pk) else None
        record = SessionRecord(
            session=key,
            row=str(row.pk),
            user=str(start.user.pk),
            device=str(start.device.pk),
            title=start.title.ref,
            rendition=start.rendition.kind.value,
            delivery=start.rendition.delivery.value,
            started=carried.started if carried else row.started_at.timestamp(),
            seen=now_s,
            exp=max(exp, carried.exp) if carried else exp,
            idle=_idle_s(start.rendition, start.title),
            bytes=carried.bytes if carried else 0,
            ip=start.client_ip or "",
            country=start.country,
            edge=carried.edge if carried else "",
            max_streams=start.entitlement["max_streams"],
            policy=start.entitlement["policy"],
            user_name=start.user.get_full_name(),
            device_name=start.device.name,
            title_name=start.title.name,
        )
        records.write_record(
            record, ttl_s=record.exp + tokens.CLOCK_SKEW_S + RECORD_GRACE_S - int(now_s)
        )
    except BaseException:
        if slot.status is SlotStatus.ADDED:
            concurrency.release(start.user.pk, key, start.device.pk)
        raise
    if slot.status is SlotStatus.ADDED:
        # One stream per device: a session of this device whose slot had already
        # lapsed (one long response) was not evicted by the script; end it too.
        stop_sessions(
            open_sessions().filter(device=start.device).exclude(session_key=key),
            KickReason.REPLACED,
            now=start.moment,
        )
    url = f"{conf.media_base_url()}/v/{token}/{start.rendition.entry}"
    return PlaybackGrant(
        url=url,
        session=row,
        session_key=key,
        rendition=start.rendition,
        expires_at=exp,
        reused=reused,
    )


def start_playback(  # noqa: PLR0913 (keyword-only request context)
    user: User,
    device: Device,
    title: PlayableTitle,
    *,
    prefer: Prefer | None = None,
    client_ip: str | None = None,
    country: str | None = None,
    user_agent: str = "",
    now: datetime | None = None,
) -> PlaybackGrant:
    """Run the SPEC §7.4 checks, take a slot, open or reuse the session and return
    the signed edge URL. Raises PlaybackDenied with a stable code.

    `device` must belong to `user` (the caller authenticated it). `client_ip` and
    `country` describe the client as the caller saw it.
    """
    moment = now or timezone.now()
    client_ip = _valid_ip(client_ip)
    try:
        entitlement = _check_account(user, moment)
        _check_device(user, device)
        _check_rules(entitlement, client_ip=client_ip, country=country, now=moment)
        _check_content(entitlement, title)
        rendition = _choose_rendition(entitlement, title, prefer)
        _check_license(title, moment)
        grant = _open_session(
            _Start(
                user=user,
                device=device,
                title=title,
                rendition=rendition,
                entitlement=entitlement,
                client_ip=client_ip,
                country=(country or "").upper()[:2],
                user_agent=user_agent,
                moment=moment,
            )
        )
    except PlaybackDenied as denied:
        STREAM_STARTS.labels(result=denied.denial.value.lower()).inc()
        if denied.denial is Denial.CONCURRENCY_LIMIT:
            CONCURRENCY_REJECTIONS.inc()
        logger.info(
            "playback.denied",
            code=denied.denial.value,
            user_id=str(user.pk),
            device_id=str(device.pk),
            title=title.ref,
        )
        raise
    STREAM_STARTS.labels(result="ok").inc()
    logger.info(
        "playback.started",
        session=grant.session_key[: tokens.SESSION_LOG_PREFIX],
        user_id=str(user.pk),
        device_id=str(device.pk),
        title=title.ref,
        rendition=rendition.kind.value,
        reused=grant.reused,
    )
    return grant


# --- Stopping sessions ----------------------------------------------------------------------

_KICK_END = {
    KickReason.KICKED: EndReason.KICKED,
    KickReason.STREAM_LIMIT: EndReason.LIMIT,
    KickReason.REPLACED: EndReason.STOPPED,
    KickReason.ACCESS_EXPIRED: EndReason.EXPIRED,
    KickReason.ACCESS_SUSPENDED: EndReason.KICKED,
    KickReason.DEVICE_DISABLED: EndReason.KICKED,
    KickReason.LICENSE_EXPIRED: EndReason.KICKED,
    KickReason.STOPPED: EndReason.STOPPED,
}


def stop_sessions(
    sessions: Iterable[PlaybackSession], reason: KickReason, *, now: datetime | None = None
) -> list[PlaybackSession]:
    """Stop open sessions now: the edge refuses their next request (within its
    60 s auth cache), their slots are free and their rows close."""
    moment = now or timezone.now()
    stopped = []
    for row in sessions:
        finished = concurrency.finish(row.session_key, reason)
        if _close_row(
            row,
            _KICK_END[reason],
            ended_at=moment,
            last_seen=finished.last_seen,
            bytes_sent=finished.bytes_sent,
        ):
            KICKS.labels(reason=reason.value).inc()
            stopped.append(row)
    return stopped


def open_sessions() -> QuerySet[PlaybackSession]:
    return PlaybackSession.objects.filter(ended_at__isnull=True)


def stop_user_sessions(user_id: UUID | str, reason: KickReason) -> int:
    return len(stop_sessions(open_sessions().filter(user_id=user_id), reason))


def stop_device_sessions(device_id: UUID | str, reason: KickReason) -> int:
    return len(stop_sessions(open_sessions().filter(device_id=device_id), reason))


def kill_session(
    session: PlaybackSession, *, actor: User | None, ip: str | None = None
) -> PlaybackSession:
    """Admin "kill": stop one session (RBAC sessions.kill), audited."""
    with transaction.atomic():
        row = PlaybackSession.objects.select_for_update().get(pk=session.pk)
        if row.ended_at is not None:
            raise ProblemError(ErrorCode.CONFLICT, "The session has already ended.")
        stop_sessions([row], KickReason.KICKED)
        audit.record(
            "session.kill",
            actor=actor,
            target=row,
            before={"ended_at": None},
            after={"ended_at": row.ended_at, "end_reason": row.end_reason},
            ip=ip,
        )
    return row


# --- The sweeper --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SweepResult:
    reaped: int
    orphans: int
    live: int


def _close_reaped(reaped: Iterable[concurrency.Reaped]) -> int:
    closed = 0
    for item in reaped:
        row = PlaybackSession.objects.filter(pk=item.row, ended_at__isnull=True).first()
        if row is not None and _close_row(
            row,
            EndReason.IDLE,
            ended_at=_aware(item.last_seen),
            last_seen=item.last_seen,
            bytes_sent=item.bytes_sent,
        ):
            closed += 1
    return closed


def _sync_open_rows(now_s: float) -> tuple[int, int]:
    """Copy live records into open rows, close rows that lost their record, and
    publish the active-streams gauge. Returns (orphans closed, live sessions)."""
    rows = list(open_sessions().order_by("started_at"))
    if not rows:
        ACTIVE_STREAMS.publish({})
        return 0, 0
    pipe = state_redis().pipeline(transaction=False)
    for row in rows:
        pipe.hgetall(concurrency.sess_key(row.session_key))
    raws = pipe.execute()
    orphans, changed = 0, []
    gauge: dict[tuple[str, ...], float] = {}
    orphan_before = _aware(now_s - ORPHAN_GRACE_S)
    for row, raw in zip(rows, raws, strict=True):
        record = records.parse_record(row.session_key, raw) if raw else None
        if record is None or record.row != str(row.pk):
            if row.last_heartbeat_at < orphan_before and _close_row(
                row, EndReason.IDLE, ended_at=row.last_heartbeat_at, last_seen=None, bytes_sent=0
            ):
                orphans += 1
            continue
        labels = ("access_profile", record.rendition, record.edge)
        gauge[labels] = gauge.get(labels, 0) + 1
        seen = _aware(record.seen)
        if seen > row.last_heartbeat_at or record.bytes > row.bytes_sent:
            row.last_heartbeat_at = max(seen, row.last_heartbeat_at)
            row.bytes_sent = max(record.bytes, row.bytes_sent)
            if record.ip:
                row.ip = record.ip
            changed.append(row)
    if changed:
        PlaybackSession.objects.bulk_update(changed, ["last_heartbeat_at", "bytes_sent", "ip"])
    ACTIVE_STREAMS.publish(gauge)
    return orphans, sum(int(count) for count in gauge.values())


def sweep(*, now: float | None = None) -> SweepResult:
    """Every minute (beat): end sessions idle past their record's idle time
    (120 s, or the runtime plus slack for progressive ones) and keep open rows
    in step with Redis."""
    now_s = now if now is not None else time.time()
    reaped = []
    for key in records.index_before(now_s - conf.heartbeat_ttl_s()):
        item = concurrency.reap(key, now_s)
        if item is not None:
            reaped.append(item)
    closed = _close_reaped(reaped)
    orphans, live = _sync_open_rows(now_s)
    if closed or orphans:
        logger.info("playback.swept", idle=closed, orphans=orphans, live=live)
    return SweepResult(reaped=closed, orphans=orphans, live=live)
