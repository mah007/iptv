"""Live TV in the customer API: the portal's Live page (Q1, ADR-0023).

- `GET live/channels`: the live groups and channels the customer may watch, each with
  what is on now and next from the guide, its catch-up window and whether a browser can
  play it. Three queries whatever the number of channels.
- `GET live/channels/{id}/guide`: one channel's programmes around now; those inside the
  recorded archive can be watched again (catch-up).

Playback goes through `playback/start` with `title_type` `live` or `catchup`
(`apps.engagement.api`). What a customer may see follows the catalogue's rules
(ADR-0013): the plan's `allow_live`, the granted categories, adult groups only when
granted; `visible_in_xtream` is the IPTV apps' switch and is not applied here, as for
movies and series.
"""

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Final
from uuid import UUID

from django.db.models import Q, QuerySet
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.response import Response

from apps.catalog.api_customer import CatalogView, not_found
from apps.catalog.browse import CustomerScope
from apps.catalog.i18n import Locale
from apps.catalog.models import Category, CategoryKind
from apps.core.schema import problems
from apps.live import state
from apps.live.models import EpgChannel, EpgProgram, LiveChannel
from apps.live.xtream import listing_id, logo_url, resolve_guides

#: The guide a channel page shows by default: 6 hours back, a day ahead.
GUIDE_BACK: Final = timedelta(hours=6)
GUIDE_AHEAD: Final = timedelta(hours=24)
#: The widest window one guide request may ask for.
GUIDE_MAX: Final = timedelta(days=8)
#: Codecs a browser plays (Shaka's MPEG-TS transmuxer: H.264 with AAC or MP3).
WEB_VIDEO: Final = frozenset({"h264"})
WEB_AUDIO: Final = frozenset({"aac", "mp3"})


def visible_channels(scope: CustomerScope, now: datetime) -> QuerySet[LiveChannel]:
    """The channels a customer may watch: enabled, licensed, in a live group they may
    browse (adult groups only when granted)."""
    if scope.nothing or not scope.allow_live:
        return LiveChannel.objects.none()
    channels = LiveChannel.objects.filter(enabled=True, group__kind=CategoryKind.LIVE).filter(
        Q(license_expires_at__isnull=True) | Q(license_expires_at__gt=now)
    )
    if scope.categories is None:
        return channels.filter(group__is_adult=False)
    return channels.filter(group_id__in=scope.categories)


def web_playable(channel: LiveChannel) -> bool:
    """False only when the probe says a browser can't decode it (a TV app can)."""
    if channel.transcode == "h264":
        return True
    probe = channel.probe if isinstance(channel.probe, dict) else {}
    video = probe.get("video") if isinstance(probe.get("video"), dict) else {}
    audio = probe.get("audio") if isinstance(probe.get("audio"), dict) else {}
    video_codec = str((video or {}).get("codec") or "")
    audio_codec = str((audio or {}).get("codec") or "")
    return (not video_codec or video_codec in WEB_VIDEO) and (
        not audio_codec or audio_codec in WEB_AUDIO
    )


def _group_name(group: Category, locale: Locale) -> str:
    return (group.name_ar if locale == "ar" and group.name_ar else group.name_en) or group.slug


def _text(row: EpgProgram, field: str, locale: Locale) -> str:
    plain = str(getattr(row, field, "") or "")
    if locale == "ar":
        return str(getattr(row, f"{field}_ar", "") or "") or plain
    return plain


def _recorded(
    row: EpgProgram, archive: state.ArchiveWindow | None, catchup_days: int, now: datetime
) -> bool:
    """Whether the programme lies inside the recorded catch-up window."""
    return (
        archive is not None
        and catchup_days > 0
        and row.stop <= now
        and row.start >= now - timedelta(days=catchup_days)
        and row.start >= archive.first
        and row.stop <= archive.last
    )


def _programme(
    row: EpgProgram, guide: EpgChannel, locale: Locale, *, catchup: bool = False
) -> dict[str, Any]:
    return {
        "id": str(listing_id(guide.source.epg_id, guide.xmltv_id, row.start)),
        "title": _text(row, "title", locale),
        "description": _text(row, "description", locale),
        "start": row.start,
        "stop": row.stop,
        "catchup": catchup,
    }


def _channel(
    channel: LiveChannel, locale: Locale, archive: state.ArchiveWindow | None
) -> dict[str, Any]:
    name = channel.name_ar if locale == "ar" and channel.name_ar else channel.name
    return {
        "id": channel.pk,
        "number": channel.xc_id,
        "name": name,
        "logo_url": logo_url(channel.logo) or None,
        "group_id": channel.group_id,
        "catchup_days": channel.catchup_days,
        "archive_from": archive.first if archive is not None else None,
        "web_playable": web_playable(channel),
        "now": None,
        "next": None,
    }


def _archive(channel: LiveChannel) -> state.ArchiveWindow | None:
    return state.archive_window(channel.storage_key) if channel.catchup_days > 0 else None


# --- Serializers ----------------------------------------------------------------------------------


class ProgrammeSerializer(serializers.Serializer[Any]):
    id = serializers.CharField(help_text="Stable across guide imports.")
    title = serializers.CharField(allow_blank=True)
    description = serializers.CharField(allow_blank=True)
    start = serializers.DateTimeField()
    stop = serializers.DateTimeField()
    catchup = serializers.BooleanField(help_text="Recorded: it can be watched again now.")


class LiveGroupSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    name = serializers.CharField()


class LiveChannelSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    number = serializers.IntegerField(help_text="The channel's number in IPTV apps.")
    name = serializers.CharField()
    logo_url = serializers.URLField(allow_null=True)
    group_id = serializers.UUIDField()
    catchup_days = serializers.IntegerField(help_text="0: no catch-up.")
    archive_from = serializers.DateTimeField(
        allow_null=True, help_text="The oldest moment recorded, when catch-up records."
    )
    web_playable = serializers.BooleanField(
        help_text="False when its codecs need a TV app (a browser can't decode them)."
    )
    now = ProgrammeSerializer(allow_null=True)
    next = ProgrammeSerializer(allow_null=True)


class LiveListingSerializer(serializers.Serializer[Any]):
    groups = LiveGroupSerializer(many=True)
    channels = LiveChannelSerializer(many=True)


class LiveGuideSerializer(serializers.Serializer[Any]):
    channel = LiveChannelSerializer()
    programmes = ProgrammeSerializer(many=True)


# --- Views ----------------------------------------------------------------------------------------


class LiveChannelListView(CatalogView):
    @extend_schema(
        operation_id="live_channels_list",
        summary="Live channels I may watch, by group, with what's on now and next",
        responses={200: LiveListingSerializer, **problems(401, 403)},
    )
    def get(self, request: Request) -> Response:
        now = timezone.now()
        locale = self.locale()
        channels = list(
            visible_channels(self.scope(), now)
            .select_related("group")
            .order_by("group__sort", "sort", "xc_id")
        )
        groups: dict[UUID, dict[str, Any]] = {}
        rows = []
        guides = resolve_guides(channels)
        for channel in channels:
            groups.setdefault(
                channel.group_id,
                {"id": channel.group_id, "name": _group_name(channel.group, locale)},
            )
            rows.append(_channel(channel, locale, _archive(channel)))
        _now_and_next(rows, channels, guides, locale, now)
        body = {"groups": list(groups.values()), "channels": rows}
        response = Response(LiveListingSerializer(body).data)
        response["Cache-Control"] = "private, max-age=30"
        return response


def _now_and_next(
    rows: list[dict[str, Any]],
    channels: list[LiveChannel],
    guides: dict[str, EpgChannel],
    locale: Locale,
    now: datetime,
) -> None:
    """Fill each row's `now` and `next` (one query for every channel)."""
    if not guides:
        return
    programmes = EpgProgram.objects.filter(
        channel_id__in={guide.pk for guide in guides.values()},
        start__gte=now - timedelta(days=1),  # partition pruning
        start__lt=now + GUIDE_AHEAD,
        stop__gt=now,
    ).order_by("channel_id", "start")
    by_guide: defaultdict[object, list[EpgProgram]] = defaultdict(list)
    for programme in programmes:
        if len(by_guide[programme.channel_id]) < 2:  # now and next
            by_guide[programme.channel_id].append(programme)
    for row, channel in zip(rows, channels, strict=True):
        guide = guides.get(channel.storage_key)
        if guide is None:
            continue
        upcoming = by_guide.get(guide.pk, [])
        current = upcoming[0] if upcoming and upcoming[0].start <= now else None
        later = [item for item in upcoming if item is not current]
        if current is not None:
            row["now"] = _programme(current, guide, locale)
        if later:
            row["next"] = _programme(later[0], guide, locale)


class LiveChannelGuideView(CatalogView):
    @extend_schema(
        operation_id="live_channels_guide",
        summary="One channel's guide; recorded programmes can be watched again",
        parameters=[
            OpenApiParameter("from", datetime, description="Start (default: 6 hours ago)."),
            OpenApiParameter("to", datetime, description="End (default: a day ahead)."),
        ],
        responses={200: LiveGuideSerializer, **problems(400, 401, 403, 404)},
    )
    def get(self, request: Request, pk: UUID) -> Response:
        now = timezone.now()
        locale = self.locale()
        channel = visible_channels(self.scope(), now).select_related("group").filter(pk=pk).first()
        if channel is None:
            raise not_found("channel")
        window = _GuideWindowSerializer(data=request.query_params)
        window.is_valid(raise_exception=True)
        start = window.validated_data.get("from") or now - GUIDE_BACK
        end = window.validated_data.get("to") or now + GUIDE_AHEAD
        end = min(end, start + GUIDE_MAX)
        archive = _archive(channel)
        guide = resolve_guides([channel]).get(channel.storage_key)
        programmes: list[dict[str, Any]] = []
        if guide is not None:
            rows = EpgProgram.objects.filter(
                channel=guide,
                start__gte=start - timedelta(days=1),
                start__lt=end,
                stop__gt=start,
            ).order_by("start")[:500]
            programmes = [
                _programme(
                    row,
                    guide,
                    locale,
                    catchup=_recorded(row, archive, channel.catchup_days, now),
                )
                for row in rows
            ]
        body = {"channel": _channel(channel, locale, archive), "programmes": programmes}
        response = Response(LiveGuideSerializer(body).data)
        response["Cache-Control"] = "private, max-age=30"
        return response


class _GuideWindowSerializer(serializers.Serializer[Any]):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # "from" is a Python keyword: declared here instead of as a class attribute.
        self.fields["from"] = serializers.DateTimeField(required=False)
        self.fields["to"] = serializers.DateTimeField(required=False)
