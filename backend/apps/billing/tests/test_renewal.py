# Naive datetimes here are wall-clock times, made aware with the zone under test.
# ruff: noqa: DTZ001
"""Renewal date maths (SPEC §15: property-based with Hypothesis): month ends, leap
years and time zones, including daylight-saving transitions."""

import calendar
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given
from hypothesis import strategies as st

from apps.billing import renewal

ZONES = (
    "UTC",
    "Asia/Riyadh",
    "Asia/Dubai",
    "Asia/Kolkata",
    "Europe/London",
    "Europe/Berlin",
    "America/New_York",
    "America/Sao_Paulo",
    "Australia/Lord_Howe",  # a 30-minute daylight-saving shift
    "Pacific/Chatham",  # a +12:45 / +13:45 offset
)

instants = st.datetimes(
    min_value=datetime(2000, 1, 1),  # Hypothesis wants naive bounds
    max_value=datetime(2090, 12, 31),
    timezones=st.just(UTC),
)
zones = st.sampled_from(ZONES).map(ZoneInfo)
months = st.integers(min_value=0, max_value=36)
days = st.integers(min_value=0, max_value=400)


def local(moment: datetime, tz: ZoneInfo) -> datetime:
    return moment.astimezone(tz)


@given(start=instants, tz=zones, months=months, days=days)
def test_a_period_always_ends_later_and_in_utc(
    start: datetime, tz: ZoneInfo, months: int, days: int
) -> None:
    end = renewal.add_period(start, months=months, days=days, tz=tz)
    assert end.tzinfo is UTC
    if months or days:
        assert end > start
    else:
        assert abs(end - start) <= timedelta(hours=1)  # only a DST gap can move it


@given(start=instants, tz=zones, days=st.integers(min_value=1, max_value=400))
def test_days_keep_the_wall_clock(start: datetime, tz: ZoneInfo, days: int) -> None:
    end = renewal.add_period(start, days=days, tz=tz)
    begin, finish = local(start, tz), local(end, tz)
    wall = begin.replace(tzinfo=None) + timedelta(days=days)
    if finish.replace(tzinfo=None) == wall:
        return
    # The wall time fell in a spring-forward gap: it moves forward by the gap.
    assert timedelta(0) < finish.replace(tzinfo=None) - wall <= timedelta(hours=1)


@given(start=instants, tz=zones, days=st.integers(min_value=1, max_value=400))
def test_days_last_about_24_hours_each(start: datetime, tz: ZoneInfo, days: int) -> None:
    elapsed = renewal.add_period(start, days=days, tz=tz) - start
    assert abs(elapsed - timedelta(days=days)) <= timedelta(hours=2)


@given(start=instants, tz=zones, months=st.integers(min_value=1, max_value=36))
def test_months_clamp_to_the_end_of_shorter_months(
    start: datetime, tz: ZoneInfo, months: int
) -> None:
    begin = local(start, tz).replace(tzinfo=None)
    finish = local(renewal.add_period(start, months=months, tz=tz), tz).replace(tzinfo=None)
    index = begin.month - 1 + months
    year, month = begin.year + index // 12, index % 12 + 1
    expected = begin.replace(
        year=year, month=month, day=min(begin.day, calendar.monthrange(year, month)[1])
    )
    # Exact, unless the wall time fell in a spring-forward gap (then up to an hour later).
    assert timedelta(0) <= finish - expected <= timedelta(hours=1)


@given(a=instants, b=instants, tz=zones, months=months, days=days)
def test_later_purchases_never_end_much_earlier(
    a: datetime, b: datetime, tz: ZoneInfo, months: int, days: int
) -> None:
    first, second = sorted((a, b))
    end_first = renewal.add_period(first, months=months, days=days, tz=tz)
    end_second = renewal.add_period(second, months=months, days=days, tz=tz)
    # Exactly monotonic, except within one DST transition (fall-back folds, spring gaps).
    assert end_second >= end_first - timedelta(hours=1)


