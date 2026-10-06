"""`manage.py ensure_libraries`: the installer's default Movies and Series libraries."""

import io
from collections.abc import Callable
from pathlib import Path

import pytest
from django.core.management import call_command

from apps.library import services
from apps.library.models import Library

pytestmark = pytest.mark.django_db


def run() -> str:
    out = io.StringIO()
    call_command("ensure_libraries", stdout=out)
    return out.getvalue()


def test_adds_a_library_per_folder_and_scans_it_once(
    media_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scans: list[str] = []
    monkeypatch.setattr(services, "request_scan", lambda library, **_: scans.append(library.name))
    output = run()
    assert "Added the Movies library" in output
    assert "Added the Series library" in output
    assert set(Library.objects.values_list("name", "path")) == {
        ("Movies", str(media_root / "movies")),
        ("Series", str(media_root / "series")),
    }
    assert scans == ["Movies", "Series"]

    again = run()
    assert again.count("A library already covers") == 2
    assert Library.objects.count() == 2
    assert scans == ["Movies", "Series"]


def test_skips_missing_folders_and_the_admins_own_libraries(
    media_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    make_library: Callable[..., Library],
) -> None:
    monkeypatch.setattr(services, "request_scan", lambda library, **_: None)
    (media_root / "series").rmdir()
    (media_root / "films").mkdir()
    make_library(name="Movies", folder="films")
    output = run()
    assert "A library named Movies exists elsewhere" in output
    assert "skipped the Series library" in output
    assert Library.objects.count() == 1
