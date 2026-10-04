# ruff: noqa: RUF001 (Arabic text)
"""Admin API of a title's media (SPEC §8.3 Title detail, §10; ADR-0014): renditions with
disk use, reprocess, tracks (edit, upload, delete), images (TMDB alternatives, uploads,
primary), rematch and the rendition cleanup, with RBAC, audit and query counts."""

import io
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.catalog.models import (
    Episode,
    FileState,
    ImageKind,
    MediaFile,
    MediaImage,
    Movie,
    Season,
    Series,
)
from apps.conftest import AdminFactory
from apps.core.services import reset_settings_cache
from apps.core.stores import cache_redis
from apps.library.models import Library
from apps.media import artwork, tasks
from apps.media.models import AudioTrack, JobStatus, Rendition, SubtitleTrack, TranscodeJob
from apps.metadata.tmdb import TMDBClient

pytestmark = pytest.mark.django_db

ADMIN = {"host": settings.ADMIN_HOST}
TITLES = "/api/v1/admin/titles"
ARABIC_SRT = "1\r\n00:00:01,000 --> 00:00:02,000\r\nمرحبا بالعالم\r\n".encode("cp1256")


@pytest.fixture(autouse=True)
def _isolated_settings_cache() -> Iterator[None]:
    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, tuple[Any, ...]]]:
    """Tasks the API queues, captured (and on_commit run at once)."""
    calls: list[tuple[str, tuple[Any, ...]]] = []
    for name in (
        "publish_presentations",
        "reprocess_media_file",
        "store_title_image",
        "prepare_media_file",
    ):
        task = getattr(tasks, name)
        monkeypatch.setattr(task, "delay", lambda *a, _n=name, **k: calls.append((_n, (*a, k))))
    monkeypatch.setattr(
        tasks.cleanup_renditions, "delay", lambda **k: calls.append(("cleanup", (k,)))
    )
    monkeypatch.setattr(tasks.run_transcode_job, "apply_async", lambda *_a, **_k: None)
    monkeypatch.setattr("apps.media.titles.transaction.on_commit", lambda fn, **_k: fn())
    return calls


@pytest.fixture
def library(make_library: Callable[..., Library]) -> Library:
    return make_library()


def media_file(library: Library, **fields: Any) -> MediaFile:
    values: dict[str, Any] = {
        "library": library,
        "storage_key": f"{uuid4().hex}.mkv",
        "state": FileState.MATCHED,
        "duration_ms": 5_400_000,
        "xxhash64": "0011223344556677",
        "video_codec": "hevc",
        "width": 1920,
        "height": 1080,
        "hdr": "sdr",
    }
    values.update(fields)
    return MediaFile.objects.create(**values)


def movie_with_media(library: Library) -> tuple[Movie, MediaFile]:
    movie = Movie.objects.create(title="Inception", tmdb_id=27205)
    file = media_file(library, movie=movie, storage_key="Inception (2010)/Inception.mkv")
    key = file.pk.hex
    Rendition.objects.create(media_file=file, kind="compat_mp4", name="compat", storage_key=key,
                             status="ready", size=1000, height=1080)  # fmt: skip
    Rendition.objects.create(media_file=file, kind="hls_master", name="hls", storage_key=key,
                             status="ready", size=50, height=1080)  # fmt: skip
    Rendition.objects.create(media_file=file, kind="hls_master", name="hls720", storage_key=key,
                             status="ready", size=1, height=720)  # fmt: skip
    for name, height in (("v0", 1080), ("v1", 720)):
        Rendition.objects.create(media_file=file, kind="hls_variant", name=name, storage_key=key,
                                 status="ready", size=400, height=height)  # fmt: skip
    Rendition.objects.create(media_file=file, kind="uhd", name="uhd", storage_key=key,
                             status="ready", size=9000, details={"linked": True})  # fmt: skip
    AudioTrack.objects.create(media_file=file, stream_index=1, language="eng", codec="ac3",
                              channels=6, default=True)  # fmt: skip
    AudioTrack.objects.create(media_file=file, stream_index=2, language="ara", codec="aac",
                              channels=2)  # fmt: skip
    SubtitleTrack.objects.create(media_file=file, stream_index=3, codec="subrip", format="srt",
                                 language="eng", status="ready", storage_key="3.eng",
                                 cues=10)  # fmt: skip
    SubtitleTrack.objects.create(media_file=file, external=True, format="srt", codec="srt",
                                 sidecar_path="Inception (2010)/Inception.ar.srt",
                                 language="ara", status="ready", storage_key="x1.ara",
                                 encoding="cp1256", cues=10)  # fmt: skip
    TranscodeJob.objects.create(media_file=file, profile="thumbnails", status=JobStatus.QUEUED)
    return movie, file


