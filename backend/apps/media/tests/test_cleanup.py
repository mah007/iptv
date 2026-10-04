"""Retention and cleanup of renditions (ADR-0014), and the asset layout helpers."""

import json
import os
import time
from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from django.utils import timezone

from apps.catalog.models import FileState, MediaFile
from apps.core.services import reset_settings_cache, set_setting
from apps.core.stores import cache_redis
from apps.library.models import Library
from apps.media import cleanup, layout, tasks
from apps.media.models import (
    JobStatus,
    Rendition,
    RenditionKind,
    RenditionStatus,
    SubtitleStatus,
    SubtitleTrack,
    TranscodeJob,
)

pytestmark = pytest.mark.django_db

OLD = time.time() - 2 * 3600


@pytest.fixture(autouse=True)
def _isolated_settings_cache() -> Iterator[None]:
    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture
def library(make_library: Callable[..., Library], data_root: Path) -> Library:
    return make_library()


def age(path: Path, when: float = OLD) -> None:
    """Make `path` (and everything under it) look `when` old."""
    for item in [path, *path.rglob("*")] if path.is_dir() else [path]:
        os.utime(item, (when, when), follow_symlinks=False)


def write(path: Path, data: bytes = b"x" * 10) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def live_asset(library: Library) -> tuple[MediaFile, Path]:
    file = MediaFile.objects.create(
        library=library, storage_key=f"{uuid4().hex}.mkv", state=FileState.MATCHED
    )
    key = file.pk.hex
    root = layout.asset_dir(key)
    write(root / "compat.mp4", b"m" * 100)
    layout.link("../subs", root / "compat" / "subs")
    write(root / "hls" / "master.m3u8")
    write(root / "hls" / "v0" / "seg_00000.m4s", b"v" * 50)
    write(root / "hls" / "a0" / "seg_00000.m4s", b"a" * 20)
    write(root / "hls" / "v7" / "seg_00000.m4s", b"o" * 30)  # a previous ladder's rung
    write(root / "subs" / "3.eng.vtt")
    write(root / "subs" / "9.fre.vtt")  # a track that is gone
    write(root / "uhd.mkv")  # a UHD version the source no longer gets
    write(root / ".tmp-" / "junk")
    Rendition.objects.create(media_file=file, kind="compat_mp4", name="compat", storage_key=key,
                             status=RenditionStatus.READY)  # fmt: skip
    Rendition.objects.create(media_file=file, kind="hls_master", name="hls", storage_key=key,
                             status=RenditionStatus.READY,
                             details={"audio": [{"dir": "a0"}]})  # fmt: skip
    Rendition.objects.create(media_file=file, kind="hls_variant", name="v0", storage_key=key,
                             status=RenditionStatus.READY)  # fmt: skip
    SubtitleTrack.objects.create(media_file=file, stream_index=3, format="srt", language="eng",
                                 storage_key="3.eng", status=SubtitleStatus.READY)  # fmt: skip
    return file, root


def test_dry_run_reports_and_a_real_run_deletes(library: Library) -> None:
    file, root = live_asset(library)
    running = TranscodeJob.objects.create(media_file=file, profile="hls", status=JobStatus.RUNNING)
    write(root / f".tmp-{running.pk.hex}" / "out.m4s")  # a running job's work: kept
    orphan = layout.asset_dir(uuid4().hex)
    write(orphan / "compat.mp4", b"z" * 40)
    age(root)
    age(orphan)

    report = cleanup.cleanup(dry_run=True)

    found = {
        (r.path.split("/", 1)[1] if "/" in r.path else "<asset>", r.reason) for r in report.removals
    }
    assert found == {
        ("<asset>", "orphaned"),
        ("hls/v7", "superseded"),
        ("subs/9.fre.vtt", "superseded"),
        ("uhd.mkv", "superseded"),
        (".tmp-", "leftover"),
    }
    assert report.bytes == 40 + 30 + 10 + 10 + 10
    assert (root / "hls" / "v7").exists()  # a dry run deletes nothing
    assert orphan.exists()

    real = cleanup.cleanup(dry_run=False)

    assert real.bytes == report.bytes
    assert not orphan.exists()
    assert not (root / "hls" / "v7").exists()
    assert not (root / "uhd.mkv").exists()
    assert not (root / "subs" / "9.fre.vtt").exists()
    for kept in ("compat.mp4", "hls/v0", "hls/a0", "hls/master.m3u8", "subs/3.eng.vtt",
                 "compat/subs", f".tmp-{running.pk.hex}"):  # fmt: skip
        assert (root / kept).exists() or (root / kept).is_symlink(), kept
    assert cleanup.cleanup(dry_run=False).removals == []


def test_recent_entries_are_left_alone(library: Library) -> None:
    _file, root = live_asset(library)
    orphan = layout.asset_dir(uuid4().hex)
    write(orphan / "compat.mp4")
    report = cleanup.cleanup(dry_run=False)  # everything was just written
    assert report.removals == []
    assert orphan.exists()
    assert (root / "hls" / "v7").exists()


