"""Admin operations on live channels and guide sources (ADR-0017).

Every change is audited, retires the Xtream response cache after commit, and stops
the sessions it takes away: a disabled or deleted channel, or one whose licence ran
out. Snapshots never hold a source URL: only its scheme and host, and whether it
changed.
"""

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any, Final
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.audit import services as audit
from apps.catalog.models import Category, CategoryKind
from apps.core.errors import ErrorCode, ProblemError, field_error
from apps.core.services import get_setting
from apps.live import epg, sources
from apps.live.models import EpgSource, EpgSourceKind, LiveChannel
from apps.playback.concurrency import KickReason
from apps.playback.models import PlaybackSession, TitleKind
from apps.playback.services import open_sessions, stop_sessions
from apps.xtream_api.cache import invalidate_on_commit

CHANNEL_FIELDS: Final = (
    "name",
    "name_ar",
    "group",
    "sort",
    "epg_channel_id",
    "epg_source",
    "output",
    "transcode",
    "catchup_days",
    "always_on",
    "enabled",
    "rights_holder",
    "license_ref",
    "license_expires_at",
)
_SORT_STEP = 10
LIVE_TITLE_KINDS: Final = (TitleKind.LIVE, TitleKind.CATCHUP)


def channel_state(channel: LiveChannel) -> dict[str, Any]:
    """What the audit log keeps of a channel (never the source URL)."""
    source = sources.describe_encrypted(channel.source_encrypted)
    return {
        "name": channel.name,
        "name_ar": channel.name_ar,
        "group": str(channel.group_id) if channel.group_id else None,
        "sort": channel.sort,
        "epg_channel_id": channel.epg_channel_id,
        "epg_source": str(channel.epg_source_id) if channel.epg_source_id else None,
        "source": f"{source.scheme}://{source.host}" if source else None,
        "output": channel.output,
        "transcode": channel.transcode,
        "catchup_days": channel.catchup_days,
        "always_on": channel.always_on,
        "enabled": channel.enabled,
        "rights_holder": channel.rights_holder,
        "license_ref": channel.license_ref,
        "license_expires_at": channel.license_expires_at,
        "origin": channel.origin,
    }


def _check_catchup(days: int) -> None:
    limit = int(get_setting("live.catchup_max_days"))
    if days > limit:
        raise ProblemError(
            ErrorCode.VALIDATION_ERROR,
            f"Catch-up is limited to {limit} days.",
            field_errors={
                "catchup_days": [field_error(f"At most {limit} days.", code="max_value")]
            },
        )


def _validate(channel: LiveChannel) -> None:
    try:
        channel.full_clean(exclude=("source_encrypted",))
    except ValidationError as exc:
        raise ProblemError(
            ErrorCode.VALIDATION_ERROR,
            "The channel is not valid.",
            field_errors={
                name: [
                    field_error(str(error.messages[0]), code=error.code or "invalid")
                    for error in errors
                ]
                for name, errors in exc.error_dict.items()
            },
        ) from None


def _channel_sessions(channels: Iterable[LiveChannel]) -> Any:
    return open_sessions().filter(
        title_kind__in=LIVE_TITLE_KINDS, title_id__in=[channel.pk for channel in channels]
    )


def stop_channel_sessions(channels: Iterable[LiveChannel], reason: KickReason) -> int:
    """Stop every open live or catch-up session of these channels."""
    return len(stop_sessions(_channel_sessions(list(channels)), reason))


def _after_commit_stop(channels: Sequence[LiveChannel], reason: KickReason) -> None:
    frozen = list(channels)
    transaction.on_commit(lambda: stop_channel_sessions(frozen, reason), robust=True)


def _next_sort(group_id: UUID) -> int:
    last = LiveChannel.objects.filter(group_id=group_id).order_by("-sort").first()
    return (last.sort if last else 0) + _SORT_STEP


def create_channel(
    data: Mapping[str, Any], *, source_url: str, actor: User | None, ip: str | None
) -> LiveChannel:
    values = {name: data[name] for name in CHANNEL_FIELDS if name in data}
    _check_catchup(int(values.get("catchup_days") or 0))
    channel = LiveChannel(**values)
    channel.source_encrypted = sources.encrypt(sources.validate_source_url(source_url))
    if "sort" not in values and channel.group_id is not None:
        channel.sort = _next_sort(channel.group_id)
    with transaction.atomic():
        _validate(channel)
        channel.save()
        audit.record(
            "live.channel.create", actor=actor, target=channel, after=channel_state(channel), ip=ip
        )
        invalidate_on_commit()
    return channel


