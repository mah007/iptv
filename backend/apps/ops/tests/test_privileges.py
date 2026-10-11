"""The database roles (G-37, ADR-0020), checked against the real PostgreSQL.

The test database gets the same grants as production (`apps.ops.grants`, after
migrate); each check switches the connection to the role (SET ROLE) inside a savepoint.
The roles themselves are created by the `db-roles` service, so these tests skip on a
cluster without them.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, timedelta
from typing import Any

import pytest
from django.db import connection, transaction
from django.db.utils import ProgrammingError
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.core import partitions
from apps.ops import grants

pytestmark = pytest.mark.django_db


def _roles_exist() -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM pg_roles WHERE rolname IN (%s, %s)",
            [grants.APP_ROLE, grants.REPORTING_ROLE],
        )
        return bool(cursor.fetchone()[0] == 2)


@pytest.fixture(autouse=True)
def _need_roles(db: None) -> None:
    if not _roles_exist():
        pytest.skip("the iptv_app and iptv_reporting roles are not in this cluster (db-roles)")


@contextmanager
def role(name: str) -> Iterator[Any]:
    """Run as `name` inside a savepoint; an error rolls the role back with it."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(f"SET LOCAL ROLE {name}")
        yield cursor
        cursor.execute("RESET ROLE")


def denied(name: str, sql: str, params: list[Any] | None = None) -> bool:
    try:
        with role(name) as cursor:
            cursor.execute(sql, params)
    except ProgrammingError as exc:
        # 42501 insufficient_privilege: permission denied, must be owner, and the
        # definer functions' own refusals.
        return getattr(exc.__cause__, "sqlstate", None) == "42501"
    return False


def test_the_app_role_reads_and_writes_rows() -> None:
    with role(grants.APP_ROLE) as cursor:
        cursor.execute("SELECT count(*) FROM accounts_user")
        cursor.execute(
            "INSERT INTO core_setting (id, created_at, updated_at, key, value, ciphertext) "
            "VALUES (gen_random_uuid(), now(), now(), 'test.privileges', '1', '')"
        )
        cursor.execute("UPDATE core_setting SET value = '2' WHERE key = 'test.privileges'")
        cursor.execute("DELETE FROM core_setting WHERE key = 'test.privileges'")
        cursor.execute("SELECT nextval(pg_get_serial_sequence('django_migrations', 'id'))")


def test_the_app_role_cannot_change_the_schema() -> None:
    assert denied(grants.APP_ROLE, "CREATE TABLE ops_intruder (id int)")
    assert denied(grants.APP_ROLE, "ALTER TABLE accounts_user ADD COLUMN intruder int")
    assert denied(grants.APP_ROLE, "DROP TABLE core_setting")
    assert denied(grants.APP_ROLE, "TRUNCATE core_setting")
    assert denied(grants.APP_ROLE, "ALTER TABLE audit_auditlog DISABLE TRIGGER ALL")


def test_the_audit_log_is_append_only_for_the_app() -> None:
    entry = AuditLog.objects.create(action="test.entry")
    with role(grants.APP_ROLE) as cursor:
        cursor.execute(
            "INSERT INTO audit_auditlog (id, at, action, target_type, target_id) "
            "VALUES (gen_random_uuid(), now(), 'test.insert', '', '')"
        )
    assert denied(
        grants.APP_ROLE, "UPDATE audit_auditlog SET action = 'x' WHERE id = %s", [entry.pk]
    )
    assert denied(grants.APP_ROLE, "DELETE FROM audit_auditlog WHERE id = %s", [entry.pk])
    assert denied(grants.APP_ROLE, "TRUNCATE audit_auditlog")


def test_not_even_the_owner_deletes_audit_rows_outside_the_purge() -> None:
    entry = AuditLog.objects.create(action="test.entry")
    with pytest.raises(ProgrammingError, match="append-only"), transaction.atomic():
        AuditLog.objects.filter(pk=entry.pk)._raw_delete(AuditLog.objects.db)


def test_audit_purge_keeps_its_floors() -> None:
    now = timezone.now()

    def entry(action: str, days: int) -> AuditLog:
        return AuditLog.objects.create(action=action, at=now - timedelta(days=days))

    recent = entry("customer.update", 200)
    old = entry("customer.update", 400)
    billing_old = entry("payment.record", 400)
    billing_ancient = entry("subscription.activate", 2000)
    with role(grants.APP_ROLE) as cursor:
        cursor.execute("SELECT audit_purge(0, 0)")  # asks for everything: floors apply
        removed = cursor.fetchone()[0]
    assert removed == 2
    left = set(AuditLog.objects.values_list("pk", flat=True))
    assert recent.pk in left
    assert billing_old.pk in left
    assert old.pk not in left
    assert billing_ancient.pk not in left


def test_public_cannot_call_the_definer_functions() -> None:
    assert denied(grants.REPORTING_ROLE, "SELECT audit_purge(400, 2000)")
    assert denied(
        grants.REPORTING_ROLE, "SELECT iptv_partition_create('live_epgprogram', '2031-01-01')"
    )


def test_the_app_creates_and_drops_partitions_through_the_functions() -> None:
    month = date(2031, 3, 1)
    with role(grants.APP_ROLE):
        created = partitions.ensure_monthly("live_epgprogram", month, 2)
        assert created == ["live_epgprogram_y2031m03", "live_epgprogram_y2031m04"]
        assert partitions.ensure_monthly("live_epgprogram", month, 1) == []
        dropped = partitions.drop_monthly_before("live_epgprogram", date(2031, 4, 1))
        assert "live_epgprogram_y2031m03" in dropped
    names = {partition.name for partition in partitions.list_partitions("live_epgprogram")}
    assert "live_epgprogram_y2031m04" in names
    assert "live_epgprogram_y2031m03" not in names


def test_the_partition_functions_refuse_other_tables_and_names() -> None:
    assert denied(grants.APP_ROLE, "SELECT iptv_partition_create('accounts_user', '2031-01-01')")
    assert denied(grants.APP_ROLE, "SELECT iptv_partition_drop('live_epgprogram', 'accounts_user')")


def test_reporting_reads_but_not_credentials_and_never_writes() -> None:
    with role(grants.REPORTING_ROLE) as cursor:
        cursor.execute("SELECT count(*) FROM billing_payment")
    assert denied(grants.REPORTING_ROLE, "SELECT * FROM accounts_xtreamcredential")
    assert denied(grants.REPORTING_ROLE, "SELECT * FROM core_setting")
    assert denied(grants.REPORTING_ROLE, "DELETE FROM billing_payment")


def test_grants_are_idempotent_and_cover_new_tables() -> None:
    first = grants.apply(connection)
    assert first == grants.apply(connection)
    with connection.cursor() as cursor:
        cursor.execute("CREATE TABLE ops_later_table (id int)")
    with role(grants.APP_ROLE) as cursor:
        cursor.execute("INSERT INTO ops_later_table VALUES (1)")


def test_the_statements_for_roles_that_exist() -> None:
    sql = grants.statements(
        database="iptv",
        owner="iptv",
        roles={grants.APP_ROLE},
        audit_exists=False,
        functions=[],
        hidden=[],
        quote=lambda name: f'"{name}"',
    )
    assert any("ALTER DEFAULT PRIVILEGES" in statement for statement in sql)
    assert not any("audit_auditlog" in statement for statement in sql)
    assert not any("iptv_reporting" in statement for statement in sql)


def test_owns_schema() -> None:
    assert grants.owns_schema(connection)
    with role(grants.APP_ROLE):
        assert not grants.owns_schema(connection)