def test_removed_files_keep_their_renditions_for_the_retention(library: Library) -> None:
    file, root = live_asset(library)
    age(root)
    file.removed_at = timezone.now() - timedelta(days=2)
    file.save()
    set_setting("library.rendition_retention_days", value=3, actor=None)

    assert {r.reason for r in cleanup.cleanup(dry_run=True).removals} == {
        "superseded",
        "leftover",
    }
    set_setting("library.rendition_retention_days", value=1, actor=None)
    reset_settings_cache()
    report = cleanup.cleanup(dry_run=False)

    assert [(r.path, r.reason) for r in report.removals] == [(root.name, "removed_file")]
    assert not root.exists()
    assert not Rendition.objects.filter(media_file=file).exists()
    track = SubtitleTrack.objects.get(media_file=file)
    assert (track.storage_key, track.status) == ("", SubtitleStatus.PENDING)


def test_the_task_stores_its_report(library: Library) -> None:
    cache_redis().delete(tasks.CLEANUP_REPORT_KEY)
    orphan = layout.asset_dir(uuid4().hex)
    write(orphan / "compat.mp4", b"q" * 7)
    age(orphan)
    assert tasks.cleanup_renditions(dry_run=True) == 7
    stored = json.loads(cast(bytes, cache_redis().get(tasks.CLEANUP_REPORT_KEY) or b"{}"))
    assert (stored["dry_run"], stored["bytes"], stored["entries"]) == (True, 7, 1)
    assert stored["removals"][0]["reason"] == "orphaned"


def test_cleanup_without_a_renditions_folder(settings: object, tmp_path: Path) -> None:
    settings.DATA_ROOT = str(tmp_path / "nothing")  # type: ignore[attr-defined]
    assert cleanup.cleanup(dry_run=False).removals == []


def test_expected_entries_cover_every_kind() -> None:
    rows = [
        Rendition(kind=RenditionKind.SOURCE, container="mkv"),
        Rendition(kind=RenditionKind.UHD, container="mp4"),
        Rendition(kind=RenditionKind.HLS_MASTER, name="hls720"),
        Rendition(kind=RenditionKind.HLS_VARIANT, name="uhd"),
        Rendition(kind=RenditionKind.THUMBNAILS, name="thumbs"),
    ]
    assert cleanup._expected_entries(rows) == {
        "subs", "source.mkv", "source", "uhd.mp4", "uhd", "hls2160", "hls720", "thumbs",
    }  # fmt: skip


# --- Layout ----------------------------------------------------------------------------------


def test_links_dirs_and_sizes(tmp_path: Path) -> None:
    target = write(tmp_path / "hls" / "v0" / "seg.m4s", b"s" * 5)
    link = tmp_path / "hls720" / "v0"
    layout.link("../hls/v0", link)
    layout.link("../hls/v0", link)  # unchanged
    assert os.readlink(link) == "../hls/v0"
    assert (link / "seg.m4s").read_bytes() == target.read_bytes()
    (tmp_path / "hls720" / "a0").mkdir()
    layout.link("../hls/a0", tmp_path / "hls720" / "a0")  # a folder is replaced by a link
    assert (tmp_path / "hls720" / "a0").is_symlink()
    assert layout.tree_size(tmp_path / "hls720") == 0  # links are not counted
    assert layout.tree_size(tmp_path / "hls") == 5
    assert layout.tree_size(target) == 5

    new = write(tmp_path / ".new" / "v0" / "seg.m4s", b"n" * 3).parent.parent
    layout.replace_dir(new, tmp_path / "hls")
    assert (tmp_path / "hls" / "v0" / "seg.m4s").read_bytes() == b"nnn"
    assert not any(p.name.startswith(".old") for p in tmp_path.iterdir())
    write(tmp_path / "file")
    layout.replace_dir(write(tmp_path / ".x" / "f").parent, tmp_path / "file")
    assert (tmp_path / "file").is_dir()

    layout.write_text(tmp_path / "m" / "master.m3u8", "#EXTM3U\n")
    assert (tmp_path / "m" / "master.m3u8").read_text() == "#EXTM3U\n"
    layout.remove(tmp_path / "m")
    layout.remove(link)
    layout.remove(tmp_path / "missing")
    assert not (tmp_path / "m").exists()
    assert not link.is_symlink()


def test_names() -> None:
    assert layout.presentation() == "hls"
    assert layout.presentation(720) == "hls720"
    assert (layout.rung_dir(2), layout.audio_dir(0)) == ("v2", "a0")
    assert layout.subtitle_file("x1.ara", "vtt") == "subs/x1.ara.vtt"
    assert layout.is_hidden(".tmp-1")
    with pytest.raises(ValueError, match="asset keys"):
        layout.asset_dir("../etc")
