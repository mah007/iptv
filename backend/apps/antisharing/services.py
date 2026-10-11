"""Anti-sharing: what was seen, the score, flags and the admin's ladder (SPEC §11.1; ADR-0021).

**Sightings.** Each place a customer is seen from becomes a `Sighting` per device,
network (/24 or /64), source and clock hour:

- IPTV app sign-ins (`record` from the Xtream host, at most once per device and network
  every `SIGHTING_INTERVAL_S`);
- playback starts (web and Xtream);
- the networks a running stream's segments come from: stream-auth adds the client's
  network to `sessnets:<session>` in redis-state (Redis only, like the rest of
  stream-auth, G-18), and `collect_stream_networks` turns them into sightings.

**Scoring.** `score_recent` (beat, every 5 minutes) assesses every customer seen in the
last 24 hours (`apps.antisharing.scoring`). A score at `antisharing.flag_score` opens or
raises the customer's flag and tells the admins (alert recipients and Telegram) once per
rise. Flags never lock an account.

**The ladder** is manual and audited, and each step tells the customer: `warn` (the
`sharing_warning` message), `reset_credentials` (every IPTV app login gets an unknown
password, its streams stop, `sharing_reset` asks the customer to make new logins in the
portal) and `suspend`.
"""

import ipaddress
import logging
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any, Final, cast
from uuid import UUID

import redis
from django.db import IntegrityError, transaction
from django.db.models import F
from django.db.models.functions import Greatest
from django.utils import timezone

from apps.accounts import services as accounts
from apps.accounts.models import Device, DeviceKind, User
from apps.antisharing import scoring
from apps.antisharing.models import FlagStatus, RiskFlag, Sighting, SightingSource
from apps.audit import services as audit
from apps.core import geoip
from apps.core.services import get_setting
from apps.core.stores import state_redis

logger = logging.getLogger(__name__)

SIGHTING_INTERVAL_S: Final = 300
STREAM_NETS_PREFIX: Final = "sessnets:"
STREAM_NETS_MAX: Final = 16
STREAM_NETS_TTL_S: Final = 1800
USERS_PER_RUN: Final = 5000
EVIDENCE_LIMIT: Final = 50
_SEEN_PREFIX: Final = "risk:seen:"


def enabled() -> bool:
    return bool(get_setting("antisharing.enabled"))


def network_of(ip: str | None) -> str | None:
    """`203.0.113.0/24`, `2001:db8:1:2::/64`, or `local` for the operator's network."""
    if not ip:
        return None
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if address.is_private or address.is_loopback or address.is_link_local:
        return "local"
    prefix = 24 if address.version == 4 else 64
    return str(ipaddress.ip_network(f"{address}/{prefix}", strict=False))


def _hour(moment: datetime) -> datetime:
    return moment.replace(minute=0, second=0, microsecond=0)


def record(  # noqa: PLR0913 (who, where and how; the rest keyword-only)
    user: User | UUID | str,
    device: Device | UUID | str | None,
    ip: str | None,
    source: SightingSource,
    *,
    at: datetime | None = None,
    geo: geoip.Geo | None = None,
    throttle: bool = True,
) -> Sighting | None:
    """Remember that `user` (and `device`) was seen from `ip`. Never raises."""
    try:
        if not enabled():
            return None
        network = network_of(ip)
        if network is None:
            return None
        user_id = str(getattr(user, "pk", user))
        device_id = str(getattr(device, "pk", device)) if device is not None else None
        if throttle and not _first_in_interval(user_id, device_id, network, source):
            return None
        info = geo or geoip.lookup(ip)
        moment = at or timezone.now()
        sighting = _upsert(user_id, device_id, network, source, moment, info)
        if device_id and info.country:
            Device.objects.filter(pk=device_id).exclude(last_country=info.country).update(
                last_country=info.country
            )
        return sighting
    except Exception:  # anti-sharing must never break sign-in or playback
        logger.warning("antisharing: sighting not recorded", exc_info=True)
        return None


def _first_in_interval(user_id: str, device_id: str | None, network: str, source: str) -> bool:
    key = f"{_SEEN_PREFIX}{user_id}:{device_id or '-'}:{network}:{source}"
    try:
        return bool(state_redis().set(key, 1, nx=True, ex=SIGHTING_INTERVAL_S))
    except redis.RedisError:
        return True


