"""The Xtream `LiveSource` over the live models (SPEC §7.5, ADR-0017).

Installed by `LiveConfig.ready()`. Each call runs a fixed number of queries
whatever the number of channels (asserted in tests); responses are cached by the
Xtream layer anyway.

A channel is visible when the scope allows live TV and the channel is enabled,
licensed (no expiry, or an expiry ahead), in a live category shown in Xtream and,
when the scope lists categories, in one of them.

Programme listing ids are stable across imports: 52 bits of a hash of the source,
the XMLTV id and the start (`listing_id`).
"""

import hashlib
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta

from django.db.models import Q, QuerySet
from django.utils import timezone

from apps.catalog.models import Category, CategoryKind
from apps.catalog.services import media_url
from apps.live import state
from apps.live.models import ChannelOutput, EpgChannel, EpgProgram, LiveChannel
from apps.xtream_api.dto import (
    CategoryItem,
    ChannelGuide,
    ChannelItem,
    Programme,
    Text,
    TitleKind,
    TitleRef,
)
from apps.xtream_api.source import CatalogScope

#: Most programmes one channel's guide returns (get_simple_data_table, xmltv.php).
MAX_PROGRAMMES_PER_CHANNEL = 2000
LOGO_SIZES = ("w185", "w500")
_ID_BITS = 52


def listing_id(epg_id: int, xmltv_id: str, start: datetime) -> int:
    digest = hashlib.sha256(f"{epg_id}|{xmltv_id}|{int(start.timestamp())}".encode()).digest()
    return (int.from_bytes(digest[:8], "big") >> (64 - _ID_BITS)) or 1


def logo_url(logo: object) -> str:
    stored = logo if isinstance(logo, dict) else {}
    for size in LOGO_SIZES:
        formats = stored.get(size)
        key = formats.get("webp") if isinstance(formats, dict) else None
        if isinstance(key, str) and key:
            return media_url(key)
    return ""


def visible_channels(scope: CatalogScope, now: datetime | None = None) -> QuerySet[LiveChannel]:
    if not scope.allow_live:
        return LiveChannel.objects.none()
    moment = now or timezone.now()
    channels = LiveChannel.objects.filter(
        enabled=True, group__kind=CategoryKind.LIVE, group__visible_in_xtream=True
    ).filter(Q(license_expires_at__isnull=True) | Q(license_expires_at__gt=moment))
    if scope.categories is not None:
        channels = channels.filter(group_id__in=scope.categories)
    return channels


def _item(channel: LiveChannel) -> ChannelItem:
    return ChannelItem(
        xc_id=channel.xc_id,
        name=Text(channel.name, channel.name_ar),
        added=channel.created_at,
        category_ids=(channel.group.xc_id,),
        sort=channel.sort,
        logo=logo_url(channel.logo),
        epg_channel_id=channel.epg_channel_id,
        catchup_days=channel.catchup_days,
        is_adult=channel.group.is_adult,
    )


def resolve_guides(channels: Iterable[LiveChannel]) -> dict[str, EpgChannel]:
    """Each channel's guide channel (by channel key): its own source when it names one,
    else the enabled source with the lowest priority that has the XMLTV id. One query."""
    wanted = [channel for channel in channels if channel.epg_channel_id]
    if not wanted:
        return {}
    candidates = (
        EpgChannel.objects.filter(
            xmltv_id__in={channel.epg_channel_id for channel in wanted}, source__enabled=True
        )
        .select_related("source")
        .order_by("source__priority", "source__created_at")
    )
    by_id: defaultdict[str, list[EpgChannel]] = defaultdict(list)
    for candidate in candidates:
        by_id[candidate.xmltv_id].append(candidate)
    resolved = {}
    for channel in wanted:
        options = by_id.get(channel.epg_channel_id, [])
        if channel.epg_source_id is not None:
            options = [item for item in options if item.source_id == channel.epg_source_id]
        if options:
            resolved[channel.storage_key] = options[0]
    return resolved


