"""Per-file ingest with real ffprobe (SPEC §7.2 steps 1-3; apps.library.pipeline).

FFmpeg is in the dev and media images; the tests skip on a host without it."""

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

import pytest

from apps.catalog.models import Episode, FileState, MediaFile, Movie, Season, Series, TitleStatus
from apps.library import pipeline
from apps.library.models import Library

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg and ffprobe"),
]


def make_video(path: Path, *, mp4: bool) -> Path:
    """One second of H.264 + AAC: a faststart MP4 (direct play) or an MKV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    muxer = ["-movflags", "+faststart", "-f", "mp4"] if mp4 else ["-f", "matroska"]
    subprocess.run(  # noqa: S603
        [  # noqa: S607
            "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=1",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1",
            "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "64k", "-shortest", *muxer, str(path),
        ],
        check=True,
        timeout=60,
    )  # fmt: skip
    return path


def add_file(library: Library, key: str) -> MediaFile:
    return MediaFile.objects.create(library=library, storage_key=key, size=1)


def test_a_faststart_mp4_plays_directly(make_library: Callable[..., Library]) -> None:
    library = make_library()
    make_video(Path(library.path) / "The Matrix.mp4", mp4=True)
    file = add_file(library, "The Matrix.mp4")
    matched: list[UUID] = []
    pipeline.process_file(file.pk, match=matched.append)
    file.refresh_from_db()
    assert matched == [file.pk]
    assert (file.state, file.direct_play, file.container) == (FileState.MATCHING, True, "mp4")
    assert (file.video_codec, file.width, file.height, file.hdr) == ("h264", 320, 240, "sdr")
    assert 900 <= (file.duration_ms or 0) <= 1100
    assert file.parse_result["title"] == "The Matrix"
    assert [track["codec"] for track in file.probe_summary["audio"]] == ["aac"]
    assert "The Matrix.mp4" not in str(file.probe)  # no file names in the stored probe


def test_an_mkv_needs_a_rendition(make_library: Callable[..., Library]) -> None:
    library = make_library()
    make_video(Path(library.path) / "Film (2001)" / "Film.2001.mkv", mp4=False)
    file = add_file(library, "Film (2001)/Film.2001.mkv")
    pipeline.process_file(file.pk, match=lambda pk: None)
    file.refresh_from_db()
    assert (file.container, file.direct_play) == ("mkv", False)


def test_unreadable_media_is_an_error_without_paths(make_library: Callable[..., Library]) -> None:
    library = make_library()
    (Path(library.path) / "Broken.2001.mkv").write_bytes(b"not a video at all")
    file = add_file(library, "Broken.2001.mkv")
    matched: list[UUID] = []
    pipeline.process_file(file.pk, match=matched.append)
    file.refresh_from_db()
    assert file.state == FileState.ERROR
    assert file.error.startswith("ffprobe failed")
    assert library.path not in file.error
    assert matched == []


def test_linked_files_are_reprobed_without_rematching(
    make_library: Callable[..., Library],
) -> None:
    library = make_library("Series", "series", "series")
    make_video(Path(library.path) / "Show" / "Show.S01E01.mp4", mp4=True)
    file = add_file(library, "Show/Show.S01E01.mp4")
    series = Series.objects.create(title="Show")
    episode = Episode.objects.create(
        season=Season.objects.create(series=series, number=1), number=1
    )
    file.episodes.add(episode)
    matched: list[UUID] = []
    pipeline.process_file(file.pk, match=matched.append)
    file.refresh_from_db()
    series.refresh_from_db()
    assert (file.state, matched) == (FileState.MATCHED, [])
    assert series.status == TitleStatus.READY


def test_removed_or_missing_files_are_skipped(make_library: Callable[..., Library]) -> None:
    library = make_library()
    file = add_file(library, "Gone.mkv")
    MediaFile.objects.filter(pk=file.pk).update(removed_at="2026-01-01T00:00:00Z")
    assert pipeline.process_file(file.pk) is None
    assert pipeline.process_file("00000000-0000-0000-0000-000000000000") is None
    assert not Movie.objects.exists()