def _upsert(  # noqa: PLR0913, PLR0917 (one sighting's fields)
    user_id: str,
    device_id: str | None,
    network: str,
    source: str,
    moment: datetime,
    info: geoip.Geo,
) -> Sighting:
    hour = _hour(moment)
    lookup = {
        "user_id": user_id,
        "device_id": device_id,
        "network": network,
        "source": source,
        "hour": hour,
    }
    updated = Sighting.objects.filter(**lookup).update(
        last_seen=Greatest(F("last_seen"), moment), hits=F("hits") + 1
    )
    if updated:
        return Sighting.objects.filter(**lookup).first() or Sighting(**lookup)
    try:
        with transaction.atomic():
            return Sighting.objects.create(
                **lookup,
                asn=info.asn,
                asn_org=info.asn_org,
                country=info.country or "",
                first_seen=moment,
                last_seen=moment,
            )
    except IntegrityError:  # a concurrent request created it first
        Sighting.objects.filter(**lookup).update(
            last_seen=Greatest(F("last_seen"), moment), hits=F("hits") + 1
        )
        return Sighting.objects.get(**lookup)


# --- Stream networks (G-18) -------------------------------------------------------------------


def collect_stream_networks(now: datetime | None = None) -> int:
    """Turn the stream networks of recent sessions into sightings; how many were new."""
    from apps.playback.models import PlaybackSession  # noqa: PLC0415 (playback imports accounts)

    moment = now or timezone.now()
    recent = PlaybackSession.objects.filter(
        last_heartbeat_at__gte=moment - timedelta(seconds=STREAM_NETS_TTL_S)
    ).values_list("session_key", "user_id", "device_id")
    created = 0
    client = state_redis()
    for session_key, user_id, device_id in recent.iterator():
        try:
            members = cast("set[bytes]", client.smembers(f"{STREAM_NETS_PREFIX}{session_key}"))
        except redis.RedisError:
            return created
        for raw in members:
            network = raw.decode()
            host = network.split("/", 1)[0]
            sighting = record(
                user_id, device_id, host, SightingSource.STREAM, at=moment, throttle=True
            )
            if sighting is not None and sighting.hits == 1:
                created += 1
    return created


# --- Scoring ----------------------------------------------------------------------------------


def limits() -> scoring.Limits:
    def number(key: str) -> int:
        return int(cast("int", get_setting(f"antisharing.{key}")))

    return scoring.Limits(
        max_asns_1h=number("max_asns_1h"),
        max_asns_24h=number("max_asns_24h"),
        max_countries_24h=number("max_countries_24h"),
        max_networks_per_credential_1h=number("max_networks_per_credential_1h"),
        travel_speed_kmh=number("travel_speed_kmh"),
        travel_min_km=number("travel_min_km"),
        flag_score=number("flag_score"),
    )


def sightings_of(user_id: UUID | str, since: datetime) -> list[Sighting]:
    return list(
        Sighting.objects.filter(user_id=user_id, last_seen__gte=since)
        .select_related("device")
        .order_by("-last_seen")[:2000]
    )


def _seen(rows: Iterable[Sighting]) -> list[scoring.Seen]:
    return [
        scoring.Seen(
            device=str(row.device_id) if row.device_id else None,
            network=row.network,
            asn=row.asn,
            country=row.country or None,
            first=row.first_seen,
            last=row.last_seen,
            asn_org=row.asn_org,
        )
        for row in rows
    ]


def assess(user_id: UUID | str, now: datetime | None = None) -> scoring.Assessment:
    moment = now or timezone.now()
    return scoring.assess(
        _seen(sightings_of(user_id, moment - timedelta(hours=24))), moment, limits()
    )


def evidence(rows: Iterable[Sighting]) -> list[dict[str, Any]]:
    """The sightings an admin needs to judge a flag (no addresses, networks only)."""
    items = []
    for row in list(rows)[:EVIDENCE_LIMIT]:
        device = row.device
        items.append(
            {
                "network": row.network,
                "asn": row.asn,
                "asn_org": row.asn_org,
                "country": row.country,
                "source": row.source,
                "device_id": str(row.device_id) if row.device_id else None,
                "device_name": device.name if device is not None else "",
                "app": device.app_hint if device is not None else "",
                "first_seen": row.first_seen.isoformat(),
                "last_seen": row.last_seen.isoformat(),
                "hits": row.hits,
            }
        )
    return items


