"""The read replica (ADR-0022): where reporting reads go, and the router."""

import json
from typing import Any

import pytest
from django.db import DatabaseError

from apps.core import replica
from apps.core.services import set_setting
from apps.core.stores import cache_redis


@pytest.fixture
def with_replica(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(replica, "configured", lambda: True)


def lag(seconds: float | None, *, ok: bool = True) -> None:
    cache_redis().set(replica.LAG_KEY, json.dumps({"ok": ok, "lag_s": seconds, "at": 0}))
    replica.reset()


def test_without_a_replica_everything_reads_the_primary() -> None:
    assert replica.reporting_db() == "default"
    assert replica.measure() is None


@pytest.mark.django_db
def test_a_fresh_replica_takes_reporting_reads(with_replica: None) -> None:
    lag(2.5)
    assert replica.reporting_db() == "replica"


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("seconds", "ok", "expected"),
    [
        (31.0, True, "default"),
        (None, True, "default"),
        (0.0, False, "default"),
        (30.0, True, "replica"),
    ],
)
def test_lag_and_health_decide(
    with_replica: None, seconds: float | None, ok: bool, expected: str
) -> None:
    lag(seconds, ok=ok)
    assert replica.reporting_db() == expected


@pytest.mark.django_db
def test_the_admin_can_turn_replica_reads_off_or_tighten_the_lag(with_replica: None) -> None:
    lag(10.0)
    set_setting("database.replica_max_lag_s", 5, actor=None)
    assert replica.reporting_db() == "default"
    set_setting("database.replica_max_lag_s", 30, actor=None)
    assert replica.reporting_db() == "replica"
    set_setting("database.replica_reads", False, actor=None)
    assert replica.reporting_db() == "default"


@pytest.mark.django_db
def test_an_unmeasured_replica_is_not_used(with_replica: None) -> None:
    cache_redis().delete(replica.LAG_KEY)
    replica.reset()
    assert replica.reporting_db() == "default"


@pytest.mark.django_db
def test_a_failing_replica_backs_off_to_the_primary(with_replica: None) -> None:
    lag(1.0)
    seen: list[str] = []

    def query(alias: str) -> str:
        seen.append(alias)
        if alias == "replica":
            raise DatabaseError("replica gone")
        return "rows"

    assert replica.read(query) == "rows"
    assert seen == ["replica", "default"]
    assert replica.reporting_db() == "default"  # for the next 30 s


def test_the_router_keeps_writes_and_migrations_on_the_primary() -> None:
    router = replica.ReplicaRouter()
    model: Any = object()
    assert router.db_for_write(model) == "default"
    assert router.db_for_read(model) is None
    assert router.allow_migrate("default", "catalog") is True
    assert router.allow_migrate("replica", "catalog") is False
    assert router.allow_relation(model, model) is True


@pytest.mark.django_db
def test_reporting_functions_read_where_the_alias_says(with_replica: None) -> None:
    lag(1.0)
    seen: list[str | None] = []
    router = replica.ReplicaRouter()

    @replica.reporting
    def report() -> str:
        seen.append(router.db_for_read(object()))
        return "done"

    assert report() == "done"
    assert seen == ["replica"]
    assert router.db_for_read(object()) is None  # only inside


@pytest.mark.django_db
def test_a_reporting_function_runs_again_on_the_primary(with_replica: None) -> None:
    lag(1.0)
    calls: list[str | None] = []
    router = replica.ReplicaRouter()

    @replica.reporting
    def report() -> int:
        calls.append(router.db_for_read(object()))
        if calls[-1] == "replica":
            raise DatabaseError("standby went away")
        return len(calls)

    assert report() == 2
    assert calls == ["replica", "default"]


@pytest.mark.django_db
def test_without_a_replica_reporting_reads_the_primary_once() -> None:
    calls: list[str | None] = []

    @replica.reporting
    def report() -> None:
        calls.append(replica.ReplicaRouter().db_for_read(object()))

    report()
    assert calls == ["default"]
