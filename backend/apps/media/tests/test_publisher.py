"""Publishing renditions to the S3 origin (ADR-0022): the bucket layout, generations,
in-place playlists, housekeeping, the Integrations Test, and the playable lookup."""

import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from django.db import transaction

from apps.catalog.models import FileState, MediaFile
from apps.core import integration
from apps.core.services import reset_settings_cache, set_setting
from apps.library.models import Library
from apps.media import checks, layout, publisher
from apps.media.models import AssetPublication, PublicationStatus, Rendition, RenditionStatus
from apps.media.s3 import S3Client, S3Config
from apps.media.tests.fake_s3 import FakeBucket

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _isolated_settings_cache() -> Iterator[None]:
    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture
def library(make_library: Callable[..., Library], data_root: Path) -> Library:
    return make_library()


@pytest.fixture
def configured() -> None:
    for key, value in {
        "storage.s3_enabled": True,
        "storage.s3_endpoint": "https://s3.example.test",
        "storage.s3_bucket": "media",
        "storage.s3_prefix": "renditions",
        "storage.s3_region": "auto",
        "storage.s3_access_key": "PUBLISHER",
        "storage.s3_secret_key": "secret-" + "value",
    }.items():
        set_setting(key, value, actor=None)


def store(bucket: FakeBucket) -> S3Client:
    return S3Client(
        S3Config("https://s3.example.test", "media", "PUBLISHER", "secret-" + "value",
                 region="auto"),
        transport=bucket.transport(),
    )  # fmt: skip


def write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def asset(library: Library, *, source: bool = False) -> tuple[MediaFile, Path]:
    """A file with a compat MP4, a two-rung ladder, a 720p presentation, subtitles and
    thumbnails, laid out as the pipeline leaves them (links included)."""
    original = Path(library.path) / "film.mp4"
    write(original, b"original" * 10)
    file = MediaFile.objects.create(
        library=library, storage_key="film.mp4", state=FileState.MATCHED
    )
    key = file.pk.hex
    root = layout.asset_dir(key)
    write(root / "compat.mp4", b"m" * 300)
    master = (
        "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nv0/index.m3u8\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=2\nv1/index.m3u8\n"
    )
    write(root / "hls" / "master.m3u8", master.encode())
    for rung in ("v0", "v1"):
        write(root / "hls" / rung / "index.m3u8", b"#EXTM3U\nseg_00001.m4s\n")
        write(root / "hls" / rung / "init.mp4", rung.encode() * 5)
        write(root / "hls" / rung / "seg_00001.m4s", rung.encode() * 50)
    write(root / "hls" / "a0" / "index.m3u8", b"#EXTM3U\n")
    write(
        root / "hls720" / "master.m3u8", b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=2\nv1/index.m3u8\n"
    )
    layout.link("../hls/v1", root / "hls720" / "v1")
    layout.link("../hls/a0", root / "hls720" / "a0")
    write(root / "subs" / "3.eng.vtt", b"WEBVTT\n")
    write(root / "thumbs" / "thumbs.vtt", b"WEBVTT\n")
    write(root / "thumbs" / "sprite_0.jpg", b"jpeg")
    for scope in ("hls", "hls720", "compat"):
        layout.link("../subs", root / scope / "subs")
        layout.link("../thumbs", root / scope / "thumbs")
    write(root / ".tmp-job" / "junk", b"x")
    rows = [
        ("compat_mp4", "compat", 1080, {}),
        ("hls_master", "hls", 1080, {}),
        ("hls_master", "hls720", 720, {}),
        ("hls_variant", "v0", 1080, {"box_height": 1080}),
        ("hls_variant", "v1", 720, {"box_height": 720}),
    ]
    if source:
        os.symlink(original, root / "source.mp4")
        rows.append(("source", "source", 1080, {}))
    for kind, name, height, details in rows:
        Rendition.objects.create(
            media_file=file, kind=kind, name=name, height=height, storage_key=key,
            container="m3u8" if kind == "hls_master" else "mp4",
            status=RenditionStatus.READY, details=details,
        )  # fmt: skip
    return file, root


def test_the_plan_names_rungs_by_height_and_skips_links(library: Library, configured: None) -> None:
    file, _root = asset(library, source=True)
    plan = publisher.plan(file)
    paths = [item.path for item in plan.objects]
    assert "hls/v0-h1080/seg_00001.m4s" in paths
    assert "hls/v1-h720/init.mp4" in paths
    assert "hls/a0/index.m3u8" in paths
    assert not any(
        path.startswith(("hls/v0/", "hls720/v1", "hls720/a0", "compat/")) for path in paths
    )
    assert not any(".tmp" in path for path in paths)
    assert {"subs/3.eng.vtt", "thumbs/thumbs.vtt", "thumbs/sprite_0.jpg"} <= set(paths)
    masters = {
        item.path: item.text or b"" for item in plan.objects if item.path.endswith("master.m3u8")
    }
    assert masters["hls/master.m3u8"] is not None
    assert b"v0-h1080/index.m3u8\n" in masters["hls/master.m3u8"]
    assert b"v1-h720/index.m3u8\n" in masters["hls720/master.m3u8"]
    assert b"\nv1/index.m3u8" not in masters["hls720/master.m3u8"]
    source = next(item for item in plan.objects if item.path == "source.mp4")
    assert source.file is not None
    assert source.file.read_bytes() == b"original" * 10
    assert plan.renditions == ["compat", "hls", "hls720", "source"]
    set_setting("storage.publish_sources", False, actor=None)
    assert "source" not in publisher.plan(file).renditions


