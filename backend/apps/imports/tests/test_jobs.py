"""Import jobs end to end against real rclone servers (SFTP, S3, WebDAV on loopback):
planning, the space and type checks, the copy, the atomic move, the scan queued, the
audit, cancel, retry and recovery (ADR-0025)."""

import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from apps.audit.models import AuditLog
from apps.core.services import set_setting
from apps.imports import rclone, services
from apps.imports.models import (
    FileKind,
    ImportFile,
    ImportJob,
    ImportSource,
    JobStatus,
    SourceKind,
    TestStatus,
)
from apps.imports.tests.conftest import PASSWORD, S3_KEY, S3_SECRET, USER, Servers
from apps.library.models import Library

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("allow_loopback", "importer_settings")]


@pytest.fixture
def scans(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    from apps.library import tasks  # noqa: PLC0415

    queued: list[tuple[str, str]] = []
    monkeypatch.setattr(tasks.scan_path, "delay", lambda *args: queued.append(args))
    return queued


@pytest.fixture
def no_dispatch(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, int]]:
    from apps.imports import tasks  # noqa: PLC0415

    sent: list[tuple[str, int]] = []
    monkeypatch.setattr(tasks.run_import, "delay", lambda *args: sent.append(args))
    return sent


def sftp_source(servers: Servers, *, password: str = PASSWORD, name: str = "NAS") -> ImportSource:
    return services.create_source(
        name=name,
        kind=SourceKind.SFTP,
        options={"host": "127.0.0.1", "port": servers.sftp_port, "username": USER},
        secrets={"password": password},
        actor=None,
        ip=None,
    )


def s3_source(servers: Servers) -> ImportSource:
    return services.create_source(
        name="Bucket",
        kind=SourceKind.S3,
        options={
            "provider": "other",
            "endpoint": f"http://127.0.0.1:{servers.s3_port}",
            "root": "media",
        },
        secrets={"access_key_id": S3_KEY, "secret_access_key": S3_SECRET},
        actor=None,
        ip=None,
    )


def webdav_source(servers: Servers) -> ImportSource:
    return services.create_source(
        name="DAV",
        kind=SourceKind.WEBDAV,
        options={"url": f"http://127.0.0.1:{servers.webdav_port}", "username": USER},
        secrets={"password": PASSWORD},
        actor=None,
        ip=None,
    )


def run_next(job: ImportJob) -> str | None:
    """Dispatch (the task is captured) and run the job like the importer would."""
    services.dispatch()
    job.refresh_from_db()
    return services.run(job.pk, job.dispatch_no)


@pytest.fixture
def library(make_library: Callable[..., Library]) -> Library:
    return make_library()


def test_a_source_test_reports_reachable_and_pins_the_sftp_host_key(servers: Servers) -> None:
    source = sftp_source(servers)
    result = services.test_source(source)
    assert result["ok"] is True
    assert result["code"] == TestStatus.OK
    source.refresh_from_db()
    assert source.last_test_status == TestStatus.OK
    pinned = source.options["host_keys"]
    assert pinned.startswith(("ssh-", "ecdsa-"))
    assert result["host_keys"] == pinned
    # The pinned key is now enforced: a different one is refused.
    source.options["host_keys"] = (
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"
    )
    source.save()
    assert services.test_source(source)["code"] == TestStatus.AUTH_FAILED


@pytest.mark.parametrize(
    ("kind", "expected"),
    [("password", TestStatus.AUTH_FAILED), ("port", TestStatus.UNREACHABLE)],
)
def test_a_failing_test_says_why(servers: Servers, kind: str, expected: str) -> None:
    if kind == "password":
        source = sftp_source(servers, password="wrong-" + "password")
    else:
        source = services.create_source(
            name="Closed",
            kind=SourceKind.SFTP,
            options={"host": "127.0.0.1", "port": 9, "username": USER},
            secrets={"password": PASSWORD},
            actor=None,
            ip=None,
        )
    result = services.test_source(source)
    assert (result["ok"], result["code"]) == (False, expected)
    assert PASSWORD not in json.dumps(result)