def _programme(row: EpgProgram, guide: EpgChannel) -> Programme:
    return Programme(
        listing_id=listing_id(guide.source.epg_id, guide.xmltv_id, row.start),
        epg_id=guide.source.epg_id,
        start=row.start,
        stop=row.stop,
        title=Text(
            row.title if row.lang != "ar" else "",
            row.title_ar or (row.title if row.lang == "ar" else ""),
        ),
        description=Text(
            row.description if row.lang != "ar" else "",
            row.description_ar or (row.description if row.lang == "ar" else ""),
        ),
        lang=row.lang if row.lang != "ar" else "",
    )


class DjangoLiveSource:
    """`LiveSource` over `apps.live.models` (installed in LiveConfig.ready)."""

    def categories(self, scope: CatalogScope) -> Sequence[CategoryItem]:
        groups = (
            Category.objects.filter(pk__in=visible_channels(scope).values("group_id"))
            .order_by("sort", "xc_id")
            .only("xc_id", "name_en", "name_ar", "sort")
        )
        return [
            CategoryItem(
                xc_id=group.xc_id, name=Text(group.name_en, group.name_ar), sort=group.sort
            )
            for group in groups
        ]

    def channels(self, scope: CatalogScope) -> Sequence[ChannelItem]:
        rows = visible_channels(scope).select_related("group").order_by("sort", "xc_id")
        return [_item(channel) for channel in rows]

    def guide(
        self, scope: CatalogScope, xc_id: int, *, start: datetime, end: datetime
    ) -> ChannelGuide | None:
        channel = visible_channels(scope).select_related("group").filter(xc_id=xc_id).first()
        if channel is None:
            return None
        guide = resolve_guides([channel]).get(channel.storage_key)
        programmes: list[Programme] = []
        if guide is not None:
            rows = EpgProgram.objects.filter(
                channel=guide,
                start__gte=start - timedelta(days=1),  # partition pruning
                start__lt=end,
                stop__gt=start,
            ).order_by("start")[:MAX_PROGRAMMES_PER_CHANNEL]
            programmes = [_programme(row, guide) for row in rows]
        window = state.archive_window(channel.storage_key) if channel.catchup_days else None
        return ChannelGuide(
            channel=_item(channel),
            programmes=tuple(programmes),
            archive_from=window.first if window else None,
        )

    def guides(self, scope: CatalogScope, *, now: datetime) -> Sequence[ChannelGuide]:
        channels = list(
            visible_channels(scope, now)
            .exclude(epg_channel_id="")
            .select_related("group")
            .order_by("sort", "xc_id")
        )
        resolved = resolve_guides(channels)
        if not resolved:
            return [ChannelGuide(channel=_item(channel), programmes=()) for channel in channels]
        since = {
            channel.storage_key: now - timedelta(days=channel.catchup_days) for channel in channels
        }
        earliest = min(since.values())
        rows = EpgProgram.objects.filter(
            channel_id__in={guide.pk for guide in resolved.values()},
            start__gte=earliest - timedelta(days=1),
            stop__gt=earliest,
        ).order_by("channel_id", "start")
        by_guide: defaultdict[object, list[EpgProgram]] = defaultdict(list)
        for row in rows:
            if len(by_guide[row.channel_id]) < MAX_PROGRAMMES_PER_CHANNEL:
                by_guide[row.channel_id].append(row)
        result = []
        for channel in channels:
            guide = resolved.get(channel.storage_key)
            items = by_guide.get(guide.pk, []) if guide else []
            cutoff = since[channel.storage_key]
            result.append(
                ChannelGuide(
                    channel=_item(channel),
                    programmes=tuple(
                        _programme(row, guide) for row in items if guide and row.stop > cutoff
                    ),
                )
            )
        return result

    def channel(self, xc_id: int) -> TitleRef | None:
        channel = (
            LiveChannel.objects.filter(xc_id=xc_id, enabled=True)
            .only("id", "xc_id", "output")
            .first()
        )
        if channel is None:
            return None
        output = "m3u8" if channel.output == ChannelOutput.HLS else "ts"
        return TitleRef(kind=TitleKind.LIVE, xc_id=channel.xc_id, id=str(channel.pk), output=output)
