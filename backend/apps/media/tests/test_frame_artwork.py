"""A manual movie's poster and backdrop from a frame of its video (ADR-0026), on real
ffmpeg: the cropped poster, the whole frame as the backdrop, only for kinds the movie
lacks, on the worker's `images` queue; and a frame never replaces another image."""

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from apps.catalog.models import (
    FileState,
    ImageKind,
    ImageSource,
    MediaFile,
    MediaImage,
    MetadataSource,
    Movie,
)
from apps.library.models import Library
from apps.media import outputs, tasks
from config.celery import app

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
        reason="ffmpeg/ffprobe not installed",
    ),
]


@pytest.fixture
def library(make_library: Callable[..., Library], data_root: Path) -> Library:
    return make_library()


def home_video(library: Library, *, with_movie: bool = True) -> MediaFile:
    """Three seconds of 1280x720 H.264 with AAC, as a manual movie's file."""
    path = Path(library.path) / "Home Video (2026).mp4"
    subprocess.run(  # noqa: S603 - fixed argv
        [  # noqa: S607
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1280x720:rate=25:duration=3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(path),
        ],
        check=True,
        timeout=120,
    )
    movie = Movie.objects.create(title="Home video", metadata_source=MetadataSource.MANUAL)
    return MediaFile.objects.create(
        library=library,
        storage_key=path.name,
        movie=movie if with_movie else None,
        state=FileState.MATCHED,
        duration_ms=3000,
        direct_play=True,
    )


def test_the_frame_gives_a_portrait_poster_and_a_landscape_backdrop(
    library: Library, data_root: Path
) -> None:
    file = home_video(library)

    stored = tasks.frame_artwork(str(file.pk), ["poster", "backdrop"])

    assert stored == ["poster", "backdrop"]
    poster = MediaImage.objects.get(movie=file.movie, kind=ImageKind.POSTER)
    backdrop = MediaImage.objects.get(movie=file.movie, kind=ImageKind.BACKDROP)
    assert poster.height > poster.width  # the middle 2:3 of the frame
    assert (backdrop.width, backdrop.height) == (1280, 720)  # the whole frame
    for image in (poster, backdrop):
        assert (image.source, image.source_path, image.is_primary) == (
            ImageSource.UPLOAD,
            outputs.FRAME_SOURCE_PATH,
            True,
        )
        assert image.blurhash
    assert set(poster.sizes) == {"w185", "w500", "original"}
    assert set(backdrop.sizes) == {"w780", "w1280"}
    for formats in backdrop.sizes.values():
        assert (data_root / formats["webp"]).is_file()

    assert tasks.frame_artwork(str(file.pk), ["poster", "backdrop"]) == []  # already there


def test_only_missing_kinds_and_only_for_a_movie(library: Library) -> None:
    file = home_video(library)
    assert file.movie is not None
    MediaImage.objects.create(movie=file.movie, kind=ImageKind.POSTER, sizes={}, is_primary=True)
    assert outputs.frame_artwork(str(file.pk), ["poster", "backdrop", "logo"]) == ["backdrop"]
    assert MediaImage.objects.filter(movie=file.movie, kind=ImageKind.POSTER).count() == 1

    orphan = MediaFile.objects.create(library=library, storage_key="other.mp4", duration_ms=1)
    assert outputs.frame_artwork(str(orphan.pk), ["poster"]) == []
    assert outputs.frame_artwork("00000000-0000-7000-8000-000000000000", ["poster"]) == []


def test_an_unreadable_source_stores_nothing(library: Library) -> None:
    file = home_video(library)
    (Path(library.path) / file.storage_key).write_bytes(b"not a video")
    assert outputs.frame_artwork(str(file.pk), ["poster"]) == []
    assert not MediaImage.objects.filter(movie=file.movie).exists()


def test_a_frame_never_replaces_an_image_that_arrived_meanwhile(library: Library) -> None:
    file = home_video(library)
    uploaded = MediaImage.objects.create(
        movie=file.movie,
        kind=ImageKind.POSTER,
        sizes={},
        source=ImageSource.UPLOAD,
        source_path="upload:0123456789abcdef",
        is_primary=True,
    )

    class Stored:
        width, height, blurhash = 10, 15, ""

        def keys(self) -> dict[str, dict[str, str]]:
            return {}

    assert outputs._record_image(Stored(), kind=ImageKind.POSTER, movie=file.movie) is False
    uploaded.refresh_from_db()
    assert uploaded.is_primary
    assert MediaImage.objects.filter(movie=file.movie).count() == 1


def test_the_task_runs_on_the_images_queue() -> None:
    route = app.amqp.router.route({}, "apps.media.tasks.frame_artwork")
    assert route["queue"].name == "images"
