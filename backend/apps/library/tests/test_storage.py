"""Library folders on the media mount (apps.library.storage)."""

import os
from pathlib import Path

import pytest
from django.core.exceptions import ValidationError

from apps.library import storage


def test_paths_must_be_readable_folders_inside_the_root(media_root: Path) -> None:
    assert storage.validate_library_path(str(media_root / "movies")) == str(media_root / "movies")
    assert storage.validate_library_path("series") == str(media_root / "series")
    (media_root / "file.mkv").write_bytes(b"x")
    cases = {
        "/etc": "outside_root",
        str(media_root): "root",
        str(media_root / "missing"): "missing",
        str(media_root / "file.mkv"): "not_a_folder",
        "../": "outside_root",
    }
    for value, code in cases.items():
        with pytest.raises(ValidationError) as caught:
            storage.validate_library_path(value)
        assert caught.value.code == code, value


def test_symlinks_out_of_the_root_are_refused(media_root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (media_root / "link").symlink_to(outside)
    with pytest.raises(ValidationError):
        storage.validate_library_path("link")


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads everything")
def test_unreadable_folders_are_refused(media_root: Path) -> None:
    locked = media_root / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        with pytest.raises(ValidationError) as caught:
            storage.validate_library_path(str(locked))
        assert caught.value.code == "unreadable"
    finally:
        locked.chmod(0o755)


def test_overlaps() -> None:
    assert storage.overlaps("/media/a", "/media/a")
    assert storage.overlaps("/media/a", "/media/a/b")
    assert storage.overlaps("/media/a/b", "/media/a")
    assert not storage.overlaps("/media/a", "/media/ab")


def test_library_file_stays_inside() -> None:
    assert storage.library_file("/media/movies", "A/b.mkv") == Path("/media/movies/A/b.mkv")
    for bad in ("../x.mkv", "/etc/passwd", ""):
        with pytest.raises(ValueError, match="relative"):
            storage.library_file("/media/movies", bad)


def test_walk_lists_videos_sorted_and_skips_hidden_and_system(media_root: Path) -> None:
    root = media_root / "movies"
    for name in (
        "B.mkv",
        "a/A.MP4",
        "a/notes.txt",
        ".hidden/x.mkv",
        "@eaDir/x.mkv",
        "a/.partial.mkv",
        "noext",
    ):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"data")
    keys = [found.key for found in storage.walk(str(root))]
    assert keys == ["B.mkv", "a/A.MP4"]
    found = next(storage.walk(str(root), "B.mkv"))
    assert (found.key, found.size) == ("B.mkv", 4)
    assert found.mtime.tzinfo is not None
    assert [f.key for f in storage.walk(str(root), "a")] == ["a/A.MP4"]
    assert list(storage.walk(str(root), "gone")) == []
    assert list(storage.walk(str(root), "a/notes.txt")) == []


def test_files_whose_real_path_leaves_the_library_are_skipped(
    media_root: Path, tmp_path: Path
) -> None:
    """G-07: a symlink to a file elsewhere on the server is never scanned or probed."""
    library = media_root / "g07"
    library.mkdir()
    (library / "Real (2020).mkv").write_bytes(b"x")
    secret = tmp_path / "secret.mkv"
    secret.write_bytes(b"y")
    (library / "Escape (2021).mkv").symlink_to(secret)
    (library / "Inside (2022).mkv").symlink_to(library / "Real (2020).mkv")
    keys = [found.key for found in storage.walk(str(library))]
    assert keys == ["Inside (2022).mkv", "Real (2020).mkv"]
    assert list(storage.walk(str(library), "Escape (2021).mkv")) == []
