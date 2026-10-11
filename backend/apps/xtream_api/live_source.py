"""The live TV queries the Xtream API needs (SPEC §7.5, M12), behind one protocol.

`apps.live` installs the implementation over its models (`apps.live.xtream`) with
`set_live_source`; `EmptyLiveSource` is the default before that, and tests install
fakes. It mirrors `CatalogSource` (source.py), kept apart so the movie and series
fakes stay as they are.

Contract every implementation keeps:
- Only channels the scope may see: `allow_live`, enabled, licence not expired, in a
  live category that is visible in Xtream and allowed (all when `categories` is None).
- `category_ids` hold category `xc_id`s the same scope's `categories()` returns.
- `channel()` ignores the scope and the licence: play URLs resolve the id, then
  playback runs the SPEC §7.4 checks and answers with its own codes. It returns
  None only for unknown or disabled channels.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from apps.xtream_api.dto import CategoryItem, ChannelGuide, ChannelItem, TitleRef
from apps.xtream_api.source import CatalogScope


class LiveSource(Protocol):
    def categories(self, scope: CatalogScope) -> Sequence[CategoryItem]:
        """Live categories that hold at least one channel the scope may see."""
        ...

    def channels(self, scope: CatalogScope) -> Sequence[ChannelItem]:
        """Every channel the scope may see."""
        ...

    def guide(
        self, scope: CatalogScope, xc_id: int, *, start: datetime, end: datetime
    ) -> ChannelGuide | None:
        """One visible channel's programmes overlapping [start, end); None if not visible."""
        ...

    def guides(self, scope: CatalogScope, *, now: datetime) -> Sequence[ChannelGuide]:
        """xmltv.php: visible channels with a guide, from each one's catch-up window on."""
        ...

    def channel(self, xc_id: int) -> TitleRef | None:
        """The channel behind a play URL (kind LIVE, `output` its default extension)."""
        ...


class EmptyLiveSource:
    """Live TV before the live app is wired in: valid, and empty."""

    def categories(self, scope: CatalogScope) -> Sequence[CategoryItem]:
        return ()

    def channels(self, scope: CatalogScope) -> Sequence[ChannelItem]:
        return ()

    def guide(
        self, scope: CatalogScope, xc_id: int, *, start: datetime, end: datetime
    ) -> ChannelGuide | None:
        return None

    def guides(self, scope: CatalogScope, *, now: datetime) -> Sequence[ChannelGuide]:
        return ()

    def channel(self, xc_id: int) -> TitleRef | None:
        return None


_source: LiveSource = EmptyLiveSource()


def live_source() -> LiveSource:
    return _source


def set_live_source(source: LiveSource) -> LiveSource:
    """Install `source` (at startup, or in tests); returns the previous one."""
    global _source
    previous, _source = _source, source
    return previous