def test_publishing_uploads_immutable_objects_then_playlists(
    library: Library, configured: None
) -> None:
    file, _root = asset(library)
    bucket = FakeBucket()
    with store(bucket) as s3:
        assert publisher.publish(file, s3) == "published"
    prefix = f"renditions/{file.pk.hex}-g1/"
    assert f"{prefix}hls/v1-h720/seg_00001.m4s" in bucket.objects
    assert bucket.headers[f"{prefix}hls/master.m3u8"]["cache-control"] == publisher.MUTABLE_CACHE
    assert bucket.headers[f"{prefix}compat.mp4"]["cache-control"] == publisher.IMMUTABLE_CACHE
    assert bucket.headers[f"{prefix}compat.mp4"]["content-type"] == "video/mp4"
    puts = [key for method, key in bucket.log if method == "PUT"]
    last_immutable = max(i for i, key in enumerate(puts) if not publisher.MUTABLE.search(key))
    first_mutable = min(i for i, key in enumerate(puts) if publisher.MUTABLE.search(key))
    assert last_immutable < first_mutable
    publication = AssetPublication.objects.get(media_file=file)
    assert (publication.generation, publication.status) == (1, PublicationStatus.PUBLISHED)
    assert publication.renditions == ["compat", "hls", "hls720"]
    with store(bucket) as s3:
        assert publisher.publish(file, s3) == "unchanged"


def test_a_new_playlist_is_overwritten_in_place(library: Library, configured: None) -> None:
    file, root = asset(library)
    bucket = FakeBucket()
    with store(bucket) as s3:
        publisher.publish(file, s3)
        write(root / "subs" / "4.ara.vtt", b"WEBVTT\n")
        bucket.log.clear()
        assert publisher.publish(file, s3) == "published"
    publication = AssetPublication.objects.get(media_file=file)
    assert publication.generation == 1
    assert [key for m, key in bucket.log if m == "PUT"] == [
        f"renditions/{file.pk.hex}-g1/subs/4.ara.vtt"
    ]


def test_a_changed_segment_publishes_a_new_generation(library: Library, configured: None) -> None:
    file, root = asset(library)
    bucket = FakeBucket()
    with store(bucket) as s3:
        publisher.publish(file, s3)
        segment = root / "hls" / "v0" / "seg_00001.m4s"
        segment.write_bytes(b"re-encoded" * 50)
        os.utime(segment, (time.time() + 5, time.time() + 5))
        bucket.log.clear()
        assert publisher.publish(file, s3) == "published"
    old, new = f"renditions/{file.pk.hex}-g1/", f"renditions/{file.pk.hex}-g2/"
    assert bucket.objects[f"{new}hls/v0-h1080/seg_00001.m4s"] == b"re-encoded" * 50
    # Unchanged immutable objects are copied inside the bucket, not uploaded again.
    copies = [key for m, key in bucket.log if m == "PUT" and key == f"{new}compat.mp4"]
    assert copies
    assert bucket.objects[f"{new}compat.mp4"] == b"m" * 300
    # The old generation stays for players still using it.
    assert f"{old}hls/v0-h1080/seg_00001.m4s" in bucket.objects
    publication = AssetPublication.objects.get(media_file=file)
    assert publication.generation == 2
    assert [item["generation"] for item in publication.retired] == [1]


