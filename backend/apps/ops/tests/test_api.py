"""The Backups page API and the backup service's internal API (ADR-0020)."""

from collections.abc import Callable
from typing import Any

import pytest
from django.conf import settings
from django.test import Client
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.conftest import AdminFactory
from apps.core.services import set_setting
from apps.ops import backups
from apps.ops.models import BackupKind, BackupRun, RunStatus, RunTrigger

pytestmark = pytest.mark.django_db

ADMIN = {"host": settings.ADMIN_HOST}
SUMMARY = "/api/v1/admin/backups"
RUNS = "/api/v1/admin/backups/runs"
RUN = "/api/v1/admin/backups/run"
POLL = "/internal/backups/agent/poll"
TOKEN = "agent-token-for-tests"  # noqa: S105


def api(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.fixture
def token(settings: Any) -> str:
    settings.BACKUP_AGENT_TOKEN = TOKEN
    return TOKEN


def agent(token: str | None = TOKEN) -> Client:
    defaults: dict[str, Any] = {"HTTP_HOST": "web"}
    if token:
        defaults["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    return Client(**defaults)


# --- Admin ------------------------------------------------------------------------------


def test_the_summary_needs_backups_view(make_admin: AdminFactory, owner: User) -> None:
    assert api(make_admin("support")).get(SUMMARY, headers=ADMIN).status_code == 403
    assert api(make_admin("viewer")).get(SUMMARY, headers=ADMIN).status_code == 200
    body = api(owner).get(SUMMARY, headers=ADMIN).json()
    assert body["enabled"] is True
    assert body["agent"]["healthy"] is False
    assert body["latest"] == {
        "db": None,
        "files": None,
        "offsite": None,
        "drill": None,
        "check": None,
    }
    assert "secret" not in str(body).lower()


def test_back_up_now_queues_and_audits(owner: User, agent_seen: Callable[..., None]) -> None:
    agent_seen()
    response = api(owner).post(RUN, {"what": "backup"}, format="json", headers=ADMIN)
    assert response.status_code == 202
    assert [run["kind"] for run in response.json()] == ["db_diff", "files"]
    assert {run.trigger for run in BackupRun.objects.all()} == {RunTrigger.MANUAL}
    entry = AuditLog.objects.get(action="backup.backup")
    assert entry.actor == owner
    assert entry.after == {"runs": ["db_diff", "files"]}
    again = api(owner).post(RUN, {"what": "backup"}, format="json", headers=ADMIN)
    assert again.status_code == 409


def test_back_up_now_refuses_without_the_service_or_when_off(
    owner: User, agent_seen: Callable[..., None]
) -> None:
    refused = api(owner).post(RUN, {"what": "backup"}, format="json", headers=ADMIN)
    assert refused.status_code == 409
    assert "not running" in refused.json()["detail"]
    agent_seen()
    set_setting("backups.enabled", False, actor=None)
    off = api(owner).post(RUN, {"what": "backup"}, format="json", headers=ADMIN)
    assert off.status_code == 409
    assert api(owner).post(RUN, {"what": "check"}, format="json", headers=ADMIN).status_code == 202


def test_the_drill_needs_a_backup_first(owner: User, agent_seen: Callable[..., None]) -> None:
    agent_seen()
    assert api(owner).post(RUN, {"what": "drill"}, format="json", headers=ADMIN).status_code == 409
    done = backups.queue(BackupKind.DB_FULL, trigger=RunTrigger.MANUAL)
    done.status = RunStatus.SUCCEEDED
    done.save()
    response = api(owner).post(RUN, {"what": "drill"}, format="json", headers=ADMIN)
    assert response.status_code == 202
    assert response.json()[0]["kind"] == "drill"


def test_running_needs_backups_run(
    make_admin: AdminFactory, agent_seen: Callable[..., None]
) -> None:
    agent_seen()
    viewer = make_admin("viewer")
    assert api(viewer).post(RUN, {"what": "check"}, format="json", headers=ADMIN).status_code == 403
    assert (
        api(make_admin("admin"))
        .post(RUN, {"what": "nope"}, format="json", headers=ADMIN)
        .status_code
        == 400
    )


def test_the_run_history_filters(owner: User) -> None:
    backups.queue(BackupKind.DB_FULL, trigger=RunTrigger.SCHEDULE)
    backups.queue(BackupKind.DRILL, trigger=RunTrigger.SCHEDULE, actor=owner)
    body = api(owner).get(RUNS, {"kind": "drill"}, headers=ADMIN).json()
    assert body["count"] == 1
    assert body["results"][0]["requested_by"] == "owner"
    assert api(owner).get(RUNS, {"status": "failed"}, headers=ADMIN).json()["count"] == 0
    assert api(owner).get(RUNS, {"kind": "bogus"}, headers=ADMIN).status_code == 400


# --- The backup service's internal API ------------------------------------------------------


def test_the_agent_api_needs_its_token(token: str, settings: Any) -> None:
    assert agent(None).post(POLL, "{}", content_type="application/json").status_code == 401
    assert agent("wrong").post(POLL, "{}", content_type="application/json").status_code == 401
    settings.BACKUP_AGENT_TOKEN = ""
    assert agent().post(POLL, "{}", content_type="application/json").status_code == 404


def test_the_agent_api_is_not_on_public_hosts(token: str) -> None:
    client = Client(HTTP_HOST=settings.ADMIN_HOST, HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
    assert client.post(POLL, "{}", content_type="application/json").status_code == 404


def test_a_poll_records_the_status_and_hands_out_the_next_job(token: str) -> None:
    run = backups.queue(BackupKind.DB_FULL, trigger=RunTrigger.MANUAL)
    response = agent().post(POLL, {"status": {"archiving": True}}, content_type="application/json")
    body = response.json()
    assert response["Cache-Control"].startswith("max-age=0")
    assert body["config"]["enabled"] is True
    assert body["job"] == {"id": str(run.pk), "kind": "db_full", "params": {}}
    assert backups.agent_state().status == {"archiving": True}
    assert agent().post(POLL, "{}", content_type="application/json").json()["job"] is None
    assert agent().post(POLL, "[1]", content_type="application/json").status_code == 400


def test_a_result_finishes_the_run(token: str) -> None:
    run = backups.queue(BackupKind.FILES, trigger=RunTrigger.MANUAL)
    backups.claim_next()
    url = f"/internal/backups/agent/jobs/{run.pk}"
    response = agent().post(
        url,
        {"status": "succeeded", "detail": {"snapshot": "abc"}, "size_bytes": 10, "duration_s": 2.5},
        content_type="application/json",
    )
    assert response.json() == {"status": "succeeded"}
    run.refresh_from_db()
    assert (run.status, run.size_bytes, run.duration_s) == (RunStatus.SUCCEEDED, 10, 2.5)
    assert run.detail == {"snapshot": "abc"}
    missing = "/internal/backups/agent/jobs/01920000-0000-7000-8000-000000000000"
    assert agent().post(missing, "{}", content_type="application/json").status_code == 404
    assert agent().post(url, "nope", content_type="application/json").status_code == 400
