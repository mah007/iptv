"""Xtream VOD sources: encrypted credentials, browsing and an end-to-end import."""

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from apps.imports import direct, services, xtream
from apps.imports.models import ImportFile, ImportSource, JobStatus, SourceKind
from apps.library.models import Library

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("importer_settings")]

BASE = "http://8.8.8.8:8080"
USER = "viewer"
PASSWORD = "subscription-" + "secret"
FILM = b"synthetic-movie-bytes" * 10_000


def handler(request: httpx.Request) -> httpx.Response:  # noqa: PLR0911
    if request.url.path == "/player_api.php":
        if (
            request.url.params.get("username") != USER
            or request.url.params.get("password") != PASSWORD
        ):
            return httpx.Response(200, json={"user_info": {"auth": 0}})
        action = request.url.params.get("action")
        if not action:
            return httpx.Response(200, json={"user_info": {"auth": 1, "status": "Active"}})
        if action == "get_vod_categories":
            return httpx.Response(200, json=[{"category_id": "7", "category_name": "Classics"}])
        row = {
            "stream_id": 42,
            "name": "Metropolis (1927)",
            "container_extension": "mp4",
            "category_id": "7",
        }
        if action == "get_vod_streams":
            return httpx.Response(200, json=[row])
        if action == "get_vod_info":
            return httpx.Response(200, json={"movie_data": row, "info": {}})
    if request.url.path == f"/movie/{USER}/{PASSWORD}/42.mp4":
        if request.method == "HEAD":
            return httpx.Response(200, headers={"Content-Length": str(len(FILM))})
        wanted = request.headers.get("range", "")
        start = int(wanted.removeprefix("bytes=").removesuffix("-")) if wanted else 0
        body = FILM[start:]
        headers = {"Content-Length": str(len(body)), "Accept-Ranges": "bytes"}
        if start:
            headers["Content-Range"] = f"bytes {start}-{len(FILM) - 1}/{len(FILM)}"
        return httpx.Response(206 if start else 200, headers=headers, stream=httpx.ByteStream(body))
    return httpx.Response(404)


@pytest.fixture(autouse=True)
def transport(monkeypatch: pytest.MonkeyPatch) -> None:
    mocked = httpx.MockTransport(handler)
    monkeypatch.setattr(xtream, "_transport", mocked)
    monkeypatch.setattr(direct, "_transport", mocked)


@pytest.fixture
def source() -> ImportSource:
    return services.create_source(
        name="Licensed VOD",
        kind=SourceKind.XTREAM,
        options={"url": BASE},
        secrets={"account_username": USER, "password": PASSWORD},
        actor=None,
        ip=None,
    )


@pytest.fixture
def library(make_library: Callable[..., Library]) -> Library:
    return make_library()


def test_source_credentials_and_catalog(source: ImportSource) -> None:
    assert source.options == {"url": BASE}
    assert services.source_secrets(source) == {
        "account_username": USER,
        "password": PASSWORD,
    }
    assert services.test_source(source)["code"] == "ok"
    top = services.browse(source, "")
    assert top["entries"] == [
        {
            "name": "Classics",
            "path": "category/7",
            "is_dir": True,
            "size": None,
            "modified": None,
            "importable": False,
        }
    ]
    listing = services.browse(source, "category/7")
    assert listing["entries"][0]["name"] == "Metropolis (1927).mp4"
    assert listing["entries"][0]["path"] == "movie/42/Metropolis (1927).mp4"


def test_movie_is_imported_without_persisting_its_stream_url(
    source: ImportSource,
    library: Library,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.imports import tasks  # noqa: PLC0415
    from apps.library import tasks as library_tasks  # noqa: PLC0415

    monkeypatch.setattr(tasks.run_import, "delay", lambda *_args: None)
    monkeypatch.setattr(library_tasks.scan_path, "delay", lambda *_args: None)
    job = services.create_source_job(
        source=source,
        items=[{"path": "movie/42/Metropolis (1927).mp4", "is_dir": False}],
        library=library,
        subfolder="Classics",
        rights_holder="Licensed distributor",
        license_ref="CONTRACT-2026-10",
        license_expires_at=None,
        actor=None,
        ip=None,
    )
    services.dispatch()
    job.refresh_from_db()
    assert services.run(job.pk, job.dispatch_no) == JobStatus.DONE
    placed = Path(library.path, "Classics", "Metropolis (1927).mp4")
    assert placed.read_bytes() == FILM
    record = ImportFile.objects.get(job=job)
    assert record.remote_path == "movie/42/Metropolis (1927).mp4"
    assert USER not in record.remote_path
    assert PASSWORD not in record.remote_path


def test_bad_account_and_paths_are_named(source: ImportSource) -> None:
    source.credentials_encrypted = services._encrypt(
        {"account_username": USER, "password": "wrong"}
    )
    source.save(update_fields=["credentials_encrypted"])
    assert services.test_source(source)["code"] == "auth_failed"
    assert services.browse(source, "../../etc")["code"] == "invalid_path"


def test_xtream_options_and_file_names_are_safe() -> None:
    assert xtream.safe_name("A/B\\C", "mp4", "9") == "A B C.mp4"
    assert xtream.schemes("https://vod.example") == frozenset({"https"})
    assert xtream.schemes("http://vod.example") == frozenset({"http", "https"})
    with pytest.raises(services.ImportRefused) as error:
        services.create_source(
            name="Missing account",
            kind=SourceKind.XTREAM,
            options={"url": "https://vod.example"},
            secrets={"password": "only-one-secret"},
            actor=None,
            ip=None,
        )
    assert error.value.params["field"] == "account_username"


def test_xtream_rejects_api_urls_and_tampered_selections(
    source: ImportSource,
    library: Library,
) -> None:
    with pytest.raises(services.ImportRefused) as error:
        services.create_source(
            name="API URL",
            kind=SourceKind.XTREAM,
            options={"url": "https://vod.example/player_api.php?username=leaked"},
            secrets={"account_username": USER, "password": PASSWORD},
            actor=None,
            ip=None,
        )
    assert error.value.code == "invalid_url"
    with pytest.raises(services.ImportRefused) as error:
        services.create_source_job(
            source=source,
            items=[{"path": "category/7/fake.mp4", "is_dir": False}],
            library=library,
            subfolder="",
            rights_holder="Licensed distributor",
            license_ref="CONTRACT-2026-10",
            license_expires_at=None,
            actor=None,
            ip=None,
        )
    assert error.value.code == "unsupported_type"