# --- Renditions ------------------------------------------------------------------------------


def test_renditions_with_disk_use_tracks_and_jobs(
    owner_client: APIClient, library: Library, django_assert_max_num_queries: Any
) -> None:
    movie, _file = movie_with_media(library)

    with django_assert_max_num_queries(16):
        response = owner_client.get(f"{TITLES}/{movie.pk}/renditions", headers=ADMIN)

    assert response.status_code == 200
    body = response.json()
    assert body["title"] == {"kind": "movie", "id": str(movie.pk), "name": "Inception"}
    [entry] = body["files"]
    assert entry["relative_path"] == "Inception (2010)/Inception.mkv"
    assert "storage_key" not in json.dumps(body)
    assert entry["disk_bytes"] == 1000 + 50 + 1 + 400 + 400  # the UHD link takes no space
    renditions = {(r["kind"], r["name"]): r for r in entry["renditions"]}
    assert renditions[("uhd", "uhd")]["disk_bytes"] == 0
    assert renditions[("compat_mp4", "compat")]["disk_bytes"] == 1000
    assert [a["language"] for a in entry["audio"]] == ["eng", "ara"]
    origins = {s["language"]: (s["origin"], s["relative_path"]) for s in entry["subtitles"]}
    assert origins == {
        "eng": ("embedded", ""),
        "ara": ("sidecar", "Inception (2010)/Inception.ar.srt"),
    }
    assert [j["profile"] for j in entry["jobs"]] == ["thumbnails"]
    same = owner_client.get(f"{TITLES}/{movie.pk}/tracks", headers=ADMIN)
    assert same.json() == body


def test_series_and_episodes_resolve_and_unknown_ids_404(
    owner_client: APIClient, library: Library
) -> None:
    series = Series.objects.create(title="Show")
    season = Season.objects.create(series=series, number=1)
    episode = Episode.objects.create(season=season, number=2)
    file = media_file(library)
    file.episodes.add(episode)

    by_series = owner_client.get(f"{TITLES}/{series.pk}/renditions", headers=ADMIN).json()
    by_episode = owner_client.get(f"{TITLES}/{episode.pk}/renditions", headers=ADMIN).json()
    assert by_series["title"]["kind"] == "series"
    assert by_episode["title"] == {"kind": "episode", "id": str(episode.pk), "name": "Show S01E02"}
    assert [f["id"] for f in by_series["files"]] == [str(file.pk)]
    assert [f["id"] for f in by_episode["files"]] == [str(file.pk)]
    missing = owner_client.get(f"{TITLES}/{uuid4()}/renditions", headers=ADMIN)
    assert (missing.status_code, missing.json()["code"]) == (404, "NOT_FOUND")


def test_deleting_renditions(owner_client: APIClient, library: Library, queued: list[Any]) -> None:
    movie, file = movie_with_media(library)
    TranscodeJob.objects.filter(media_file=file).update(status=JobStatus.DONE)
    rows = {(r.kind, r.name): r for r in Rendition.objects.filter(media_file=file)}
    url = f"{TITLES}/{movie.pk}/renditions"

    # The ladder takes its rungs and capped presentations along; the UHD link stays.
    response = owner_client.delete(f"{url}/{rows[('hls_master', 'hls')].pk}", headers=ADMIN)
    assert response.status_code == 204
    left = set(Rendition.objects.filter(media_file=file).values_list("kind", "name"))
    assert left == {("compat_mp4", "compat"), ("uhd", "uhd")}
    assert ("publish_presentations", (str(file.pk), {})) in queued
    log = AuditLog.objects.get(action="rendition.delete")
    assert "hls_variant:v0" in (log.before or {})["renditions"]

    for kind, code in (("source", 409), ("hls_variant", 409)):
        extra = Rendition.objects.create(
            media_file=file, kind=kind, name="v9" if kind == "hls_variant" else "source",
            storage_key=file.pk.hex, status="ready",
        )  # fmt: skip
        assert owner_client.delete(f"{url}/{extra.pk}", headers=ADMIN).status_code == code
    assert owner_client.delete(f"{url}/{uuid4()}", headers=ADMIN).status_code == 404
    TranscodeJob.objects.create(media_file=file, profile="hls", status=JobStatus.RUNNING)
    compat = rows[("compat_mp4", "compat")]
    assert owner_client.delete(f"{url}/{compat.pk}", headers=ADMIN).status_code == 409