def test_s3_and_webdav_sources_answer(servers: Servers) -> None:
    for source in (s3_source(servers), webdav_source(servers)):
        result = services.test_source(source)
        assert result["code"] == TestStatus.OK, result
    missing = services.create_source(
        name="Gone",
        kind=SourceKind.S3,
        options={
            "provider": "other",
            "endpoint": f"http://127.0.0.1:{servers.s3_port}",
            "root": "nope",
        },
        secrets={"access_key_id": S3_KEY, "secret_access_key": S3_SECRET},
        actor=None,
        ip=None,
    )
    assert services.test_source(missing)["code"] == TestStatus.NOT_FOUND


def test_the_browser_lists_one_folder_with_sizes(servers: Servers) -> None:
    source = sftp_source(servers)
    top = services.browse(source, "")
    assert [entry["name"] for entry in top["entries"]] == ["Films"]
    films = services.browse(source, "Films")
    assert films["path"] == "Films"
    rows = {entry["name"]: entry for entry in films["entries"]}
    assert list(rows) == ["Metropolis (1927)", "Nosferatu (1922).mp4", "notes.txt"]
    assert rows["Metropolis (1927)"] == {
        "name": "Metropolis (1927)",
        "path": "Films/Metropolis (1927)",
        "is_dir": True,
        "size": None,
        "modified": rows["Metropolis (1927)"]["modified"],
        "importable": True,
    }
    assert rows["Nosferatu (1922).mp4"]["size"] == 200_000
    assert rows["notes.txt"]["importable"] is False
    assert services.browse(source, "../etc")["code"] == "invalid_path"
    assert services.browse(source, "Films/missing")["code"] == TestStatus.NOT_FOUND


def test_requests_are_answered_through_redis(
    servers: Servers, monkeypatch: pytest.MonkeyPatch
) -> None:
    from apps.imports import tasks  # noqa: PLC0415

    sent: list[tuple[Any, ...]] = []
    monkeypatch.setattr(tasks.remote_request, "delay", lambda *args: sent.append(args))
    source = sftp_source(servers)
    request_id = services.queue_request("browse", source, "Films")
    assert services.read_result(request_id) == {"status": "pending"}
    tasks.remote_request(*sent[0])
    answer = services.read_result(request_id)
    assert answer is not None
    assert answer["status"] == "done"
    assert answer["result"]["ok"] is True


