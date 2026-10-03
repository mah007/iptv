"""Admin API of libraries and scans, and the scan progress stream (SPEC §8.3)."""

import json
from collections.abc import AsyncGenerator, Callable
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.http import StreamingHttpResponse
from django.test import AsyncRequestFactory
from django.urls import resolve
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.catalog.models import Category, CategoryKind
from apps.conftest import AdminFactory
from apps.library import services, sse
from apps.library.models import Library, ScanJob, ScanStatus, ScanTrigger

pytestmark = pytest.mark.django_db

ADMIN = {"host": settings.ADMIN_HOST}
URL = "/api/v1/admin/libraries"
type Capture = Callable[..., Any]


@pytest.fixture
def no_tasks(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    from apps.library import tasks  # noqa: PLC0415

    sent: list[str] = []
    monkeypatch.setattr(tasks.scan_library, "delay", sent.append)
    return sent


def test_create_list_update_delete(
    owner_client: APIClient, media_root: Path, owner: User, django_assert_num_queries: Capture
) -> None:
    action = Category.objects.create(kind=CategoryKind.VOD, slug="a", name_en="A", name_ar="أ")
    response = owner_client.post(
        URL,
        {
            "name": "Movies",
            "kind": "movies",
            "path": "movies",
            "default_categories": [str(action.pk)],
            "scan_interval_min": 30,
        },
        headers=ADMIN,
    )
    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["path"] == str(media_root / "movies")
    assert (body["processing_policy"], body["scan_interval_min"], body["enabled"]) == (
        "ingest",
        30,
        True,
    )
    assert body["default_categories"][0]["id"] == str(action.pk)
    assert body["stats"]["files"] == 0
    assert body["last_scan"] is None
    owner_client.post(URL, {"name": "Series", "kind": "series", "path": "series"}, headers=ADMIN)
    library = Library.objects.get(name="Movies")
    ScanJob.objects.create(library=library, trigger=ScanTrigger.MANUAL, status=ScanStatus.DONE)
    with django_assert_num_queries(5):
        response = owner_client.get(URL, headers=ADMIN)
    rows = response.json()["results"]
    assert [row["name"] for row in rows] == ["Movies", "Series"]
    assert rows[0]["last_scan"]["status"] == "done"
    response = owner_client.patch(
        f"{URL}/{library.pk}", {"enabled": False, "name": "Films"}, headers=ADMIN
    )
    assert (response.json()["name"], response.json()["enabled"]) == ("Films", False)
    assert owner_client.get(f"{URL}/{library.pk}", headers=ADMIN).json()["name"] == "Films"
    assert owner_client.delete(f"{URL}/{library.pk}", headers=ADMIN).status_code == 204
    actions = AuditLog.objects.filter(action__startswith="library.").values_list(
        "action", flat=True
    )
    assert sorted(actions) == [
        "library.create",
        "library.create",
        "library.delete",
        "library.update",
    ]


def test_path_and_name_validation(owner_client: APIClient, media_root: Path) -> None:
    def create(**fields: Any) -> Any:
        body = {"name": "Movies", "kind": "movies", "path": "movies", **fields}
        return owner_client.post(URL, body, headers=ADMIN)

    assert create().status_code == 201
    response = create(name="Other", path="/etc")
    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert "path" in response.json()["field_errors"]
    assert create(name="Other", path="missing").status_code == 400
    (media_root / "movies" / "4k").mkdir()
    overlap = create(name="Other", path="movies/4k")
    assert (overlap.status_code, overlap.json()["code"]) == (409, "CONFLICT")
    assert create(path="series").status_code == 409  # the name is taken
    library = Library.objects.get()
    clash = owner_client.patch(f"{URL}/{library.pk}", {"path": "movies/4k"}, headers=ADMIN)
    assert clash.status_code == 200  # inside its own folder: no other library overlaps
    other = Library.objects.create(name="Series", kind="series", path=str(media_root / "series"))
    rename = owner_client.patch(f"{URL}/{other.pk}", {"name": "Movies"}, headers=ADMIN)
    assert rename.status_code == 409
    assert create(name="X", path="series", scan_interval_min=0).status_code == 400


def test_scan_now_and_history(
    owner_client: APIClient,
    make_library: Callable[..., Library],
    no_tasks: list[str],
    django_capture_on_commit_callbacks: Capture,
) -> None:
    library = make_library()
    with django_capture_on_commit_callbacks(execute=True):
        response = owner_client.post(f"{URL}/{library.pk}/scan", headers=ADMIN)
    assert response.status_code == 202
    body = response.json()
    assert (body["created"], body["job"]["status"], body["job"]["trigger"]) == (
        True,
        "queued",
        "manual",
    )
    assert no_tasks == [body["job"]["id"]]
    again = owner_client.post(f"{URL}/{library.pk}/scan", headers=ADMIN).json()
    assert (again["created"], again["job"]["id"]) == (False, body["job"]["id"])
    assert AuditLog.objects.filter(action="library.scan").count() == 1
    history = owner_client.get(f"/api/v1/admin/scans?library={library.pk}", headers=ADMIN).json()
    assert [row["id"] for row in history["results"]] == [body["job"]["id"]]
    detail = owner_client.get(f"/api/v1/admin/scans/{body['job']['id']}", headers=ADMIN)
    assert detail.json()["library"]["name"] == library.name


def test_rbac(make_admin: AdminFactory, make_library: Callable[..., Library]) -> None:
    library = make_library()
    viewer = APIClient()
    viewer.force_authenticate(make_admin("viewer"))
    assert viewer.get(URL, headers=ADMIN).status_code == 200
    assert viewer.get("/api/v1/admin/scans", headers=ADMIN).status_code == 200
    assert viewer.post(f"{URL}/{library.pk}/scan", headers=ADMIN).status_code == 403
    assert viewer.delete(f"{URL}/{library.pk}", headers=ADMIN).status_code == 403
    body = {"name": "X", "kind": "movies", "path": "movies"}
    assert viewer.post(URL, body, headers=ADMIN).status_code == 403
    support = APIClient()
    support.force_authenticate(make_admin("support"))
    assert support.get(URL, headers=ADMIN).status_code == 403


# --- The progress stream (server-sent events) ---------------------------------------------------


def _stream(
    user: User | None, library_id: object, *, publish: ScanJob | None = None
) -> tuple[int, list[str]]:
    """Call the async view directly (the admin URLconf routes to it; see the next test)."""
    factory = AsyncRequestFactory()

    async def scenario() -> tuple[int, list[str]]:
        request = factory.get(f"{URL}/{library_id}/scan/stream")

        async def auser() -> Any:
            return user if user is not None else AnonymousUser()

        request.auser = auser
        response = await sse.scan_stream(request, cast("UUID", library_id))
        if not isinstance(response, StreamingHttpResponse):
            return response.status_code, [response.content.decode()]  # type: ignore[attr-defined]
        chunks: list[str] = []
        stream = cast("AsyncGenerator[bytes]", response.streaming_content)
        try:
            for _ in range(3):  # retry hint, the latest scan, the subscription's ping
                chunks.append(_text(await anext(stream)))
            if publish is not None:
                await sync_to_async(services.publish)(publish)
                chunks.append(_text(await anext(stream)))
        finally:
            await stream.aclose()
        assert response["Content-Type"] == "text/event-stream"
        return response.status_code, chunks

    return async_to_sync(scenario)()


def _text(chunk: bytes | str) -> str:
    return chunk.decode() if isinstance(chunk, bytes) else chunk


def test_the_stream_is_routed_on_the_admin_host() -> None:
    match = resolve(f"{URL}/00000000-0000-0000-0000-000000000000/scan/stream", "config.urls_admin")
    assert match.func is sse.scan_stream


@pytest.mark.django_db(transaction=True)
def test_the_scan_stream_relays_progress(make_admin: AdminFactory, media_root: Path) -> None:
    library = Library.objects.create(name="Movies", kind="movies", path=str(media_root / "movies"))
    job = ScanJob.objects.create(library=library, trigger=ScanTrigger.MANUAL, found=3)
    status, chunks = _stream(make_admin("viewer"), library.pk, publish=job)
    assert status == 200
    assert chunks[0] == "retry: 5000\n\n"
    event, data = chunks[1].strip().split("\n")
    assert event == "event: scan"
    assert json.loads(data.removeprefix("data: "))["found"] == 3
    assert chunks[2] == ": ping\n\n"
    assert json.loads(chunks[3].strip().split("\n")[1].removeprefix("data: "))["id"] == str(job.pk)


@pytest.mark.django_db(transaction=True)
def test_the_scan_stream_checks_access(make_admin: AdminFactory, media_root: Path) -> None:
    library = Library.objects.create(name="Movies", kind="movies", path=str(media_root / "movies"))
    assert _stream(None, library.pk)[0] == 401
    assert _stream(make_admin("support"), library.pk)[0] == 403
    missing = UUID("00000000-0000-0000-0000-000000000000")
    status, body = _stream(make_admin("viewer"), missing)
    assert status == 404
    assert json.loads(body[0])["code"] == "NOT_FOUND"
