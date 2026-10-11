"""Monthly range partitions in PostgreSQL (SPEC §6: high-volume tables).

A partitioned parent is created by its app's migration (`PARTITION BY RANGE (<col>)`,
with the partition column in the primary key). This module keeps its children, one
per calendar month in UTC, named `<table>_yYYYYmMM`:

- `ensure_monthly` creates the months a writer is about to use, and the months ahead
  (a daily beat task), so an insert never meets a missing partition. There is no
  DEFAULT partition: once it held rows, creating the matching month would fail.
- `drop_monthly_before` drops whole months that ended before a cutoff: retention
  without row-by-row deletes or vacuum debt.

Both serialise on a transaction-level advisory lock per table, so two importers
creating the same month never race. ADR-0017 introduced this for `live_epgprogram`;
M15 can reuse it for the tables ADR-0005 deferred.
"""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime

from django.db import connections, transaction

_TABLE = re.compile(r"[a-z_][a-z0-9_]{0,40}")
_CHILD = re.compile(r"_y(?P<year>\d{4})m(?P<month>\d{2})$")


@dataclass(frozen=True, slots=True, order=True)
class Partition:
    lower: date  # the first day of the month
    name: str

    @property
    def upper(self) -> date:
        return add_months(self.lower, 1)


def month_start(moment: date | datetime) -> date:
    if isinstance(moment, datetime):
        moment = (moment.astimezone(UTC) if moment.tzinfo else moment).date()
    return moment.replace(day=1)


def add_months(month: date, count: int) -> date:
    index = month.year * 12 + (month.month - 1) + count
    return date(index // 12, index % 12 + 1, 1)


def partition_name(table: str, month: date) -> str:
    return f"{_checked(table)}_y{month.year:04d}m{month.month:02d}"


def _checked(table: str) -> str:
    if not _TABLE.fullmatch(table):
        msg = f"not a partitioned table name: {table!r}"
        raise ValueError(msg)
    return table


def _bound(month: date) -> str:
    return f"{month.isoformat()} 00:00:00+00"


def _lock(cursor: object, table: str) -> None:
    cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [f"partitions:{table}"])  # type: ignore[attr-defined]


def list_partitions(table: str, *, using: str = "default") -> list[Partition]:
    """The monthly children of `table`, oldest first."""
    _checked(table)
    with connections[using].cursor() as cursor:
        cursor.execute(
            """
            SELECT child.relname
            FROM pg_inherits
            JOIN pg_class parent ON parent.oid = pg_inherits.inhparent
            JOIN pg_class child ON child.oid = pg_inherits.inhrelid
            WHERE parent.relname = %s
            """,
            [table],
        )
        names = [row[0] for row in cursor.fetchall()]
    found = []
    for name in names:
        match = _CHILD.search(name)
        if match and name == partition_name(
            table, date(int(match["year"]), int(match["month"]), 1)
        ):
            found.append(Partition(date(int(match["year"]), int(match["month"]), 1), name))
    return sorted(found)


def ensure_monthly(
    table: str, first: date | datetime, months: int = 1, *, using: str = "default"
) -> list[str]:
    """Create the partitions for `months` months from `first`'s month; returns the new ones."""
    start = month_start(first)
    created: list[str] = []
    connection = connections[using]
    quote = connection.ops.quote_name
    with transaction.atomic(using=using), connection.cursor() as cursor:
        _lock(cursor, table)
        existing = {partition.name for partition in list_partitions(table, using=using)}
        for offset in range(max(0, months)):
            month = add_months(start, offset)
            name = partition_name(table, month)
            if name in existing:
                continue
            cursor.execute(
                f"CREATE TABLE IF NOT EXISTS {quote(name)} PARTITION OF {quote(table)} "
                f"FOR VALUES FROM ('{_bound(month)}') TO ('{_bound(add_months(month, 1))}')"
            )
            created.append(name)
    return created


def drop_monthly_before(
    table: str, cutoff: date | datetime, *, using: str = "default"
) -> list[str]:
    """Drop every monthly partition that ended on or before `cutoff`; returns their names."""
    if isinstance(cutoff, datetime):
        cutoff_day = (cutoff.astimezone(UTC) if cutoff.tzinfo else cutoff).date()
    else:
        cutoff_day = cutoff
    dropped: list[str] = []
    connection = connections[using]
    with transaction.atomic(using=using), connection.cursor() as cursor:
        _lock(cursor, table)
        # Deferred foreign-key checks of rows written earlier in this transaction would
        # block the DROP ("pending trigger events"): run them now.
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        for partition in list_partitions(table, using=using):
            if partition.upper <= cutoff_day:
                cursor.execute(f"DROP TABLE IF EXISTS {connection.ops.quote_name(partition.name)}")
                dropped.append(partition.name)
    return dropped
