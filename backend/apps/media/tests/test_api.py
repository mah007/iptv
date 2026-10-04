"""Admin API of transcode jobs (SPEC §8.3.10): RBAC, actions, audit, query counts, the
live feed and the OpenAPI schema."""

import asyncio
import io
import json
from collections.abc import Callable, Iterator
from typing import Any
from uuid import uuid4

import pytest
import yaml
from django.conf import settings
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.catalog.models import Episode, FileState, MediaFile, Movie, Season, Series
from apps.conftest import AdminFactory
from apps.core.services import reset_settings_cache
from apps.core.stores import state_redis
from apps.library.models import Library
from apps.media import feed, services, tasks
from apps.media.api import JobStreamView
from apps.media.models import JobStatus, Rendition, RenditionKind, TranscodeJob

pytestmark = pytest.mark.django_db

ADMIN = {"host": settings.ADMIN_HOST}
JOBS = "/api/v1/admin/transcode-jobs"


@pytest.fixture(autouse=True)
def _isolated_settings_cache() -> Iterator[None]:
    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture(autouse=True)
def _no_broker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tasks.prepare_media_file, "delay", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks.run_transcode_job, "apply_async", lambda *_a, **_k: None)


def client_for(user: Any) -> APIClient:
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.fixture
def library(make_library: Callable[..., Library]) -> Library:
    return make_library()


def make_job(library: Library, *, episode: bool = False, **fields: Any) -> TranscodeJob:
    file = MediaFile.objects.create(
        library=library, storage_key=f"{uuid4().hex}.mkv", state=FileState.MATCHED, duration_ms=1000
    )
    if episode:
        series = Series.objects.create(title="Show")
        season = Season.objects.create(series=series, number=2)
        file.episodes.add(Episode.objects.create(season=season, number=3))
    else:
        file.movie = Movie.objects.create(title="Film")
        file.save()
    rendition = Rendition.objects.create(
        media_file=file, kind=RenditionKind.COMPAT_MP4, storage_key=file.pk.hex
    )
    return TranscodeJob.objects.create(media_file=file, rendition=rendition, **fields)


def test_list_with_titles_filters_and_constant_queries(
    owner_client: APIClient, library: Library, django_assert_max_num_queries: Any
) -> None:
    make_job(
        library, status=JobStatus.RUNNING, progress=42.5, backend="nvenc", encoder="h264_nvenc"
    )
    with django_assert_max_num_queries(12) as one:
        owner_client.get(JOBS, headers=ADMIN)
    for index in range(4):
        make_job(library, episode=bool(index % 2), status=JobStatus.FAILED, error="ffmpeg")
    with django_assert_max_num_queries(len(one.captured_queries)):
        response = owner_client.get(JOBS, headers=ADMIN)

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 5
    titles = {(row["title"] or {}).get("name") for row in body["results"]}
    assert titles == {"Film", "Show S02E03"}
    running = owner_client.get(JOBS, {"status": "running"}, headers=ADMIN).json()["results"]
    assert len(running) == 1
    row = running[0]
    assert (row["progress"], row["backend"], row["encoder"]) == (42.5, "nvenc", "h264_nvenc")
    assert row["file"]["library"] == library.name
    assert "storage_key" not in json.dumps(body)
    assert library.path not in json.dumps(body)


def test_rbac(make_admin: AdminFactory, library: Library) -> None:
    job = make_job(library, status=JobStatus.FAILED)
    viewer = client_for(make_admin(permissions=["library.view"]))
    assert viewer.get(JOBS, headers=ADMIN).status_code == 200
    assert viewer.post(f"{JOBS}/{job.pk}/retry", headers=ADMIN).status_code == 403
    outsider = client_for(make_admin(permissions=["customers.view"]))
    assert outsider.get(JOBS, headers=ADMIN).status_code == 403
    assert APIClient().get(JOBS, headers=ADMIN).status_code in (401, 403)