def update_channel(
    channel: LiveChannel,
    data: Mapping[str, Any],
    *,
    source_url: str | None = None,
    actor: User | None,
    ip: str | None,
) -> LiveChannel:
    before = channel_state(channel)
    was_enabled = channel.enabled
    old_epg = (channel.epg_channel_id, channel.epg_source_id)
    if "catchup_days" in data:
        _check_catchup(int(data["catchup_days"] or 0))
    with transaction.atomic():
        for name in CHANNEL_FIELDS:
            if name in data:
                setattr(channel, name, data[name])
        if channel.license_expires_at is None or channel.license_expires_at > timezone.now():
            channel.license_lapsed = False
        source_changed = False
        if source_url is not None:
            channel.source_encrypted = sources.encrypt(sources.validate_source_url(source_url))
            channel.probe = {}
            source_changed = True
        _validate(channel)
        channel.save()
        after = channel_state(channel)
        if source_changed:
            after["source_changed"] = True
        audit.record(
            "live.channel.update", actor=actor, target=channel, before=before, after=after, ip=ip
        )
        invalidate_on_commit()
        if was_enabled and not channel.enabled:
            _after_commit_stop([channel], KickReason.KICKED)
        if (channel.epg_channel_id, channel.epg_source_id) != old_epg and channel.epg_channel_id:
            _refresh_guides_for(channel)
    return channel


def delete_channel(channel: LiveChannel, *, actor: User | None, ip: str | None) -> None:
    with transaction.atomic():
        audit.record(
            "live.channel.delete", actor=actor, target=channel, before=channel_state(channel), ip=ip
        )
        stop_channel_sessions([channel], KickReason.KICKED)
        channel.delete()
        invalidate_on_commit()


def set_enabled(
    ids: Sequence[UUID], enabled: bool, *, actor: User | None, ip: str | None
) -> list[LiveChannel]:
    """Enable or disable several channels. Enabling needs `rights_holder` on each."""
    with transaction.atomic():
        channels = list(LiveChannel.objects.select_for_update().filter(pk__in=ids))
        if len(channels) != len(set(ids)):
            raise ProblemError(ErrorCode.NOT_FOUND, "Some channels do not exist.")
        if enabled:
            missing = [str(channel.pk) for channel in channels if not channel.rights_holder.strip()]
            if missing:
                raise ProblemError(
                    ErrorCode.VALIDATION_ERROR,
                    "Channels need a rights holder before they can be enabled.",
                    field_errors={
                        "ids": [field_error("A rights holder is required.", code="rights_required")]
                    },
                )
        changed = [channel for channel in channels if channel.enabled != enabled]
        now = timezone.now()
        for channel in changed:
            channel.enabled = enabled
            channel.updated_at = now
        LiveChannel.objects.bulk_update(changed, ["enabled", "updated_at"])
        for channel in changed:
            audit.record(
                "live.channel.enable" if enabled else "live.channel.disable",
                actor=actor,
                target=channel,
                before={"enabled": not enabled},
                after={"enabled": enabled},
                ip=ip,
            )
        invalidate_on_commit()
        if not enabled and changed:
            _after_commit_stop(changed, KickReason.KICKED)
    return channels


def reorder_channels(
    group: Category, ids: Sequence[UUID], *, actor: User | None, ip: str | None
) -> list[LiveChannel]:
    """Give the group's channels the order of `ids`; the rest follow in their order."""
    if group.kind != CategoryKind.LIVE:
        raise ProblemError(ErrorCode.VALIDATION_ERROR, "Not a live group.")
    with transaction.atomic():
        channels = list(
            LiveChannel.objects.select_for_update().filter(group=group).order_by("sort", "xc_id")
        )
        by_id = {channel.pk: channel for channel in channels}
        unknown = [str(pk) for pk in ids if pk not in by_id]
        if unknown:
            raise ProblemError(
                ErrorCode.VALIDATION_ERROR,
                "Some channels are not in this group.",
                field_errors={"ids": [field_error("Not in this group.", code="invalid")]},
            )
        listed = list(dict.fromkeys(ids))
        ordered = [by_id[pk] for pk in listed] + [c for c in channels if c.pk not in set(listed)]
        before = [str(channel.pk) for channel in channels]
        now = timezone.now()
        for index, channel in enumerate(ordered, start=1):
            channel.sort = index * _SORT_STEP
            channel.updated_at = now
        LiveChannel.objects.bulk_update(ordered, ["sort", "updated_at"])
        audit.record(
            "live.channel.reorder",
            actor=actor,
            target=group,
            before={"order": before},
            after={"order": [str(channel.pk) for channel in ordered]},
            ip=ip,
        )
        invalidate_on_commit()
    return ordered


# --- Licences --------------------------------------------------------------------------------


def enforce_licences(now: datetime | None = None) -> int:
    """Beat, every 5 minutes: channels whose licence just ran out disappear from Xtream
    (the cache is retired) and their sessions stop (`license_expired`). Returns how many."""
    moment = now or timezone.now()
    lapsed = list(LiveChannel.objects.filter(license_expires_at__lte=moment, license_lapsed=False))
    if not lapsed:
        return 0
    with transaction.atomic():
        LiveChannel.objects.filter(pk__in=[channel.pk for channel in lapsed]).update(
            license_lapsed=True, updated_at=moment
        )
        for channel in lapsed:
            audit.record(
                "live.channel.license_expired",
                actor=None,
                target=channel,
                after={"license_expires_at": channel.license_expires_at},
            )
        invalidate_on_commit()
    stop_channel_sessions(lapsed, KickReason.LICENSE_EXPIRED)
    return len(lapsed)


# --- Guide sources ---------------------------------------------------------------------------