def test_reconcile_deletes_retired_generations_after_their_grace(
    library: Library, configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    file, root = asset(library)
    bucket = FakeBucket()
    monkeypatch.setattr(publisher, "client", lambda cfg=None: store(bucket))
    queued: list[str] = []
    monkeypatch.setattr("apps.media.publish_tasks.publish_asset.delay", queued.append)
    with store(bucket) as s3:
        publisher.publish(file, s3)
        (root / "compat.mp4").write_bytes(b"new" * 100)
        publisher.publish(file, s3)
    now = time.time()
    counts = publisher.reconcile(now=now)
    assert counts["retired"] == 0
    assert any(key.startswith(f"renditions/{file.pk.hex}-g1/") for key in bucket.objects)
    counts = publisher.reconcile(now=now + 9 * 3600)
    assert counts["retired"] == 1
    assert not any(key.startswith(f"renditions/{file.pk.hex}-g1/") for key in bucket.objects)
    assert bucket.objects["renditions/edgeprobe/probe.txt"] == publisher.PROBE_BODY


def test_reconcile_queues_waiting_files_and_removes_gone_ones(
    library: Library, configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from django.utils import timezone  # noqa: PLC0415

    waiting, _ = asset(library)
    bucket = FakeBucket()
    monkeypatch.setattr(publisher, "client", lambda cfg=None: store(bucket))
    queued: list[str] = []
    monkeypatch.setattr("apps.media.publish_tasks.publish_asset.delay", queued.append)
    counts = publisher.reconcile()
    assert queued == [str(waiting.pk)]
    assert counts["queued"] == 1
    with store(bucket) as s3:
        publisher.publish(waiting, s3)
    MediaFile.objects.filter(pk=waiting.pk).update(removed_at=timezone.now())
    counts = publisher.reconcile()
    assert counts["removed"] == 1
    assert not any(key.startswith(f"renditions/{waiting.pk.hex}") for key in bucket.objects)
    assert not AssetPublication.objects.filter(media_file=waiting).exists()


def test_failures_are_recorded_without_the_bucket(library: Library, configured: None) -> None:
    file, _root = asset(library)
    bucket = FakeBucket(fail_puts=100)
    with store(bucket) as s3:
        assert publisher.publish(file, s3) == "failed"
    publication = AssetPublication.objects.get(media_file=file)
    assert publication.status == PublicationStatus.FAILED
    assert publication.attempts == 1
    assert "media" not in publication.error.split()  # no bucket name


def test_outputs_changing_queue_a_publication(
    library: Library, configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    file, _root = asset(library)
    queued: list[str] = []
    monkeypatch.setattr("apps.media.publish_tasks.publish_asset.delay", queued.append)
    with transaction.atomic():
        publisher.mark_stale(file)
    assert AssetPublication.objects.get(media_file=file).status == PublicationStatus.PENDING


def test_nothing_is_queued_while_publishing_is_off(library: Library) -> None:
    file, _root = asset(library)
    publisher.mark_stale(file)
    assert not AssetPublication.objects.filter(media_file=file).exists()


def test_the_playable_lookup_offers_published_renditions(
    library: Library, configured: None
) -> None:
    from apps.catalog.models import Movie, TitleStatus  # noqa: PLC0415
    from apps.catalog.playable import playable_title  # noqa: PLC0415
    from apps.playback.models import TitleKind  # noqa: PLC0415

    file, _root = asset(library)
    movie = Movie.objects.create(title="Film", status=TitleStatus.READY)
    MediaFile.objects.filter(pk=file.pk).update(movie=movie)
    title = playable_title(TitleKind.MOVIE, movie.xc_id)
    assert title is not None
    assert all(r.published_key == "" for r in title.renditions)
    with store(FakeBucket()) as s3:
        publisher.publish(file, s3)
    title = playable_title(TitleKind.MOVIE, movie.xc_id)
    assert title is not None
    assert {r.published_key for r in title.renditions} == {f"{file.pk.hex}-g1"}


# --- The Integrations page's Test -----------------------------------------------------------


@pytest.fixture
def bucket(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeBucket]:
    fake = FakeBucket()
    with integration.use_transport(fake.transport()):
        yield fake


def test_the_origin_test_needs_settings() -> None:
    assert checks.check_origin().code == "not_configured"


def test_the_origin_test_checks_read_only_edge_keys(configured: None, bucket: FakeBucket) -> None:
    result = checks.check_origin()
    assert result.ok
    assert result.details == {"edges": False}
    set_setting("storage.s3_edge_access_key", "EDGEREAD", actor=None)
    set_setting("storage.s3_edge_secret_key", "edge-" + "secret", actor=None)
    result = checks.check_origin()
    assert result.ok
    assert result.details == {"edges": True}
    assert not any("integration-test" in key for key in bucket.objects)


def test_the_origin_test_refuses_writable_edge_keys(configured: None, bucket: FakeBucket) -> None:
    bucket.keys["EDGEWRITE"] = True
    set_setting("storage.s3_edge_access_key", "EDGEWRITE", actor=None)
    set_setting("storage.s3_edge_secret_key", "edge-" + "secret", actor=None)
    result = checks.check_origin()
    assert (result.ok, result.code) == (False, "rejected")
    assert "read-only" in result.message
    assert not any("integration-test" in key for key in bucket.objects)


def test_the_origin_test_explains_a_wrong_key(configured: None, bucket: FakeBucket) -> None:
    set_setting("storage.s3_access_key", "WRONG", actor=None)
    result = checks.check_origin()
    assert (result.ok, result.code) == (False, "forbidden")
    assert "media" not in result.message.split()
