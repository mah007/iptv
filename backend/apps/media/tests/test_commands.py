"""The media management commands: run_transcoder, bench_transcode, media_ready and
plan_media."""

import io
import shutil
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.catalog.models import Movie, Series, TitleStatus
from apps.core.services import reset_settings_cache
from apps.library.models import Library
from apps.media.tests.test_pipeline import direct_play_mp4, hevc_mkv

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed"),
]


@pytest.fixture(autouse=True)
def _isolated_settings_cache() -> Iterator[None]:
    reset_settings_cache()
    yield
    reset_settings_cache()


def test_run_transcoder_detects_the_encoders() -> None:
    out = io.StringIO()
    call_command("run_transcoder", "--detect-only", stdout=out)
    assert '"transcode.cpu"' in out.getvalue()
    assert "cpu/h264: ok" in out.getvalue()


def test_bench_transcode_prints_a_table(media_root: Path) -> None:
    hevc_mkv(media_root / "movies" / "Bench.mkv")
    out = io.StringIO()
    call_command("bench_transcode", "movies/Bench.mkv", "--backends", "cpu,nvenc", stdout=out)
    rows = out.getvalue().splitlines()
    assert rows[0].startswith("| File |")
    assert "| cpu | libx264 |" in rows[2]
    assert rows[2].endswith("| ok |")
    assert rows[3].endswith("| unavailable |")  # no NVIDIA GPU in the test container
    with pytest.raises(CommandError):
        call_command("bench_transcode", "../etc/passwd", stdout=out)


def test_bench_transcode_times_the_ladder(media_root: Path) -> None:
    hevc_mkv(media_root / "movies" / "Ladder.mkv")
    out = io.StringIO()
    call_command(
        "bench_transcode", "movies/Ladder.mkv", "--backends", "cpu", "--profile", "hls", stdout=out
    )
    rows = out.getvalue().splitlines()
    assert "| 1 rungs (240p) | ok |" in rows[2]
    with pytest.raises(CommandError, match="not a UHD source"):
        call_command(
            "bench_transcode", "movies/Ladder.mkv", "--backends", "cpu", "--profile", "uhd",
            stdout=out,
        )  # fmt: skip


def test_media_ready_adds_the_sample_libraries_and_waits(
    media_root: Path, monkeypatch: pytest.MonkeyPatch, make_library: Callable[..., Library]
) -> None:
    direct_play_mp4(media_root / "movies" / "Clip.mp4")
    with pytest.raises(CommandError):
        call_command("media_ready", "--timeout", "0", stdout=io.StringIO())
    assert set(Library.objects.values_list("name", flat=True)) == {"Movies", "Series"}

    Movie.objects.create(title="Ready", status=TitleStatus.READY)
    Series.objects.create(title="Ready", status=TitleStatus.READY)
    out = io.StringIO()
    call_command("media_ready", stdout=out)
    assert "A ready movie and a ready series exist." in out.getvalue()


def test_plan_media_queues_every_matched_file(
    make_library: Callable[..., Library], monkeypatch: pytest.MonkeyPatch
) -> None:
    from apps.catalog.models import FileState, MediaFile  # noqa: PLC0415
    from apps.media import tasks  # noqa: PLC0415

    library = make_library()
    planned: list[str] = []
    monkeypatch.setattr(tasks.prepare_media_file, "delay", planned.append)
    matched = [
        MediaFile.objects.create(library=library, storage_key=f"{n}.mkv", state=FileState.MATCHED)
        for n in range(3)
    ]
    MediaFile.objects.create(library=library, storage_key="new.mkv")  # not matched yet
    planned.clear()  # the matched saves queued their own plans

    out = io.StringIO()
    call_command("plan_media", stdout=out)
    assert planned == [str(f.pk) for f in matched]
    assert "Queued planning of 3 files." in out.getvalue()
    planned.clear()
    call_command("plan_media", "--limit", "1", stdout=out)
    assert planned == [str(matched[0].pk)]
