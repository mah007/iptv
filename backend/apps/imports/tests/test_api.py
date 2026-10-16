"""Admin API of imports: RBAC, step-up on credentials, secrets never returned or
audited, refusals as problem+json, the queue and the history (ADR-0025)."""

import json
from collections.abc import Callable
from typing import Any

import pytest
from django.conf import settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.conftest import AdminFactory
from apps.imports import services
from apps.imports.models import ImportFile, ImportJob, ImportSource, JobStatus, SourceKind
from apps.library.models import Library

pytestmark = pytest.mark.django_db

ADMIN = {"host": settings.ADMIN_HOST}
BASE = "/api/v1/admin/imports"
SECRET = "sk-" + "test-" + "0123456789abcdef"
type Capture = Callable[..., Any]


def client_for(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.fixture
def manager(make_admin: AdminFactory) -> APIClient:
    return client_for(make_admin(permissions=["library.view", "library.manage"]))


@pytest.fixture
def viewer(make_admin: AdminFactory) -> APIClient:
    return client_for(make_admin(permissions=["library.view"]))


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    from apps.imports import tasks  # noqa: PLC0415

    sent: list[tuple[Any, ...]] = []
    monkeypatch.setattr(tasks.run_import, "delay", lambda *args: sent.append(args))
    monkeypatch.setattr(tasks.remote_request, "delay", lambda *args: sent.append(args))
    return sent


@pytest.fixture
def library(make_library: Callable[..., Library]) -> Library:
    return make_library()


def s3_body(**extra: Any) -> dict[str, Any]:
    return {
        "name": "Wasabi",
        "kind": "s3",
        "options": {"provider": "wasabi", "endpoint": "https://s3.wasabisys.com", "root": "films"},
        "secrets": {"access_key_id": "AKIA" + "EXAMPLE", "secret_access_key": SECRET},
        **extra,
    }


def test_sources_are_created_listed_and_never_show_secrets(
    manager: APIClient, viewer: APIClient, django_assert_num_queries: Capture
) -> None:
    response = manager.post(f"{BASE}/sources", s3_body(), format="json", headers=ADMIN)
    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["secrets_set"] == ["access_key_id", "secret_access_key"]
    assert body["summary"] == "s3.wasabisys.com/films"
    assert SECRET not in json.dumps(body)
    manager.post(
        f"{BASE}/sources",
        {
            "name": "NAS",
            "kind": "sftp",
            "options": {"host": "nas.lan", "username": "me"},
            "secrets": {"password": SECRET},
        },
        format="json",
        headers=ADMIN,
    )
    with django_assert_num_queries(2):
        listed = viewer.get(f"{BASE}/sources", headers=ADMIN)
    assert [row["name"] for row in listed.json()] == ["NAS", "Wasabi"]
    assert SECRET not in listed.content.decode()
    entries = AuditLog.objects.filter(action__startswith="import.source")
    assert entries.count() == 2
    assert SECRET not in json.dumps(list(entries.values("before", "after")), default=str)
    sets = sorted(entry.after["set"] for entry in entries if entry.after)
    assert sets == [["access_key_id", "secret_access_key"], ["password"]]


def test_viewers_read_and_managers_change(
    viewer: APIClient, manager: APIClient, library: Library, queued: list[Any]
) -> None:
    assert (
        viewer.post(f"{BASE}/sources", s3_body(), format="json", headers=ADMIN).status_code == 403
    )
    assert viewer.get(f"{BASE}/jobs", headers=ADMIN).status_code == 200
    assert viewer.get(f"{BASE}/status", headers=ADMIN).status_code == 200
    source = manager.post(f"{BASE}/sources", s3_body(), format="json", headers=ADMIN).json()
    assert viewer.post(f"{BASE}/sources/{source['id']}/test", headers=ADMIN).status_code == 403
    assert viewer.post(f"{BASE}/links", {}, format="json", headers=ADMIN).status_code == 403
    anonymous = APIClient()
    assert anonymous.get(f"{BASE}/jobs", headers=ADMIN).status_code in (401, 403)


@pytest.mark.step_up
def test_credentials_need_a_recent_confirmation(
    manager: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = manager.post(f"{BASE}/sources", s3_body(), format="json", headers=ADMIN)
    assert (response.status_code, response.json()["code"]) == (403, "STEP_UP_REQUIRED")
    source = services.create_source(
        name="Wasabi",
        kind=SourceKind.S3,
        options={"provider": "wasabi", "endpoint": "https://s3.wasabisys.com"},
        secrets={"access_key_id": "a", "secret_access_key": "b"},
        actor=None,
        ip=None,
    )
    url = f"{BASE}/sources/{source.pk}"
    # A new name is not a credential...
    assert (
        manager.patch(url, {"name": "Wasabi EU"}, format="json", headers=ADMIN).status_code == 200
    )
    # ...a new secret, a new endpoint or removing a secret are.
    for body in (
        {"secrets": {"secret_access_key": "new"}},
        {"options": {"endpoint": "https://s3.eu-central-1.wasabisys.com"}},
        {"clear_secrets": ["access_key_id"]},
    ):
        response = manager.patch(url, body, format="json", headers=ADMIN)
        assert response.json()["code"] == "STEP_UP_REQUIRED", body
    assert manager.delete(url, headers=ADMIN).json()["code"] == "STEP_UP_REQUIRED"


def test_updates_keep_or_replace_secrets_and_record_which(manager: APIClient) -> None:
    source = manager.post(f"{BASE}/sources", s3_body(), format="json", headers=ADMIN).json()
    url = f"{BASE}/sources/{source['id']}"
    response = manager.patch(url, {"options": {"root": "classics"}}, format="json", headers=ADMIN)
    assert response.json()["options"]["root"] == "classics"
    assert response.json()["options"]["endpoint"] == "https://s3.wasabisys.com"
    stored = ImportSource.objects.get(pk=source["id"])
    assert services.source_secrets(stored)["secret_access_key"] == SECRET
    response = manager.patch(
        url, {"secrets": {"secret_access_key": "rotated"}}, format="json", headers=ADMIN
    )
    assert response.status_code == 200
    update = AuditLog.objects.filter(action="import.source.update").order_by("-at").first()
    assert update is not None
    assert update.after is not None
    assert update.after["changed"] == ["secret_access_key"]
    assert "rotated" not in json.dumps(update.after)
    response = manager.patch(url, {"kind": "sftp"}, format="json", headers=ADMIN)
    assert response.json()["field_error_codes"]["kind"] == ["kind_fixed"]
    response = manager.patch(
        url, {"clear_secrets": ["access_key_id"]}, format="json", headers=ADMIN
    )
    assert (response.status_code, response.json()["reason"]) == (400, "required")


@pytest.mark.parametrize(
    ("body", "field", "code"),
    [
        ({"options": {"provider": "r2"}}, "endpoint", "required"),
        (
            {"options": {"provider": "other", "endpoint": "http://postgres:5432"}},
            "endpoint",
            "unsafe_destination",
        ),
        ({"secrets": {}}, "access_key_id", "required"),
        ({"kind": "sftp", "options": {"host": "nas"}}, "username", "required"),
    ],
)
def test_bad_sources_are_refused_on_the_field(
    manager: APIClient, body: dict[str, Any], field: str, code: str
) -> None:
    response = manager.post(f"{BASE}/sources", {**s3_body(), **body}, format="json", headers=ADMIN)
    assert response.status_code == 400
    assert response.json()["field_error_codes"][field] == [code]


def test_a_test_and_a_browse_are_queued_and_polled(manager: APIClient, queued: list[Any]) -> None:
    source = manager.post(f"{BASE}/sources", s3_body(), format="json", headers=ADMIN).json()
    response = manager.post(f"{BASE}/sources/{source['id']}/test", headers=ADMIN)
    assert response.status_code == 202
    request_id = response.json()["request_id"]
    assert queued[-1][1:3] == ("test", source["id"])
    assert manager.get(f"{BASE}/requests/{request_id}", headers=ADMIN).json() == {
        "status": "pending",
        "result": None,
    }
    services.store_result(
        request_id, {"status": "done", "result": {"ok": True, "code": "ok", "message": ""}}
    )
    assert (
        manager.get(f"{BASE}/requests/{request_id}", headers=ADMIN).json()["result"]["ok"] is True
    )
    browse = manager.post(
        f"{BASE}/sources/{source['id']}/browse", {"path": "Films"}, format="json", headers=ADMIN
    )
    assert browse.status_code == 202
    assert queued[-1][1:] == ("browse", source["id"], "Films")
    assert manager.get(f"{BASE}/requests/nope", headers=ADMIN).status_code == 404


def test_imports_are_started_listed_and_cancelled(
    manager: APIClient,
    viewer: APIClient,
    library: Library,
    queued: list[Any],
    django_assert_num_queries: Capture,
) -> None:
    source = manager.post(f"{BASE}/sources", s3_body(), format="json", headers=ADMIN).json()
    body = {
        "source": source["id"],
        "items": [
            {"path": "Metropolis (1927)", "is_dir": True},
            {"path": "Nosferatu.mp4", "is_dir": False},
        ],
        "library": str(library.pk),
        "subfolder": "Classics",
        "rights_holder": "Public domain",
        "license_ref": "https://archive.org/details/metropolis",
    }
    response = manager.post(f"{BASE}/jobs", body, format="json", headers=ADMIN)
    assert response.status_code == 201, response.json()
    job = response.json()
    assert (job["status"], job["source"]["name"], job["library"]["name"]) == (
        "queued",
        "Wasabi",
        library.name,
    )
    assert job["created_by_name"]
    start = AuditLog.objects.get(action="import.start")
    assert start.after is not None
    assert start.after["rights_holder"] == "Public domain"
    assert start.after["items"] == ["Metropolis (1927)", "Nosferatu.mp4"]
    link = manager.post(
        f"{BASE}/links",
        {
            "url": "https://archive.org/download/plan9/Plan%209.mp4?sig=secret",
            "library": str(library.pk),
            "rights_holder": "Public domain",
            "license_ref": "https://archive.org/details/plan9",
        },
        format="json",
        headers=ADMIN,
    )
    assert link.status_code == 201, link.json()
    assert link.json()["url"] == "https://archive.org/download/plan9/Plan%209.mp4"
    assert link.json()["file_name"] == "Plan 9.mp4"
    for index in range(3):
        ImportJob.objects.create(
            origin="link",
            library=library,
            rights_holder="PD",
            license_ref=f"PD{index}",
            status=JobStatus.DONE,
        )
    with django_assert_num_queries(3):
        history = viewer.get(f"{BASE}/jobs?state=finished", headers=ADMIN)
    assert history.json()["count"] == 3
    active = viewer.get(f"{BASE}/jobs?state=active", headers=ADMIN).json()["results"]
    assert [row["origin"] for row in active] == ["source", "link"]
    cancelled = manager.post(f"{BASE}/jobs/{link.json()['id']}/cancel", headers=ADMIN)
    assert cancelled.json()["status"] == "cancelled"
    again = manager.post(f"{BASE}/jobs/{link.json()['id']}/cancel", headers=ADMIN)
    assert (again.status_code, again.json()["reason"]) == (409, "finished")


@pytest.mark.parametrize(
    ("body", "field", "code"),
    [
        ({"url": "http://archive.org/x.mp4"}, "url", "link_not_https"),
        ({"url": "https://redis-state/x.mp4"}, "url", "unsafe_destination"),
        ({"file_name": "x.iso"}, "file_name", "unsupported_type"),
        ({"checksum": "abc"}, "checksum", "invalid_checksum"),
        ({"subfolder": "../x"}, "subfolder", "invalid_path"),
        ({"license_ref": " "}, "license_ref", "blank"),
    ],
)
def test_bad_links_are_refused_on_the_field(  # noqa: PLR0917 (fixtures and parameters)
    manager: APIClient,
    library: Library,
    queued: list[Any],
    body: dict[str, Any],
    field: str,
    code: str,
) -> None:
    base = {
        "url": "https://archive.org/download/x/x.mp4",
        "library": str(library.pk),
        "rights_holder": "Public domain",
        "license_ref": "PD",
    }
    response = manager.post(f"{BASE}/links", {**base, **body}, format="json", headers=ADMIN)
    assert response.status_code == 400, response.json()
    codes = response.json().get("field_error_codes", {})
    assert codes.get(field) == [code] or response.json().get("reason") == code


def test_explicit_files_of_other_types_are_refused(
    manager: APIClient, library: Library, queued: list[Any]
) -> None:
    source = manager.post(f"{BASE}/sources", s3_body(), format="json", headers=ADMIN).json()
    response = manager.post(
        f"{BASE}/jobs",
        {
            "source": source["id"],
            "items": [{"path": "x/setup.exe", "is_dir": False}],
            "library": str(library.pk),
            "rights_holder": "PD",
            "license_ref": "PD",
        },
        format="json",
        headers=ADMIN,
    )
    assert response.status_code == 400
    assert (response.json()["reason"], response.json()["names"]) == (
        "unsupported_type",
        ["setup.exe"],
    )


def test_status_and_details(
    manager: APIClient, library: Library, django_assert_num_queries: Capture
) -> None:
    status = manager.get(f"{BASE}/status", headers=ADMIN).json()
    assert status["importer_online"] is False
    assert (status["max_concurrent"], status["reserve_bytes"], status["bandwidth_mbit"]) == (
        1,
        10 * 1000**3,
        0,
    )
    assert status["libraries"][0]["free_bytes"] > 0
    job = ImportJob.objects.create(
        origin="link",
        library=library,
        library_name=library.name,
        rights_holder="PD",
        license_ref="PD",
        status=JobStatus.DONE,
    )
    ImportFile.objects.create(
        job=job,
        library=library,
        storage_key="Film/Film.mkv",
        remote_path="https://x/Film.mkv",
        kind="video",
        size=5,
    )
    with django_assert_num_queries(3):
        detail = manager.get(f"{BASE}/jobs/{job.pk}", headers=ADMIN).json()
    assert detail["files"][0]["relative_path"] == "Film/Film.mkv"
    assert library.path not in json.dumps(detail)