def test_reprocess_queues_the_worker(
    owner_client: APIClient, library: Library, queued: list[Any]
) -> None:
    movie, file = movie_with_media(library)
    url = f"{TITLES}/{movie.pk}/reprocess"

    response = owner_client.post(
        url, {"outputs": ["hls", "subtitles"]}, format="json", headers=ADMIN
    )

    assert response.status_code == 202
    assert response.json() == {"files": [str(file.pk)], "outputs": ["hls", "subtitles"]}
    assert [c for c in queued if c[0] == "reprocess_media_file"] == [
        ("reprocess_media_file", (str(file.pk), ["hls", "subtitles"], {}))
    ]
    everything = owner_client.post(url, {"media_file": str(file.pk)}, format="json", headers=ADMIN)
    assert everything.json()["outputs"] == [
        "compat_mp4", "hls", "uhd", "thumbnails", "subtitles",
    ]  # fmt: skip
    other = owner_client.post(url, {"media_file": str(uuid4())}, format="json", headers=ADMIN)
    assert other.status_code == 400
    bad = owner_client.post(url, {"outputs": ["dash"]}, format="json", headers=ADMIN)
    assert bad.status_code == 400
    assert AuditLog.objects.filter(action="title.reprocess").count() == 2
    empty = Movie.objects.create(title="Nothing")
    assert owner_client.post(f"{TITLES}/{empty.pk}/reprocess", {}, format="json",
                             headers=ADMIN).status_code == 409  # fmt: skip


# --- Tracks ----------------------------------------------------------------------------------


def test_editing_tracks(owner_client: APIClient, library: Library, queued: list[Any]) -> None:
    movie, file = movie_with_media(library)
    arabic_audio = AudioTrack.objects.get(media_file=file, language="ara")
    url = f"{TITLES}/{movie.pk}/tracks/{arabic_audio.pk}"

    response = owner_client.patch(
        url, {"default": True, "title": "عربي"}, format="json", headers=ADMIN
    )

    assert response.status_code == 200
    body = response.json()
    assert (body["kind"], body["audio"]["default"], body["audio"]["title"]) == (
        "audio",
        True,
        "عربي",
    )
    assert body["subtitle"] is None
    assert not AudioTrack.objects.get(media_file=file, language="eng").default
    assert queued[-1] == ("publish_presentations", (str(file.pk), {}))

    sidecar = SubtitleTrack.objects.get(media_file=file, external=True)
    response = owner_client.patch(
        f"{TITLES}/{movie.pk}/tracks/{sidecar.pk}",
        {"language": "ar-SA", "forced": True},
        format="json",
        headers=ADMIN,
    )
    assert response.status_code == 200
    assert (response.json()["subtitle"]["language"], response.json()["subtitle"]["forced"]) == (
        "ara",
        True,
    )
    bad = owner_client.patch(
        f"{TITLES}/{movie.pk}/tracks/{sidecar.pk}",
        {"language": "klingon"},
        format="json",
        headers=ADMIN,
    )
    assert bad.status_code == 400
    assert bad.json()["field_errors"]["language"]
    other = Movie.objects.create(title="Other")
    assert owner_client.patch(f"{TITLES}/{other.pk}/tracks/{sidecar.pk}", {}, format="json",
                              headers=ADMIN).status_code == 404  # fmt: skip
    log = AuditLog.objects.filter(action="track.update").order_by("at").first()
    assert log is not None
    assert ((log.before or {})["default"], (log.after or {})["default"]) == (False, True)