EPG_SOURCE_FIELDS: Final = ("name", "refresh_cron", "priority", "enabled")


def epg_source_state(source: EpgSource) -> dict[str, Any]:
    url = sources.describe_encrypted(source.url_encrypted)
    return {
        "name": source.name,
        "kind": source.kind,
        "url": f"{url.scheme}://{url.host}" if url else None,
        "upload_name": source.upload_name,
        "refresh_cron": source.refresh_cron,
        "priority": source.priority,
        "enabled": source.enabled,
    }


def _clean_source(source: EpgSource) -> None:
    try:
        source.refresh_cron = epg.validate_cron(source.refresh_cron)
    except ValidationError as exc:
        raise ProblemError(
            ErrorCode.VALIDATION_ERROR,
            "The schedule is not valid.",
            field_errors={"refresh_cron": [field_error(exc.messages[0], code="invalid_cron")]},
        ) from None


def _set_url(source: EpgSource, url: str) -> None:
    try:
        source.url_encrypted = sources.encrypt(sources.validate_http_url(url))
    except ValidationError as exc:
        raise ProblemError(
            ErrorCode.VALIDATION_ERROR,
            "The URL is not valid.",
            field_errors={"url": [field_error(exc.messages[0], code="invalid_url")]},
        ) from None


def create_epg_source(
    data: Mapping[str, Any],
    *,
    url: str | None,
    upload: bytes | None,
    upload_name: str = "",
    actor: User | None,
    ip: str | None,
) -> EpgSource:
    source = EpgSource(**{name: data[name] for name in EPG_SOURCE_FIELDS if name in data})
    if upload is not None:
        source.kind = EpgSourceKind.UPLOAD
        source.upload = epg.compress_upload(upload)
        source.upload_name = upload_name[:255]
    elif url:
        source.kind = EpgSourceKind.URL
        _set_url(source, url)
    else:
        raise ProblemError(
            ErrorCode.VALIDATION_ERROR,
            "Give a URL or upload a file.",
            field_errors={"url": [field_error("A URL or a file is required.", code="required")]},
        )
    _clean_source(source)
    with transaction.atomic():
        source.save()
        audit.record(
            "live.epg_source.create",
            actor=actor,
            target=source,
            after=epg_source_state(source),
            ip=ip,
        )
    schedule_import(source)
    return source


def update_epg_source(  # noqa: PLR0913
    source: EpgSource,
    data: Mapping[str, Any],
    *,
    url: str | None = None,
    upload: bytes | None = None,
    upload_name: str = "",
    actor: User | None,
    ip: str | None,
) -> EpgSource:
    before = epg_source_state(source)
    for name in EPG_SOURCE_FIELDS:
        if name in data:
            setattr(source, name, data[name])
    reimport = False
    if upload is not None:
        source.kind, source.upload, source.upload_name = (
            EpgSourceKind.UPLOAD,
            epg.compress_upload(upload),
            upload_name[:255],
        )
        source.url_encrypted, source.etag, source.last_modified = "", "", ""
        reimport = True
    elif url:
        source.kind, source.upload, source.upload_name = EpgSourceKind.URL, None, ""
        _set_url(source, url)
        source.etag, source.last_modified = "", ""
        reimport = True
    _clean_source(source)
    with transaction.atomic():
        source.save()
        audit.record(
            "live.epg_source.update",
            actor=actor,
            target=source,
            before=before,
            after=epg_source_state(source),
            ip=ip,
        )
        invalidate_on_commit()
    if reimport:
        schedule_import(source)
    return source


def delete_epg_source(source: EpgSource, *, actor: User | None, ip: str | None) -> None:
    with transaction.atomic():
        audit.record(
            "live.epg_source.delete",
            actor=actor,
            target=source,
            before=epg_source_state(source),
            ip=ip,
        )
        source.delete()  # guide channels cascade, and their programmes in the database
        invalidate_on_commit()


def schedule_import(source: EpgSource) -> None:
    from apps.live.tasks import import_epg_source  # noqa: PLC0415 (tasks import services)

    source_id = str(source.pk)
    transaction.on_commit(lambda: import_epg_source.delay(source_id), robust=True)


def _refresh_guides_for(channel: LiveChannel) -> None:
    """A newly mapped XMLTV id: import the sources that may describe it."""
    candidates = EpgSource.objects.filter(enabled=True)
    if channel.epg_source_id is not None:
        candidates = candidates.filter(pk=channel.epg_source_id)
    else:
        candidates = candidates.filter(channels__xmltv_id=channel.epg_channel_id).distinct()
    for source in candidates:
        schedule_import(source)


def viewers_by_channel(ids: Iterable[UUID]) -> dict[UUID, int]:
    """Open live and catch-up sessions per channel (Postgres rows, admin views only)."""
    counts: dict[UUID, int] = {}
    rows = PlaybackSession.objects.filter(
        ended_at__isnull=True, title_kind__in=LIVE_TITLE_KINDS, title_id__in=list(ids)
    ).values_list("title_id", flat=True)
    for title_id in rows:
        counts[title_id] = counts.get(title_id, 0) + 1
    return counts
