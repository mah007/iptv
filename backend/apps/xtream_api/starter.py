"""The PlaybackStarter over `playback.services.start_playback` (SPEC §7.4, §7.5).

A play URL's title (already resolved by the catalog source, so it exists) becomes
a `PlayableTitle` through `catalog.playable.playable_title`, which answers None
while no rendition plays yet: that is TITLE_PREPARING (503, retry later). Then
`start_playback` runs the entitlement checks in SPEC §7.4 order and takes the
slot; its denial code becomes the refusal, its grant the 302.

`.m3u8` (and `output=hls` playlists) asks for HLS; every other extension for MP4.
"""

import logging
from collections.abc import Callable

import redis

from apps.core.errors import ErrorCode
from apps.playback import concurrency
from apps.playback.models import TitleKind as PlaybackKind
from apps.playback.services import PlayableTitle, PlaybackDenied, Prefer, start_playback
from apps.xtream_api.playback import PlayOutcome, PlayRefused, PlayRequest, PlayStarted

logger = logging.getLogger(__name__)

HLS_EXTENSIONS = frozenset({"m3u8"})

type TitleLookup = Callable[[PlaybackKind, int], PlayableTitle | None]


def catalog_lookup(kind: PlaybackKind, xc_id: int) -> PlayableTitle | None:
    """`catalog.playable.playable_title`, imported when first used: the catalog app
    imports this app's cache, so importing it at app loading could cycle."""
    from apps.catalog.playable import playable_title  # noqa: PLC0415

    return playable_title(kind, xc_id)


class DjangoPlaybackStarter:
    """PlaybackStarter over the playback service (installed in XtreamApiConfig.ready)."""

    def __init__(self, lookup: TitleLookup = catalog_lookup) -> None:
        self.lookup = lookup

    def start(self, request: PlayRequest) -> PlayOutcome:
        kind = PlaybackKind(request.title.kind.value)
        try:
            title = self.lookup(kind, request.title.xc_id)
            if title is None:
                return PlayRefused(ErrorCode.TITLE_PREPARING)
            prefer = Prefer.HLS if request.extension in HLS_EXTENSIONS else Prefer.MP4
            grant = start_playback(
                request.user,
                request.device,
                title,
                prefer=prefer,
                client_ip=request.client_ip,
                user_agent=request.user_agent,
            )
        except PlaybackDenied as denied:
            return PlayRefused(denied.denial.value)
        except redis.RedisError:
            logger.exception("redis-state unavailable; playback refused")
            return PlayRefused(ErrorCode.INTERNAL_ERROR)
        return PlayStarted(grant.url)

    def active_streams(self, user_id: str) -> int:
        try:
            return concurrency.active_streams(user_id)
        except redis.RedisError:
            logger.warning("redis-state unavailable; active_cons reported as 0")
            return 0