def score_recent(now: datetime | None = None) -> dict[str, int]:
    """Assess everyone seen in the last 24 hours; open or raise flags; tell the admins."""
    if not enabled():
        return {"assessed": 0, "flagged": 0}
    moment = now or timezone.now()
    collect_stream_networks(moment)
    user_ids = list(
        Sighting.objects.filter(last_seen__gte=moment - timedelta(hours=24))
        .exclude(network="local")
        .values_list("user_id", flat=True)
        .distinct()[:USERS_PER_RUN]
    )
    current_limits = limits()
    flagged = 0
    for user_id in user_ids:
        rows = sightings_of(user_id, moment - timedelta(hours=24))
        assessment = scoring.assess(_seen(rows), moment, current_limits)
        if assessment.flagged:
            flagged += 1
            _raise_flag(user_id, assessment, rows, moment)
        else:
            RiskFlag.objects.filter(user_id=user_id, status=FlagStatus.OPEN).update(
                last_scored_at=moment
            )
    return {"assessed": len(user_ids), "flagged": flagged}


def _raise_flag(
    user_id: UUID | str, assessment: scoring.Assessment, rows: list[Sighting], moment: datetime
) -> None:
    with transaction.atomic():
        flag = (
            RiskFlag.objects.select_for_update()
            .filter(user_id=user_id, status=FlagStatus.OPEN)
            .first()
        )
        opened = flag is None
        if flag is None:
            flag = RiskFlag(user_id=user_id, opened_at=moment, last_scored_at=moment)
        flag.last_scored_at = moment
        if opened or assessment.score >= flag.score:
            flag.score = assessment.score
            flag.signals = assessment.as_dict()
            flag.evidence = evidence(rows)
        tell = assessment.score > flag.notified_score
        if tell:
            flag.notified_score = assessment.score
        flag.save()
        if opened:
            audit.record(
                "customer.risk_flag",
                actor=None,
                target=flag.user,
                after={"score": assessment.score},
            )
    if tell and get_setting("antisharing.notify_admins"):
        transaction.on_commit(lambda: _notify_admins(flag, opened=opened), robust=True)


def _notify_admins(flag: RiskFlag, *, opened: bool) -> None:
    from apps.notifications import alerts  # noqa: PLC0415 (notifications imports accounts)

    user = flag.user
    reasons = [
        f"{signal['name']} {signal['value']} (limit {signal['limit']})"
        for signal in cast("list[dict[str, Any]]", flag.signals.get("signals", []))
        if signal.get("points")
    ]
    now = timezone.now().isoformat()
    summary = (
        f"Possible credential sharing: {user.get_full_name()} ({user.username})"
        if opened
        else f"Sharing score rose to {flag.score}: {user.get_full_name()} ({user.username})"
    )
    outcome = alerts.deliver(
        {
            "status": "firing",
            "commonLabels": {"alertname": "SharingSuspected", "severity": "warning"},
            "externalURL": "",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {
                        "alertname": "SharingSuspected",
                        "severity": "warning",
                        "service": "antisharing",
                    },
                    "annotations": {
                        "summary": summary,
                        "description": f"Score {flag.score}: {', '.join(reasons)}. Review the "
                        "customer's Security tab in the admin; nothing was locked.",
                        "runbook": "docs/runbooks/leaked-credential.md#sharing-flags",
                    },
                    "startsAt": now,
                    "endsAt": "",
                    "generatorURL": "",
                }
            ],
        }
    )
    logger.info("antisharing.flag_notice", extra={"channels": outcome.channels})


# --- The admin's ladder -----------------------------------------------------------------------


def open_flag(user: User) -> RiskFlag | None:
    return RiskFlag.objects.filter(user=user, status=FlagStatus.OPEN).first()


def latest_flag(user: User) -> RiskFlag | None:
    return RiskFlag.objects.filter(user=user).order_by("-opened_at").first()


def _note_action(user: User, action: str, actor: User | None) -> None:
    flag = open_flag(user)
    if flag is None:
        return
    flag.actions = [
        *flag.actions,
        {"action": action, "at": timezone.now().isoformat(), "by": str(actor.pk) if actor else ""},
    ]
    flag.save(update_fields=["actions", "updated_at"])


