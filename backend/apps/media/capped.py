"""Compat MP4s capped at a plan's ceiling (ADR-0023).

IPTV apps ask for `<id>.mp4` and play only progressive files (ExoPlayer, VLC). On a
plan capped at 720p (or 480p) the full compat MP4 is too tall, and an HLS redirect for
an `.mp4` request fails in those apps. So, for every ceiling that plans use, the ladder
rung that fits is remuxed (no encode: seconds per title) with the AAC audio renditions
and the text subtitles into `compat720.mp4` / `compat480.mp4`, a `compat_capped`
rendition named after its token rendition.

`library.capped_compat_mp4`: `auto` makes the ceilings an active plan, subscription or
access profile uses (480 and 720 only; a 1080 plan plays the compat MP4); `off` makes
none. A file gets one only when its compat MP4 is taller than the ceiling and its
ladder has a rung that fits.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final

from apps.core.services import get_setting
from apps.media import layout
from apps.media.models import Rendition, RenditionKind, RenditionStatus

#: The plan ceilings below the compat MP4's 1080p that can get a capped MP4.
CEILINGS: Final = layout.CAPPED_CEILINGS
AUTO: Final = "auto"
#: Audio a progressive MP4 carries everywhere.
MP4_AUDIO: Final = frozenset({"aac", "mp3"})


@dataclass(frozen=True, slots=True)
class Needed:
    ceiling: int
    rung: Rendition  # the ladder's tallest `hls_variant` that fits

    @property
    def name(self) -> str:
        return layout.capped_compat(self.ceiling)


def wanted_ceilings() -> tuple[int, ...]:
    """The ceilings plans use now (empty when the setting is `off`)."""
    if get_setting("library.capped_compat_mp4") != AUTO:
        return ()
    # Local imports: accounts and billing sit above media.
    from apps.accounts.models import CustomerAccess  # noqa: PLC0415
    from apps.billing.models import Plan, Subscription, SubscriptionStatus  # noqa: PLC0415

    used = set(
        CustomerAccess.objects.filter(max_quality__in=CEILINGS)
        .values_list("max_quality", flat=True)
        .distinct()
    )
    used |= set(
        Plan.objects.filter(active=True, max_quality__in=CEILINGS)
        .values_list("max_quality", flat=True)
        .distinct()
    )
    live = (SubscriptionStatus.PENDING, SubscriptionStatus.ACTIVE, SubscriptionStatus.GRACE)
    used |= {
        int(value)
        for value in Subscription.objects.filter(
            status__in=live, plan_snapshot__max_quality__in=list(CEILINGS)
        )
        .values_list("plan_snapshot__max_quality", flat=True)
        .distinct()
        if value is not None
    }
    return tuple(sorted(ceiling for ceiling in used if ceiling in CEILINGS))


def _box(row: Rendition) -> int:
    return int((row.details or {}).get("box_height") or row.height or 0)


def needed(rows: Iterable[Rendition], ceilings: Sequence[int]) -> list[Needed]:
    """What a file's ready renditions call for, per wanted ceiling."""
    ready = [row for row in rows if row.status == RenditionStatus.READY]
    ladder = [
        row for row in ready if row.kind == RenditionKind.HLS_VARIANT and row.name.startswith("v")
    ]
    progressive = [
        row.height or 0
        for row in ready
        if row.kind in (RenditionKind.COMPAT_MP4, RenditionKind.SOURCE)
    ]
    found = []
    for ceiling in ceilings:
        if progressive and min(progressive) <= ceiling:
            continue  # the compat MP4 (or the source) already fits
        fitting = [row for row in ladder if _box(row) <= ceiling]
        if fitting:
            found.append(Needed(ceiling, max(fitting, key=_box)))
    return found


def missing(rows: Iterable[Rendition], ceilings: Sequence[int]) -> list[Needed]:
    """The needed capped MP4s that aren't ready yet (or were made from another rung)."""
    rows = list(rows)
    have = {
        row.name: (row.details or {}).get("rung")
        for row in rows
        if row.kind == RenditionKind.COMPAT_CAPPED and row.status == RenditionStatus.READY
    }
    return [item for item in needed(rows, ceilings) if have.get(item.name) != item.rung.name]
