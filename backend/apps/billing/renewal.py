"""Subscription period arithmetic (SPEC §7.6, §15: property-tested with Hypothesis).

A plan period is `months` calendar months plus `days` days, added on the
customer's wall clock in their time zone, so a subscription bought at 21:00 in
Riyadh ends at 21:00 there, whatever daylight saving does elsewhere:

- months first, clamping the day to the target month's end (Jan 31 + 1 month =
  Feb 28, or Feb 29 in a leap year);
- then days, as calendar days;
- a wall time that does not exist (a spring-forward gap) moves forward by the gap;
  an ambiguous one (fall-back) takes its first occurrence (PEP 495, fold=0).

Results are aware UTC datetimes. Trials are absolute: `start + hours`.
"""

import calendar
from datetime import UTC, datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def zone(name: str) -> tzinfo:
    """The customer's time zone; UTC for an unknown name."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return UTC


def add_months(moment: datetime, months: int) -> datetime:
    """`moment` (naive or aware, kept as is) plus calendar months, clamped to the month's end."""
    index = moment.month - 1 + months
    year, month = moment.year + index // 12, index % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def add_period(start: datetime, *, months: int = 0, days: int = 0, tz: tzinfo = UTC) -> datetime:
    """`start` plus a plan period on the wall clock of `tz`, as an aware UTC datetime."""
    if start.tzinfo is None:
        msg = "start must be timezone-aware"
        raise ValueError(msg)
    if months < 0 or days < 0:
        msg = "periods are never negative"
        raise ValueError(msg)
    wall = start.astimezone(tz).replace(tzinfo=None)
    wall = add_months(wall, months) + timedelta(days=days)
    return wall.replace(tzinfo=tz, fold=0).astimezone(UTC)


def extend_from(
    now: datetime, current_end: datetime | None, *, months: int, days: int, tz: tzinfo
) -> datetime:
    """SPEC §7.6: the new end of a renewal, `max(now, current_end) + period`."""
    base = now if current_end is None or current_end < now else current_end
    return add_period(base, months=months, days=days, tz=tz)


def trial_end(start: datetime, hours: int) -> datetime:
    return (start + timedelta(hours=hours)).astimezone(UTC)