def test_actions_are_audited(owner_client: APIClient, owner: Any, library: Library) -> None:
    job = make_job(library)

    raised = owner_client.post(f"{JOBS}/{job.pk}/priority", {"priority": 8}, headers=ADMIN)
    assert raised.status_code == 200
    assert raised.json()["priority"] == 8
    invalid = owner_client.post(f"{JOBS}/{job.pk}/priority", {"priority": 12}, headers=ADMIN)
    assert invalid.status_code == 400

    cancelled = owner_client.post(f"{JOBS}/{job.pk}/cancel", headers=ADMIN)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    again = owner_client.post(f"{JOBS}/{job.pk}/cancel", headers=ADMIN)
    assert again.status_code == 409
    assert again.json()["code"] == "CONFLICT"

    retried = owner_client.post(f"{JOBS}/{job.pk}/retry", headers=ADMIN)
    assert retried.status_code == 200
    assert retried.json()["status"] == "queued"
    assert owner_client.post(f"{JOBS}/{uuid4()}/retry", headers=ADMIN).status_code == 404

    entries = AuditLog.objects.filter(target_id=str(job.pk), actor=owner)
    assert set(entries.values_list("action", flat=True)) == {
        "transcode_job.priority",
        "transcode_job.cancel",
        "transcode_job.retry",
    }


def test_stream_endpoint(
    owner_client: APIClient,
    make_admin: AdminFactory,
    library: Library,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_job(library, status=JobStatus.RUNNING)
    make_job(library, status=JobStatus.DONE)
    monkeypatch.setattr(JobStreamView, "limit", 0)
    response = owner_client.get(f"{JOBS}/stream", headers={**ADMIN, "Accept": "text/event-stream"})
    assert response.status_code == 200
    assert response["Content-Type"] == "text/event-stream"
    with pytest.warns(Warning, match="asynchronous iterators"):
        body = b"".join(response)
    head, _, data = body.decode().partition("data: ")
    assert head == "retry: 5000\nevent: snapshot\n"
    assert [job["status"] for job in json.loads(data)["jobs"]] == ["running"]
    denied = client_for(make_admin(permissions=["customers.view"])).get(
        f"{JOBS}/stream", headers={**ADMIN, "Accept": "text/event-stream"}
    )
    assert denied.status_code == 403


def test_feed_relays_published_jobs(library: Library) -> None:
    job = make_job(library, status=JobStatus.RUNNING, progress=12.0)

    async def run() -> list[bytes]:
        events = feed.job_events([], limit=1)
        chunks = [await anext(events)]
        await asyncio.to_thread(services.publish, job)
        await asyncio.to_thread(state_redis().publish, services.CHANNEL, b"not json")
        chunks += [chunk async for chunk in events]
        return chunks

    chunks = asyncio.run(run())
    assert chunks[0].startswith(b"retry: 5000\nevent: snapshot\n")
    assert chunks[1].startswith(b"event: job\n")
    assert json.loads(chunks[1].split(b"data: ", 1)[1])["progress"] == 12.0


def test_schema_ids() -> None:
    out = io.StringIO()
    call_command(
        "spectacular", "--urlconf", "config.urls_admin", "--validate", "--fail-on-warn", stdout=out
    )
    paths = yaml.safe_load(out.getvalue())["paths"]
    assert paths[JOBS]["get"]["operationId"] == "transcode_jobs_list"
    assert paths[f"{JOBS}/{{id}}/retry"]["post"]["operationId"] == "transcode_jobs_retry"
    assert paths[f"{JOBS}/{{id}}/cancel"]["post"]["operationId"] == "transcode_jobs_cancel"
    assert paths[f"{JOBS}/{{id}}/priority"]["post"]["operationId"] == "transcode_jobs_priority"
    stream = paths[f"{JOBS}/stream"]["get"]
    assert "text/event-stream" in stream["responses"]["200"]["content"]