def test_uploading_and_deleting_a_subtitle(
    owner_client: APIClient, library: Library, queued: list[Any]
) -> None:
    movie, file = movie_with_media(library)
    url = f"{TITLES}/{movie.pk}/tracks"
    upload = SimpleUploadedFile("arabic.srt", ARABIC_SRT, content_type="application/x-subrip")

    response = owner_client.post(
        url, {"file": upload, "language": "ar", "title": "Arabic", "default": "true"},
        format="multipart", headers=ADMIN,
    )  # fmt: skip

    assert response.status_code == 201, response.json()
    body = response.json()
    assert (body["origin"], body["language"], body["encoding"], body["status"]) == (
        "upload",
        "ara",
        "cp1256",
        "pending",
    )
    track = SubtitleTrack.objects.get(pk=body["id"])
    assert bytes(track.upload or b"") == ARABIC_SRT
    assert track.default
    assert not SubtitleTrack.objects.filter(media_file=file, default=True).exclude(pk=track.pk)
    job = TranscodeJob.objects.get(media_file=file, profile="subtitles")
    assert job.status == JobStatus.QUEUED

    for name, data, field in (
        ("notes.txt", b"hello", "file"),
        ("empty.srt", b"   ", "file"),
        ("ok.srt", ARABIC_SRT, "language"),
    ):
        bad = owner_client.post(
            url,
            {"file": SimpleUploadedFile(name, data),
             "language": "zz-top" if field == "language" else "ar"},
            format="multipart", headers=ADMIN,
        )  # fmt: skip
        assert bad.status_code == 400, name
        assert field in bad.json()["field_errors"], name

    embedded = SubtitleTrack.objects.get(media_file=file, stream_index=3)
    assert owner_client.delete(f"{url}/{embedded.pk}", headers=ADMIN).status_code == 409
    assert owner_client.delete(f"{url}/{track.pk}", headers=ADMIN).status_code == 204
    assert not SubtitleTrack.objects.filter(pk=track.pk).exists()
    assert {"track.upload", "track.delete"} <= set(
        AuditLog.objects.values_list("action", flat=True)
    )


def test_a_title_with_several_files_needs_a_choice(
    owner_client: APIClient, library: Library, queued: list[Any]
) -> None:
    movie, _file = movie_with_media(library)
    second = media_file(library, movie=movie, is_primary=False)
    url = f"{TITLES}/{movie.pk}/tracks"

    def post(**extra: Any) -> Any:
        upload = SimpleUploadedFile("a.srt", ARABIC_SRT)
        data = {"file": upload, "language": "ar", **extra}
        return owner_client.post(url, data, format="multipart", headers=ADMIN)

    assert post().status_code == 400
    assert post(media_file=str(second.pk)).status_code == 201
    assert SubtitleTrack.objects.filter(media_file=second).count() == 1


# --- Images ----------------------------------------------------------------------------------


