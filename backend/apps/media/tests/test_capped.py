"""Which capped compat MP4s a file needs (ADR-0023), without ffmpeg."""

from typing import Any

import pytest

from apps.media import capped
from apps.media.models import Rendition, RenditionKind, RenditionStatus

READY = RenditionStatus.READY


def row(kind: str, name: str = "", height: int = 0, **details: Any) -> Rendition:
    return Rendition(kind=kind, name=name, height=height, status=READY, details=details)


LADDER = [
    row(RenditionKind.COMPAT_MP4, "compat", 1080),
    row(RenditionKind.HLS_VARIANT, "v0", 1080, box_height=1080),
    row(RenditionKind.HLS_VARIANT, "v1", 718, box_height=720),
    row(RenditionKind.HLS_VARIANT, "v2", 540, box_height=540),
    row(RenditionKind.HLS_VARIANT, "v3", 360, box_height=360),
    row(RenditionKind.HLS_VARIANT, "uhd", 2160, box_height=2160),
]


def test_each_ceiling_takes_the_tallest_rung_that_fits() -> None:
    found = capped.needed(LADDER, (480, 720))
    assert [(item.name, item.rung.name) for item in found] == [
        ("compat480", "v3"),
        ("compat720", "v1"),  # by its box: a 718-line rung of a 720p box
    ]


def test_nothing_when_the_compat_mp4_already_fits_or_no_rung_does() -> None:
    small = [row(RenditionKind.COMPAT_MP4, "compat", 720), *LADDER[1:]]
    assert [item.name for item in capped.needed(small, (480, 720))] == ["compat480"]
    assert capped.needed([LADDER[0], LADDER[1]], (480,)) == []  # only a 1080p rung
    assert capped.needed(LADDER, ()) == []


def test_missing_skips_ready_ones_made_from_the_same_rung() -> None:
    made = row(RenditionKind.COMPAT_CAPPED, "compat720", 718, rung="v1")
    assert [item.name for item in capped.missing([*LADDER, made], (720,))] == []
    stale = row(RenditionKind.COMPAT_CAPPED, "compat720", 540, rung="v2")
    assert [item.name for item in capped.missing([*LADDER, stale], (720,))] == ["compat720"]


@pytest.mark.django_db
def test_ceilings_come_from_plans_subscriptions_and_access(make_customer: Any) -> None:
    from apps.billing.models import Plan  # noqa: PLC0415

    assert capped.wanted_ceilings() == ()
    Plan.objects.create(code="sd", name_en="SD", name_ar="SD", max_quality=480, active=False)
    assert capped.wanted_ceilings() == ()  # an archived plan doesn't count
    Plan.objects.create(code="hd", name_en="HD", name_ar="HD", max_quality=720)
    make_customer(max_quality=480)
    make_customer(max_quality=1080)
    assert capped.wanted_ceilings() == (480, 720)