def test_credentials_never_reach_rclones_command_line(
    servers: Servers, monkeypatch: pytest.MonkeyPatch, importer_settings: Path
) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []
    real_run, real_popen = subprocess.run, subprocess.Popen

    def run(args: list[str], **kwargs: Any) -> Any:
        calls.append((list(args), dict(kwargs.get("env") or {})))
        return real_run(args, **kwargs)

    def popen(args: list[str], **kwargs: Any) -> Any:
        calls.append((list(args), dict(kwargs.get("env") or {})))
        return real_popen(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(subprocess, "Popen", popen)
    source = sftp_source(servers)
    assert services.test_source(source)["ok"]
    assert calls
    for args, env in calls:
        assert PASSWORD not in " ".join(args)
        assert not any(PASSWORD in value for value in env.values())
        assert not any(name.startswith("RCLONE_CONFIG_") for name in env)
    # The per-run folder (and its config file) is gone afterwards.
    assert list(importer_settings.iterdir()) == []


def test_a_folder_and_a_file_are_imported_into_a_subfolder(
    servers: Servers, library: Library, scans: list[tuple[str, str]], no_dispatch: list[Any]
) -> None:
    source = sftp_source(servers)
    job = services.create_source_job(
        source=source,
        items=[
            {"path": "Films/Metropolis (1927)", "is_dir": True},
            {"path": "Films/Nosferatu (1922).mp4", "is_dir": False},
        ],
        library=library,
        subfolder="Classics",
        rights_holder="Public domain",
        license_ref="https://archive.org/details/metropolis",
        license_expires_at=None,
        actor=None,
        ip=None,
    )
    assert job.status == JobStatus.QUEUED
    assert run_next(job) == JobStatus.DONE
    job.refresh_from_db()
    root = Path(library.path) / "Classics"
    assert (root / "Metropolis (1927)" / "Metropolis (1927).mkv").stat().st_size == 300_000
    assert (root / "Metropolis (1927)" / "Metropolis (1927).en.srt").is_file()
    assert (root / "Nosferatu (1922).mp4").stat().st_size == 200_000
    assert not (root / "Metropolis (1927)" / "poster.jpg").exists()
    assert not (Path(library.path) / ".smart-iptv-import").exists()
    assert (job.files_total, job.files_done, job.bytes_done) == (3, 3, job.bytes_total)
    assert job.skipped == {"count": 1, "names": ["poster.jpg"]}
    records = {record.storage_key: record.kind for record in ImportFile.objects.filter(job=job)}
    assert records == {
        "Classics/Metropolis (1927)/Metropolis (1927).en.srt": FileKind.SUBTITLE,
        "Classics/Metropolis (1927)/Metropolis (1927).mkv": FileKind.VIDEO,
        "Classics/Nosferatu (1922).mp4": FileKind.VIDEO,
    }
    assert sorted(path for _library, path in scans) == ["Classics", "Classics/Metropolis (1927)"]
    actions = list(AuditLog.objects.filter(target_id=str(job.pk)).values_list("action", flat=True))
    assert sorted(actions) == ["import.finish", "import.start"]
    finish = AuditLog.objects.get(action="import.finish", target_id=str(job.pk))
    assert finish.after is not None
    assert finish.after["status"] == "done"


@pytest.mark.parametrize("make", [s3_source, webdav_source])
def test_s3_and_webdav_imports(
    servers: Servers,
    library: Library,
    scans: list[Any],
    no_dispatch: list[Any],
    make: Callable[[Servers], ImportSource],
) -> None:
    job = services.create_source_job(
        source=make(servers),
        items=[{"path": "Films/Nosferatu (1922).mp4", "is_dir": False}],
        library=library,
        subfolder="",
        rights_holder="Public domain",
        license_ref="PD-1922",
        license_expires_at=None,
        actor=None,
        ip=None,
    )
    assert run_next(job) == JobStatus.DONE
    assert (Path(library.path) / "Nosferatu (1922).mp4").stat().st_size == 200_000


def test_types_rights_and_targets_are_checked(
    servers: Servers, library: Library, scans: list[Any], no_dispatch: list[Any]
) -> None:
    source = sftp_source(servers)
    base: dict[str, Any] = {
        "source": source,
        "library": library,
        "subfolder": "",
        "license_expires_at": None,
        "actor": None,
        "ip": None,
    }
    with pytest.raises(services.ImportRefused) as error:
        services.create_source_job(
            items=[{"path": "Films/notes.txt", "is_dir": False}],
            rights_holder="X",
            license_ref="Y",
            **base,
        )
    assert (error.value.code, error.value.params["names"]) == ("unsupported_type", ["notes.txt"])
    with pytest.raises(services.ImportRefused) as error:
        services.create_source_job(
            items=[{"path": "Films/Nosferatu (1922).mp4", "is_dir": False}],
            rights_holder=" ",
            license_ref="Y",
            **base,
        )
    assert error.value.params["field"] == "rights_holder"
    with pytest.raises(services.ImportRefused) as error:
        services.create_source_job(
            items=[{"path": "Films/a.mkv", "is_dir": False}],
            rights_holder="X",
            license_ref="Y",
            **{**base, "subfolder": "../up"},
        )
    assert error.value.params["field"] == "subfolder"
    (Path(library.path) / "Nosferatu (1922).mp4").write_bytes(b"already here")
    job = services.create_source_job(
        items=[{"path": "Films/Nosferatu (1922).mp4", "is_dir": False}],
        rights_holder="X",
        license_ref="Y",
        **base,
    )
    assert run_next(job) == JobStatus.FAILED
    job.refresh_from_db()
    assert (job.error_code, job.error_params["names"]) == (
        "target_exists",
        ["Nosferatu (1922).mp4"],
    )
    assert (Path(library.path) / "Nosferatu (1922).mp4").read_bytes() == b"already here"


def test_an_import_that_would_not_fit_is_refused_before_copying(
    servers: Servers, library: Library, scans: list[Any], no_dispatch: list[Any]
) -> None:
    set_setting("library.import_reserve_gb", value=100_000, actor=None)
    job = services.create_source_job(
        source=sftp_source(servers),
        items=[{"path": "Films/Metropolis (1927)", "is_dir": True}],
        library=library,
        subfolder="",
        rights_holder="Public domain",
        license_ref="PD",
        license_expires_at=None,
        actor=None,
        ip=None,
    )
    assert run_next(job) == JobStatus.FAILED
    job.refresh_from_db()
    assert job.error_code == "insufficient_space"
    assert job.error_params["reserve"] == 100_000 * 1000**3
    assert job.error_params["needed"] > 0
    assert not list(Path(library.path).rglob("*.mkv"))


def test_a_cancelled_import_stops_and_leaves_nothing(
    servers: Servers,
    library: Library,
    scans: list[Any],
    no_dispatch: list[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = services.create_source_job(
        source=sftp_source(servers),
        items=[{"path": "Films/Metropolis (1927)", "is_dir": True}],
        library=library,
        subfolder="",
        rights_holder="Public domain",
        license_ref="PD",
        license_expires_at=None,
        actor=None,
        ip=None,
    )
    # The admin's cancel, as the copy loop sees it on its first look.
    monkeypatch.setattr(services.Reporter, "should_stop", lambda self: True)
    assert run_next(job) == JobStatus.CANCELLED
    job.refresh_from_db()
    assert job.finished_at is not None
    assert not list(Path(library.path).rglob("*"))
    assert scans == []


def test_progress_reports_read_the_cancel_flag_and_watch_the_disk(
    library: Library, no_dispatch: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    job = ImportJob.objects.create(
        origin="link", library=library, rights_holder="PD", license_ref="PD", bytes_total=1000
    )
    reporter = services.Reporter(job, library.path)
    reporter.update(done=400, total=1000, speed=100.0, files_done=0)
    job.refresh_from_db()
    assert (job.bytes_done, job.speed_bps, job.eta_s) == (400, 100, 6)
    assert reporter.should_stop() is False
    ImportJob.objects.filter(pk=job.pk).update(cancel_requested=True)
    reporter.last = 0.0
    reporter.update(done=500, total=1000, speed=100.0)
    assert reporter.should_stop() is True
    set_setting("library.import_reserve_gb", value=100_000, actor=None)
    reporter.last = reporter.space_at = 0.0
    with pytest.raises(services.ImportRefused) as error:
        reporter.update(done=600, total=1000, speed=100.0)
    assert error.value.code == "insufficient_space"


def test_queued_imports_cancel_at_once_and_finished_ones_cannot(
    servers: Servers, library: Library, no_dispatch: list[Any]
) -> None:
    set_setting("library.import_max_concurrent", value=1, actor=None)
    source = sftp_source(servers)
    jobs = [
        services.create_source_job(
            source=source,
            items=[{"path": f"Films/{name}", "is_dir": False}],
            library=library,
            subfolder="",
            rights_holder="PD",
            license_ref="PD",
            license_expires_at=None,
            actor=None,
            ip=None,
        )
        for name in ("a.mkv", "b.mkv")
    ]
    first, second = (ImportJob.objects.get(pk=job.pk) for job in jobs)
    # One at a time: the first is dispatched, the second waits.
    assert first.dispatched_at is not None
    assert second.dispatched_at is None
    cancelled = services.cancel_job(second, actor=None, ip=None)
    assert cancelled.status == JobStatus.CANCELLED
    assert AuditLog.objects.filter(action="import.cancel", target_id=str(second.pk)).exists()
    with pytest.raises(services.ImportRefused) as error:
        services.cancel_job(second, actor=None, ip=None)
    assert error.value.code == "finished"


def test_a_transient_failure_is_retried_once(
    servers: Servers,
    library: Library,
    scans: list[Any],
    no_dispatch: list[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = services.create_source_job(
        source=sftp_source(servers),
        items=[{"path": "Films/Nosferatu (1922).mp4", "is_dir": False}],
        library=library,
        subfolder="",
        rights_holder="PD",
        license_ref="PD",
        license_expires_at=None,
        actor=None,
        ip=None,
    )
    real_copy = rclone.copy
    failures = iter([True, True])

    def flaky(*args: Any, **kwargs: Any) -> None:
        if next(failures, False):
            raise rclone.RcloneError(TestStatus.UNREACHABLE, "connection reset", transient=True)
        real_copy(*args, **kwargs)

    monkeypatch.setattr(rclone, "copy", flaky)
    assert run_next(job) == JobStatus.QUEUED
    job.refresh_from_db()
    assert (job.attempts, job.error_code) == (1, TestStatus.UNREACHABLE)
    assert job.retry_at is not None
    assert services.dispatch() == 0  # waits for retry_at
    ImportJob.objects.filter(pk=job.pk).update(retry_at=None)
    assert run_next(job) == JobStatus.FAILED  # the second failure is final
    job.refresh_from_db()
    assert job.attempts == 2


def test_stale_messages_are_ignored_and_dead_runs_recovered(
    servers: Servers, library: Library, no_dispatch: list[Any]
) -> None:
    job = services.create_source_job(
        source=sftp_source(servers),
        items=[{"path": "Films/Nosferatu (1922).mp4", "is_dir": False}],
        library=library,
        subfolder="",
        rights_holder="PD",
        license_ref="PD",
        license_expires_at=None,
        actor=None,
        ip=None,
    )
    job.refresh_from_db()
    assert services.run(job.pk, job.dispatch_no + 1) is None
    # A run whose importer died: running, no lease, no progress for a while.
    ImportJob.objects.filter(pk=job.pk).update(status=JobStatus.RUNNING, attempts=1)
    ImportJob.objects.filter(pk=job.pk).update(updated_at=job.created_at.replace(year=2020))
    staging = Path(library.path, ".smart-iptv-import", "0" * 32)
    staging.mkdir(parents=True)
    (staging / "left.mkv").write_bytes(b"x")
    result = services.sweep()
    assert result["recovered"] == 1
    assert result["removed"] == 1
    job.refresh_from_db()
    assert (job.status, job.error_code) == (JobStatus.QUEUED, "worker_lost")
    assert not staging.exists()
    ImportJob.objects.filter(pk=job.pk).update(
        status=JobStatus.RUNNING, attempts=2, updated_at=job.created_at.replace(year=2020)
    )
    services.recover_stale_jobs()
    job.refresh_from_db()
    assert (job.status, job.error_code) == (JobStatus.FAILED, "worker_lost")


def test_what_rclone_writes_back_is_stored(
    servers: Servers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refreshed OAuth token (OneDrive rotates them) is kept, encrypted."""
    token = json.dumps(
        {"access_token": "a1", "refresh_token": "r1", "expiry": "2026-01-01T00:00:00Z"}
    )
    source = services.create_source(
        name="Drive",
        kind=SourceKind.DRIVE,
        options={},
        secrets={"token": token},
        actor=None,
        ip=None,
    )
    fresh = json.dumps(
        {"access_token": "a2", "refresh_token": "r2", "expiry": "2027-01-01T00:00:00Z"}
    )

    def lsjson(current: rclone.Session, remote: str, **_kwargs: Any) -> list[dict[str, Any]]:
        text = current.config.read_text().replace(
            json.dumps(json.loads(token), separators=(",", ":")), fresh
        )
        current.config.write_text(text)
        return []

    monkeypatch.setattr(rclone, "lsjson", lsjson)
    assert services.test_source(source)["ok"]
    source.refresh_from_db()
    refreshed = json.loads(services.source_secrets(source)["token"])
    assert refreshed["refresh_token"] == "r2"  # noqa: S105 (a made-up token)
    assert "a2" not in source.credentials_encrypted


def test_onedrive_drives_are_discovered_through_rclones_config(
    servers: Servers, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = json.dumps({"access_token": "a", "refresh_token": "r"})
    source = services.create_source(
        name="OneDrive",
        kind=SourceKind.ONEDRIVE,
        options={},
        secrets={"token": token},
        actor=None,
        ip=None,
    )
    seen: list[list[str]] = []

    def run(current: rclone.Session, args: list[str], *, timeout: int) -> str:
        seen.append(args)
        if "--continue" not in args:
            return json.dumps(
                {
                    "State": "choose_drive",
                    "Option": {"Default": "b!drive", "Examples": []},
                    "Error": "",
                }
            )
        with current.config.open("a") as handle:
            handle.write("drive_id = b!drive\ndrive_type = personal\n")
        return json.dumps({"State": "", "Option": None, "Error": ""})

    monkeypatch.setattr(rclone, "_run", run)
    monkeypatch.setattr(rclone, "lsjson", lambda *_a, **_k: [])
    assert services.test_source(source)["ok"]
    source.refresh_from_db()
    assert (source.options["drive_id"], source.options["drive_type"]) == ("b!drive", "personal")
    assert seen[1][-3:] == ["--result", "b!drive", "--non-interactive"]
    assert all(token not in " ".join(args) for args in seen)


def test_sources_with_active_jobs_cannot_be_deleted(
    servers: Servers, library: Library, no_dispatch: list[Any]
) -> None:
    source = sftp_source(servers)
    services.create_source_job(
        source=source,
        items=[{"path": "Films/Nosferatu (1922).mp4", "is_dir": False}],
        library=library,
        subfolder="",
        rights_holder="PD",
        license_ref="PD",
        license_expires_at=None,
        actor=None,
        ip=None,
    )
    with pytest.raises(services.ImportRefused) as error:
        services.delete_source(source, actor=None, ip=None)
    assert error.value.code == "source_busy"
    assert os.environ.get("RCLONE_CONFIG_SRC_PASS") is None


def test_the_tasks_wrap_the_services(no_dispatch: list[Any]) -> None:
    from uuid import uuid4  # noqa: PLC0415

    from apps.imports import tasks  # noqa: PLC0415

    assert tasks.run_import(str(uuid4()), 1) is None
    assert set(tasks.sweep_imports()) == {"recovered", "removed", "dispatched"}
    assert services.importer_online() is True  # the sweep beat


def test_the_round_trip_command_is_for_the_dev_stack_only(settings: Any) -> None:
    from django.core.management import CommandError, call_command  # noqa: PLC0415

    settings.DEBUG = False
    with pytest.raises(CommandError, match="dev stack"):
        call_command(
            "import_test", "--sftp-host", "x", "--sftp-user", "u", "--link", "https://x/a.mp4"
        )


def test_a_done_import_stays_done_when_its_scan_cannot_be_queued(
    servers: Servers, library: Library, no_dispatch: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from apps.library import tasks  # noqa: PLC0415

    def broken(*_args: Any) -> None:
        raise ConnectionError("broker down")

    monkeypatch.setattr(tasks.scan_path, "delay", broken)
    job = services.create_source_job(
        source=sftp_source(servers),
        items=[{"path": "Films/Nosferatu (1922).mp4", "is_dir": False}],
        library=library,
        subfolder="",
        rights_holder="PD",
        license_ref="PD",
        license_expires_at=None,
        actor=None,
        ip=None,
    )
    assert run_next(job) == JobStatus.DONE
    assert (Path(library.path) / "Nosferatu (1922).mp4").is_file()
