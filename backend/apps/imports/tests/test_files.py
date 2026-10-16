"""The library side of imports: types, names, staging and the atomic move (ADR-0025)."""

import errno
import os
from pathlib import Path

import pytest

from apps.imports import files
from apps.imports.files import PathRefused
from apps.imports.models import FileKind


def test_only_videos_and_subtitles_are_imported() -> None:
    assert files.file_kind("Film (2001).MKV") == FileKind.VIDEO
    assert files.file_kind("dir/Film.m2ts") == FileKind.VIDEO
    assert files.file_kind("Film.ar.srt") == FileKind.SUBTITLE
    assert files.file_kind("Film.en.forced.ass") == FileKind.SUBTITLE
    for name in ("poster.jpg", "movie.nfo", "setup.exe", "archive.zip", ".mkv", "mkv"):
        assert files.file_kind(name) is None


def test_subfolders_and_names() -> None:
    assert files.clean_subfolder(" /Classics/Silent/ ") == "Classics/Silent"
    assert files.clean_subfolder("") == ""
    for bad in ("../x", "a/../b", ".hidden", "a/.b", "a\\b", "a\x00b"):
        with pytest.raises(PathRefused):
            files.clean_subfolder(bad)
    assert files.clean_file_name("Metropolis (1927).mp4") == "Metropolis (1927).mp4"
    for bad in ("a/b.mp4", ".x.mp4", "", "x" * 300 + ".mp4"):
        with pytest.raises(PathRefused) as error:
            files.clean_file_name(bad)
        assert error.value.code == "invalid_name"
    with pytest.raises(PathRefused) as error:
        files.clean_file_name("film.zip")
    assert error.value.code == "unsupported_type"
    assert files.name_from_url(
        "https://archive.org/download/x/Night%20of%20the%20Living%20Dead.mp4?a=1"
    ) == ("Night of the Living Dead.mp4")
    assert files.name_from_url("https://example.com/") == ""


def test_library_keys_keep_the_selected_name() -> None:
    assert files.library_key("", "Films/Metropolis (1927)", True, "Metropolis (1927).mkv") == (
        "Metropolis (1927)/Metropolis (1927).mkv"
    )
    assert files.library_key("Classics", "Films/Nosferatu (1922).mp4", False) == (
        "Classics/Nosferatu (1922).mp4"
    )
    assert files.library_key("A", "Show", True, "Season 1/E01.mkv") == "A/Show/Season 1/E01.mkv"
    with pytest.raises(PathRefused):
        files.library_key("", "Show", True, "../escape.mkv")


def test_space_and_staging(tmp_path: Path) -> None:
    assert files.Space(free=100, needed=60, reserve=40).fits
    assert not files.Space(free=100, needed=61, reserve=40).fits
    library = tmp_path / "movies"
    library.mkdir()
    staging = files.staging_dir(str(library), "abc")
    assert staging == library / ".smart-iptv-import" / "abc"
    assert files.staged_bytes(staging) == 0
    files.make_dirs(staging / "a")
    (staging / "a" / "f.mkv").write_bytes(b"12345")
    assert files.staged_bytes(staging) == 5
    assert oct(staging.stat().st_mode & 0o777) == "0o755"
    files.remove_staging(staging)
    assert not (library / ".smart-iptv-import").exists()
    assert files.free_bytes(library) > 0
    assert files.device(library) == os.stat(tmp_path).st_dev


def test_place_moves_atomically_and_never_overwrites(tmp_path: Path) -> None:
    library = tmp_path / "movies"
    staged = tmp_path / "staged.mkv"
    staged.write_bytes(b"video")
    library.mkdir()
    target = files.place(staged, str(library), "Film (2001)/Film (2001).mkv")
    assert target.read_bytes() == b"video"
    assert not staged.exists()
    assert oct(target.stat().st_mode & 0o777) == "0o644"
    again = tmp_path / "again.mkv"
    again.write_bytes(b"other")
    with pytest.raises(PathRefused) as error:
        files.place(again, str(library), "Film (2001)/Film (2001).mkv")
    assert error.value.code == "target_exists"
    assert target.read_bytes() == b"video"
    with pytest.raises(ValueError, match="relative"):
        files.place(again, str(library), "../escape.mkv")


@pytest.mark.parametrize("failure", [errno.EXDEV, errno.EPERM])
def test_place_without_hard_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: int
) -> None:
    """Across file systems (EXDEV) a hidden copy is renamed; without hard links (EPERM on
    some network file systems) a plain rename."""
    library = tmp_path / "movies"
    library.mkdir()
    staged = tmp_path / "staged.mkv"
    staged.write_bytes(b"video")

    def no_link(*_args: object) -> None:
        raise OSError(failure, os.strerror(failure))

    monkeypatch.setattr(os, "link", no_link)
    target = files.place(staged, str(library), "Film.mkv")
    assert target.read_bytes() == b"video"
    assert not staged.exists()
    assert not list(library.glob(".*"))
