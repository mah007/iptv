"""A live channel, or a window of its catch-up, as playback needs it (ADR-0017).

The media token's `title` is the channel key; its rendition is `live` or `archive`:

- live: `live.ts` (the relay's continuous MPEG-TS) or `live/index.m3u8` (HLS);
- catch-up: `archive/<start>-<seconds>.<ts|m3u8>`, which the relay serves from the
  archive. The token's scope is the whole archive of the channel, which the customer
  may watch anyway; the window only says where to start and stop.

`height` is the channel's probed height (0 when unknown: allowed by any plan).
"""

from datetime import datetime, timedelta

from django.utils import timezone

from apps.core.services import get_setting
from apps.live import layout, state
from apps.live.models import LiveChannel
from apps.playback.models import TitleKind
from apps.playback.services import PlayableRendition, PlayableTitle, RenditionKind
from apps.xtream_api.dto import TimeshiftWindow

HLS_EXTENSIONS = frozenset({"m3u8", "hls"})


def _container(extension: str) -> str:
    return "m3u8" if extension.lower() in HLS_EXTENSIONS else "ts"


def playable_channel(
    xc_id: int,
    *,
    extension: str,
    window: TimeshiftWindow | None = None,
    now: datetime | None = None,
) -> PlayableTitle | None:
    """None for an unknown or disabled channel, or a catch-up window it cannot play."""
    channel = LiveChannel.objects.select_related("group").filter(xc_id=xc_id, enabled=True).first()
    if channel is None:
        return None
    container = _container(extension)
    key = channel.storage_key
    if window is None:
        kind, title_kind = RenditionKind.LIVE, TitleKind.LIVE
        path = layout.LIVE_PLAYLIST_TAIL if container == "m3u8" else layout.LIVE_TS_TAIL
    else:
        path_or_none = _catchup_path(channel, window, container, now or timezone.now())
        if path_or_none is None:
            return None
        kind, title_kind, path = RenditionKind.ARCHIVE, TitleKind.CATCHUP, path_or_none
    rendition = PlayableRendition(
        storage_key=key, kind=kind, height=channel.height, container=container, path=path
    )
    return PlayableTitle(
        kind=title_kind,
        id=channel.pk,
        renditions=(rendition,),
        category_ids=frozenset({channel.group_id}),
        adult=channel.group.is_adult,
        license_expires_at=channel.license_expires_at,
        name=channel.name,
    )


def _catchup_path(
    channel: LiveChannel, window: TimeshiftWindow, container: str, now: datetime
) -> str | None:
    if channel.catchup_days <= 0:
        return None
    longest = int(get_setting("live.timeshift_max_minutes"))
    if not 1 <= window.minutes <= longest:
        return None
    start = window.start
    if start >= now or start < now - timedelta(days=channel.catchup_days):
        return None
    archived = state.archive_window(channel.storage_key)
    end = start + timedelta(minutes=window.minutes)
    if archived is None or archived.first >= end or archived.last <= start:
        return None
    return layout.window_tail(int(start.timestamp()), window.minutes * 60, container)
