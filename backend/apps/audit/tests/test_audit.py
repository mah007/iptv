"""The audit log: append-only at the database, redacted snapshots (SPEC §6 ops, §11)."""

import datetime as dt
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.db import DatabaseError, connection, transaction
from django.db.models import ProtectedError
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.models import AuditLog, AuditLogImmutableError
from apps.audit.services import AuditTarget, record, target_of

pytestmark = pytest.mark.django_db


@pytest.fixture
def entry(staff_user: User) -> AuditLog:
    return record(
        "setting.update",
        actor=staff_user,
        target=AuditTarget("core.setting", "billing.grace_days"),
        before={"value": 3},
        after={"value": 5},
        ip="203.0.113.7",
    )


def test_an_entry_records_who_what_when_and_where(entry: AuditLog, staff_user: User) -> None:
    assert entry.pk.version == 7
    assert abs(timezone.now() - entry.at) < dt.timedelta(seconds=5)
    assert entry.actor == staff_user
    assert entry.actor_ip == "203.0.113.7"
    assert entry.action == "setting.update"
    assert (entry.target_type, entry.target_id) == ("core.setting", "billing.grace_days")
    assert (entry.before, entry.after) == ({"value": 3}, {"value": 5})
    assert str(entry) == "setting.update core.setting:billing.grace_days"


def test_model_targets_are_identified_by_label_and_pk(staff_user: User) -> None:
    assert target_of(staff_user) == ("accounts.user", str(staff_user.pk))
    assert target_of(None) == ("", "")
    logged = record("customer.suspend", actor=None, target=staff_user)
    assert (logged.target_type, logged.target_id) == ("accounts.user", str(staff_user.pk))
    assert logged.actor is None


def test_snapshots_are_redacted_and_json_normalised(staff_user: User) -> None:
    marker = uuid.uuid4()
    when = dt.datetime(2026, 10, 3, 12, 0, tzinfo=dt.UTC)
    logged = record(
        "device.reset_credentials",
        actor=staff_user,
        before={"username": "mah-7k3p9q", "password_hash": "argon2$...", "device": marker},
        after={
            "username": "mah-7k3p9q",
            "password": "Plaintext-Once-1",
            "url": "/movie/mah-7k3p9q/Plaintext-Once-1/1.mp4",
            "at": when,
        },
    )
    logged.refresh_from_db()
    assert logged.before == {
        "username": "mah-7k3p9q",
        "password_hash": "***",
        "device": str(marker),
    }
    assert logged.after == {
        "username": "mah-7k3p9q",
        "password": "***",
        "url": "/movie/***/***/1.mp4",
        "at": "2026-10-03T12:00:00Z",
    }


@pytest.mark.parametrize(
    ("ip", "stored"),
    [("2001:DB8::1", "2001:db8::1"), ("not-an-ip", None), ("", None), (None, None)],
)
def test_actor_ips_are_validated(ip: str | None, stored: str | None) -> None:
    assert record("x.y", actor=None, ip=ip).actor_ip == stored


def test_the_model_refuses_updates_and_deletes(entry: AuditLog) -> None:
    entry.action = "tampered"
    with pytest.raises(AuditLogImmutableError):
        entry.save()
    with pytest.raises(AuditLogImmutableError):
        entry.delete()


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_auditlog SET action = 'tampered'",
        "DELETE FROM audit_auditlog",
    ],
)
def test_the_database_refuses_updates_and_deletes(entry: AuditLog, statement: str) -> None:
    with (
        pytest.raises(DatabaseError, match="append-only"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(statement)
    entry.refresh_from_db()
    assert entry.action == "setting.update"


def test_bulk_queryset_writes_hit_the_trigger_too(entry: AuditLog) -> None:
    with pytest.raises(DatabaseError, match="UPDATE is not allowed"), transaction.atomic():
        AuditLog.objects.filter(pk=entry.pk).update(action="tampered")
    with pytest.raises(DatabaseError, match="DELETE is not allowed"), transaction.atomic():
        AuditLog.objects.filter(pk=entry.pk).delete()
    assert AuditLog.objects.get(pk=entry.pk).action == "setting.update"


def test_actors_with_history_cannot_be_deleted(entry: AuditLog, staff_user: User) -> None:
    with pytest.raises(ProtectedError):
        get_user_model().objects.filter(pk=staff_user.pk).delete()


def test_newest_entries_come_first(staff_user: User) -> None:
    first = record("a.first", actor=staff_user)
    second = record("a.second", actor=staff_user)
    assert list(AuditLog.objects.all()) == [second, first]