@given(a=instants, b=instants, months=months, days=days)
def test_monotonic_in_utc(a: datetime, b: datetime, months: int, days: int) -> None:
    first, second = sorted((a, b))
    assert renewal.add_period(first, months=months, days=days) <= renewal.add_period(
        second, months=months, days=days
    )


@given(
    now=instants,
    offset=st.integers(min_value=-500, max_value=500),
    tz=zones,
    days=st.integers(min_value=1, max_value=400),
)
def test_extension_starts_from_the_later_of_now_and_the_end(
    now: datetime, offset: int, tz: ZoneInfo, days: int
) -> None:
    current_end = now + timedelta(days=offset)
    end = renewal.extend_from(now, current_end, months=0, days=days, tz=tz)
    assert end == renewal.add_period(max(now, current_end), days=days, tz=tz)
    assert end > now
    assert end >= current_end


@pytest.mark.parametrize(
    ("start", "months", "expected"),
    [
        # Month ends clamp; leap years keep Feb 29.
        (datetime(2027, 1, 31, 21, 0), 1, datetime(2027, 2, 28, 21, 0)),
        (datetime(2028, 1, 31, 21, 0), 1, datetime(2028, 2, 29, 21, 0)),
        (datetime(2028, 2, 29, 9, 30), 12, datetime(2029, 2, 28, 9, 30)),
        (datetime(2028, 2, 29, 9, 30), 48, datetime(2032, 2, 29, 9, 30)),
        (datetime(2026, 3, 31, 0, 0), 1, datetime(2026, 4, 30, 0, 0)),
        (datetime(2026, 12, 15, 23, 59), 1, datetime(2027, 1, 15, 23, 59)),
        (datetime(2026, 10, 31, 12, 0), 4, datetime(2027, 2, 28, 12, 0)),
    ],
)
def test_month_examples_on_the_riyadh_clock(
    start: datetime, months: int, expected: datetime
) -> None:
    riyadh = ZoneInfo("Asia/Riyadh")
    end = renewal.add_period(start.replace(tzinfo=riyadh), months=months, tz=riyadh)
    assert end.astimezone(riyadh).replace(tzinfo=None) == expected


def test_dst_examples() -> None:
    new_york = ZoneInfo("America/New_York")
    # 30 days across spring-forward keep 21:00 local, so they last 30 days minus an hour.
    start = datetime(2026, 3, 1, 21, 0, tzinfo=new_york)
    end = renewal.add_period(start, days=30, tz=new_york)
    assert end.astimezone(new_york).replace(tzinfo=None) == datetime(2026, 3, 31, 21, 0)
    assert end - start == timedelta(days=30, hours=-1)
    # A wall time inside the gap (02:30 on 8 March) moves forward by the gap.
    gap = renewal.add_period(datetime(2026, 3, 7, 2, 30, tzinfo=new_york), days=1, tz=new_york)
    assert gap.astimezone(new_york).replace(tzinfo=None) == datetime(2026, 3, 8, 3, 30)
    # An ambiguous time (01:30 on 1 November) takes its first occurrence (EDT).
    fold = renewal.add_period(datetime(2026, 10, 31, 1, 30, tzinfo=new_york), days=1, tz=new_york)
    assert fold.astimezone(new_york).utcoffset() == timedelta(hours=-4)


def test_trials_are_absolute_hours() -> None:
    start = datetime(2026, 3, 7, 12, 0, tzinfo=UTC)
    assert renewal.trial_end(start, 24) == start + timedelta(hours=24)


def test_bad_input_is_refused() -> None:
    with pytest.raises(ValueError, match="aware"):
        renewal.add_period(datetime(2026, 1, 1), days=1)
    with pytest.raises(ValueError, match="negative"):
        renewal.add_period(datetime(2026, 1, 1, tzinfo=UTC), days=-1)


def test_unknown_zones_fall_back_to_utc() -> None:
    assert renewal.zone("Mars/Olympus") is UTC
    assert renewal.zone("Asia/Riyadh") == ZoneInfo("Asia/Riyadh")