def mark_reviewed(user: User, *, actor: User | None, ip: str | None, note: str = "") -> RiskFlag:
    with transaction.atomic():
        flag = (
            RiskFlag.objects.select_for_update().filter(user=user, status=FlagStatus.OPEN).first()
        )
        if flag is None:
            from apps.core.errors import ErrorCode, ProblemError  # noqa: PLC0415

            raise ProblemError(ErrorCode.NOT_FOUND, "This customer has no open flag.", status=404)
        flag.status = FlagStatus.REVIEWED
        flag.reviewed_by = actor
        flag.reviewed_at = timezone.now()
        flag.note = note.strip()[:2000]
        flag.save(update_fields=["status", "reviewed_by", "reviewed_at", "note", "updated_at"])
        audit.record(
            "customer.risk_review",
            actor=actor,
            target=user,
            after={"score": flag.score, "note": bool(flag.note)},
            ip=ip,
        )
    return flag


def warn(user: User, *, actor: User | None, ip: str | None) -> None:
    """Step 1: tell the customer their account looks shared (an email)."""
    from apps.notifications import services as notifications  # noqa: PLC0415

    with transaction.atomic():
        notifications.enqueue(user, "sharing_warning", {})
        _note_action(user, "warn", actor)
        audit.record("customer.risk_warn", actor=actor, target=user, ip=ip)


def reset_credentials(user: User, *, actor: User | None, ip: str | None) -> int:
    """Step 2: every IPTV app login gets a password nobody knows; its streams stop.

    The customer makes new logins in the portal (Devices & TV apps); the message says so.
    Returns how many logins were reset."""
    from apps.notifications import services as notifications  # noqa: PLC0415

    count = 0
    with transaction.atomic():
        devices = Device.objects.filter(
            user=user, kind=DeviceKind.XTREAM, revoked_at__isnull=True
        ).select_related("credential")
        for device in devices:
            accounts.reset_credential(device, actor=actor, ip=ip, notify=False)
            count += 1
        notifications.enqueue(user, "sharing_reset", {"count": count})
        _note_action(user, "reset", actor)
        audit.record(
            "customer.risk_reset", actor=actor, target=user, after={"logins": count}, ip=ip
        )
    return count


def suspend(user: User, *, actor: User | None, ip: str | None) -> None:
    """Step 3: suspend the account (playback stops; the customer can still sign in)."""
    with transaction.atomic():
        accounts.suspend_customer(user, actor=actor, ip=ip, reason="sharing")
        _note_action(user, "suspend", actor)


# --- GeoIP database health --------------------------------------------------------------------


def check_geoip(now: datetime | None = None) -> bool:
    """Tell the admins (once) while lookups are on but the country database is missing,
    and again when it is back. Returns True when a message was sent."""
    moment = now or timezone.now()
    missing = geoip.enabled() and not geoip.available("country")
    # Leave a new installation an hour to download it before saying anything.
    if missing and moment - _first_check(moment) < timedelta(hours=1):
        return False
    key = "antisharing:alert:GeoIpDatabaseMissing"
    try:
        firing = bool(state_redis().exists(key))
        if firing == missing:
            return False
        if missing:
            state_redis().set(key, moment.isoformat())
        else:
            state_redis().delete(key)
    except redis.RedisError:
        return False
    _send_alert(
        "GeoIpDatabaseMissing",
        status="firing" if missing else "resolved",
        summary="The IP geolocation database is missing"
        if missing
        else "The IP geolocation database is installed again",
        description="Country rules can't tell where clients are, and the anti-sharing score "
        "has no countries or networks. Settings → Integrations → IP geolocation: Update now "
        "(the worker needs internet access to download.db-ip.com).",
        anchor="geoipdatabasemissing",
    )
    return True


def _send_alert(name: str, *, status: str, summary: str, description: str, anchor: str) -> None:
    from apps.notifications import alerts  # noqa: PLC0415 (notifications imports accounts)

    now = timezone.now().isoformat()
    alerts.deliver(
        {
            "status": status,
            "commonLabels": {"alertname": name, "severity": "warning"},
            "externalURL": "",
            "alerts": [
                {
                    "status": status,
                    "labels": {"alertname": name, "severity": "warning", "service": "geoip"},
                    "annotations": {
                        "summary": summary,
                        "description": description,
                        "runbook": f"docs/runbooks/alerts.md#{anchor}",
                    },
                    "startsAt": now,
                    "endsAt": now if status == "resolved" else "",
                    "generatorURL": "",
                }
            ],
        }
    )


def _first_check(moment: datetime) -> datetime:
    key = "antisharing:geoip:first-check"
    try:
        client = state_redis()
        client.set(key, moment.isoformat(), nx=True)
        raw = cast("bytes | None", client.get(key))
        return datetime.fromisoformat(raw.decode()) if raw else moment
    except (redis.RedisError, ValueError):
        return moment