def png(width: int = 300, height: int = 450) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (200, 30, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_images_with_tmdb_alternatives(
    owner_client: APIClient, library: Library, offline_tmdb: TMDBClient
) -> None:
    movie = Movie.objects.create(title="Inception", tmdb_id=27205)
    stored = MediaImage.objects.create(movie=movie, kind=ImageKind.POSTER, is_primary=True,
                                       source_path="/stored.jpg", sizes={})  # fmt: skip

    response = owner_client.get(f"{TITLES}/{movie.pk}/images", headers=ADMIN)

    assert response.status_code == 200
    body = response.json()
    assert [i["id"] for i in body["images"]] == [str(stored.pk)]
    assert body["alternatives_error"] is None
    for item in body["alternatives"]:
        assert item["kind"] in {"poster", "backdrop", "logo"}
        assert item["path"].startswith("/")
        assert item["path"] != "/stored.jpg"
        assert item["preview_url"].startswith("https://image.tmdb.org/t/p/w185/")
    no_tmdb = Movie.objects.create(title="Home video")
    empty = owner_client.get(f"{TITLES}/{no_tmdb.pk}/images", headers=ADMIN).json()
    assert (empty["alternatives"], empty["alternatives_error"]) == ([], None)


def test_adding_choosing_and_deleting_images(
    owner_client: APIClient, library: Library, queued: list[Any], data_root: Path
) -> None:
    movie = Movie.objects.create(title="Inception")
    url = f"{TITLES}/{movie.pk}/images"

    picked = owner_client.post(url, {"kind": "poster", "tmdb_path": "/abc.jpg"}, format="json",
                               headers=ADMIN)  # fmt: skip
    upload = SimpleUploadedFile("poster.png", png(), content_type="image/png")
    uploaded = owner_client.post(url, {"kind": "poster", "file": upload, "primary": "true"},
                                 format="multipart", headers=ADMIN)  # fmt: skip

    assert (picked.status_code, uploaded.status_code) == (202, 202)
    requests = [args[0] for name, args in queued if name == "store_title_image"]
    assert requests[0]["tmdb_path"] == "/abc.jpg"
    assert requests[1]["upload_b64"]
    both = owner_client.post(url, {"kind": "poster", "tmdb_path": "/x.jpg",
                                   "file": SimpleUploadedFile("p.png", png())},
                             format="multipart", headers=ADMIN)  # fmt: skip
    assert both.status_code == 400
    assert owner_client.post(url, {"kind": "poster", "tmdb_path": "x"}, format="json",
                             headers=ADMIN).status_code == 400  # fmt: skip

    # The worker stores the upload (WebP/AVIF at every size) as the primary poster.
    assert tasks.store_title_image(requests[1]) is True
    first = MediaImage.objects.get(movie=movie)
    assert (first.source, first.is_primary, first.width) == ("upload", True, 300)
    assert "w185" in first.sizes
    assert artwork.store("movie", str(uuid4()), kind="poster", tmdb_path=None, upload=b"",
                         primary=True) is False  # fmt: skip
    assert artwork.store("movie", str(movie.pk), kind="poster", tmdb_path=None, upload=b"junk",
                         primary=True) is False  # fmt: skip
    second = MediaImage.objects.create(movie=movie, kind="poster", sizes={}, source_path="/b.jpg")

    response = owner_client.post(f"{url}/{second.pk}/primary", headers=ADMIN)
    assert response.status_code == 200
    first.refresh_from_db()
    second.refresh_from_db()
    assert (first.is_primary, second.is_primary) == (False, True)

    assert owner_client.delete(f"{url}/{second.pk}", headers=ADMIN).status_code == 204
    first.refresh_from_db()
    assert first.is_primary  # the newest remaining poster takes over
    assert owner_client.delete(f"{url}/{second.pk}", headers=ADMIN).status_code == 404
    assert owner_client.post(f"{url}/{uuid4()}/primary", headers=ADMIN).status_code == 404


def test_storing_a_tmdb_alternative(
    library: Library, offline_tmdb: TMDBClient, data_root: Path
) -> None:
    from apps.metadata import services as metadata  # noqa: PLC0415

    movie = metadata.upsert_movie(603, offline_tmdb)
    details = offline_tmdb.movie_details(603)
    posters = [p["file_path"] for p in details["images"]["posters"]]
    stored_paths = set(MediaImage.objects.filter(movie=movie).values_list("source_path", flat=True))
    candidates = [p for p in posters if p not in stored_paths]
    if not candidates:
        pytest.skip("the fixture has no alternative poster")
    assert artwork.store("movie", str(movie.pk), kind="poster", tmdb_path=candidates[0],
                         upload=None, primary=True) is True  # fmt: skip
    chosen = MediaImage.objects.get(movie=movie, source_path=candidates[0])
    assert chosen.is_primary
    assert MediaImage.objects.filter(movie=movie, kind="poster", is_primary=True).count() == 1


# --- Rematch ---------------------------------------------------------------------------------


def test_rematch_to_another_tmdb_title(
    owner_client: APIClient, library: Library, offline_tmdb: TMDBClient, queued: list[Any]
) -> None:
    wrong = Movie.objects.create(title="The Matrix Reloaded", tmdb_id=604)
    file = media_file(library, movie=wrong)

    response = owner_client.post(f"{TITLES}/{wrong.pk}/rematch", {"tmdb_id": 603}, format="json",
                                 headers=ADMIN)  # fmt: skip

    assert response.status_code == 200, response.json()
    body = response.json()
    file.refresh_from_db()
    assert file.movie is not None
    assert file.movie.tmdb_id == 603
    assert body["title_ids"] == [str(file.movie.pk)]
    assert (body["files"], body["automatic"]) == (1, False)
    assert AuditLog.objects.filter(action="title.rematch").exists()


def test_rematch_again_automatically(
    owner_client: APIClient, library: Library, monkeypatch: pytest.MonkeyPatch, queued: list[Any]
) -> None:
    from apps.metadata import tasks as metadata_tasks  # noqa: PLC0415

    matched: list[str] = []
    monkeypatch.setattr(metadata_tasks.match_media_file, "delay", matched.append)
    movie = Movie.objects.create(title="Film")
    file = media_file(library, movie=movie)

    response = owner_client.post(f"{TITLES}/{movie.pk}/rematch", {}, format="json", headers=ADMIN)

    assert response.status_code == 200
    assert response.json() == {"files": 1, "automatic": True, "title_ids": []}
    file.refresh_from_db()
    assert file.state == FileState.MATCHING
    assert matched == [str(file.pk)]
    empty = Movie.objects.create(title="Nothing")
    assert owner_client.post(f"{TITLES}/{empty.pk}/rematch", {}, format="json",
                             headers=ADMIN).status_code == 409  # fmt: skip


def test_rematch_to_a_series_needs_episode_numbers(
    owner_client: APIClient, library: Library, offline_tmdb: TMDBClient, queued: list[Any]
) -> None:
    movie = Movie.objects.create(title="Pilot")
    media_file(library, movie=movie, parse_result={"kind": "movie", "title": "Pilot"})
    response = owner_client.post(
        f"{TITLES}/{movie.pk}/rematch",
        {"tmdb_id": 1396, "kind": "tv"},
        format="json",
        headers=ADMIN,
    )
    assert response.status_code == 400
    assert "tmdb_id" in response.json()["field_errors"]


# --- Cleanup and RBAC ------------------------------------------------------------------------


def test_cleanup_report_and_run(owner_client: APIClient, queued: list[Any]) -> None:
    cache_redis().delete(tasks.CLEANUP_REPORT_KEY)
    url = "/api/v1/admin/renditions/cleanup"
    assert owner_client.get(url, headers=ADMIN).status_code == 404

    response = owner_client.post(url, {}, format="json", headers=ADMIN)
    assert response.status_code == 202
    assert queued == [("cleanup", ({"dry_run": True},))]
    owner_client.post(url, {"dry_run": False}, format="json", headers=ADMIN)
    assert queued[-1] == ("cleanup", ({"dry_run": False},))
    assert AuditLog.objects.filter(action="renditions.cleanup").count() == 2

    cache_redis().set(tasks.CLEANUP_REPORT_KEY, json.dumps({
        "dry_run": True, "finished_at": "2026-10-04T10:00:00+00:00", "bytes": 10, "entries": 1,
        "removals": [{"path": "abc/hls9", "reason": "superseded", "bytes": 10}],
    }))  # fmt: skip
    report = owner_client.get(url, headers=ADMIN).json()
    assert (report["bytes"], report["removals"][0]["reason"]) == (10, "superseded")


def test_permissions(make_admin: AdminFactory, library: Library, queued: list[Any]) -> None:
    movie, _file = movie_with_media(library)
    reader = APIClient()
    reader.force_authenticate(make_admin(permissions=["library.view"]))
    assert reader.get(f"{TITLES}/{movie.pk}/renditions", headers=ADMIN).status_code == 200
    assert reader.get("/api/v1/admin/renditions/cleanup", headers=ADMIN).status_code in (200, 404)
    for path in (
        f"{TITLES}/{movie.pk}/reprocess",
        f"{TITLES}/{movie.pk}/rematch",
        f"{TITLES}/{movie.pk}/images",
        "/api/v1/admin/renditions/cleanup",
    ):
        response = reader.post(path, {}, format="json", headers=ADMIN)
        assert response.status_code == 403, path
    reviewer = APIClient()
    reviewer.force_authenticate(make_admin(permissions=["library.review"]))
    with_review = reviewer.post(f"{TITLES}/{movie.pk}/rematch", {}, format="json", headers=ADMIN)
    assert with_review.status_code != 403
    nobody = APIClient()
    nobody.force_authenticate(make_admin(permissions=[]))
    assert nobody.get(f"{TITLES}/{movie.pk}/renditions", headers=ADMIN).status_code == 403
    anonymous = APIClient().get(f"{TITLES}/{movie.pk}/renditions", headers=ADMIN)
    assert anonymous.status_code in (401, 403)
