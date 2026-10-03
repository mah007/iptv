"""Fingerprints: first and last 16 MiB plus the size, streamed."""

import io
from pathlib import Path

import pytest
import xxhash

from apps.library.fingerprint import EDGE_BYTES, fingerprint_fileobj, fingerprint_path


def _expected(content: bytes) -> str:
    size = len(content)
    hashed = content if size <= 2 * EDGE_BYTES else content[:EDGE_BYTES] + content[-EDGE_BYTES:]
    return xxhash.xxh64(hashed + size.to_bytes(8, "little")).hexdigest()


def _sparse(path: Path, size: int, *, head: bytes, tail: bytes, middle: bytes = b"") -> Path:
    """A large file without writing it all: sparse, with data at both ends."""
    with path.open("wb") as handle:
        handle.truncate(size)
        handle.write(head)
        if middle:
            handle.seek(size // 2)
            handle.write(middle)
        handle.seek(size - len(tail))
        handle.write(tail)
    return path


@pytest.mark.parametrize("size", [0, 1, 1000, EDGE_BYTES, 2 * EDGE_BYTES])
def test_small_files_are_hashed_whole(tmp_path: Path, size: int) -> None:
    content = bytes(range(256)) * (size // 256) + b"x" * (size % 256)
    path = tmp_path / "media.mkv"
    path.write_bytes(content)
    assert fingerprint_path(path) == _expected(content)


def test_fingerprint_is_16_hex_digits(tmp_path: Path) -> None:
    path = tmp_path / "media.mkv"
    path.write_bytes(b"hello")
    value = fingerprint_path(path)
    assert len(value) == 16
    assert int(value, 16) >= 0


def test_large_file_hashes_only_the_edges_and_the_size(tmp_path: Path) -> None:
    size = 2 * EDGE_BYTES + 5 * 1024 * 1024
    base = _sparse(tmp_path / "a.mkv", size, head=b"HEAD", tail=b"TAIL")
    content = base.read_bytes()
    assert fingerprint_path(base) == _expected(content)

    # A change in the middle is invisible by design (that is what makes it fast).
    middle = _sparse(tmp_path / "b.mkv", size, head=b"HEAD", tail=b"TAIL", middle=b"CHANGED")
    assert fingerprint_path(middle) == fingerprint_path(base)

    # A change at either end, or in the size, is not.
    head = _sparse(tmp_path / "c.mkv", size, head=b"HEAX", tail=b"TAIL")
    tail = _sparse(tmp_path / "d.mkv", size, head=b"HEAD", tail=b"TAIX")
    longer = _sparse(tmp_path / "e.mkv", size + 1, head=b"HEAD", tail=b"TAIL")
    values = {fingerprint_path(path) for path in (base, head, tail, longer)}
    assert len(values) == 4


def test_same_content_at_another_path_has_the_same_fingerprint(tmp_path: Path) -> None:
    first = tmp_path / "Movies" / "a.mkv"
    first.parent.mkdir()
    first.write_bytes(b"x" * 4096)
    moved = tmp_path / "moved.mkv"
    moved.write_bytes(first.read_bytes())
    assert fingerprint_path(first) == fingerprint_path(moved)


def test_fileobj_matches_path(tmp_path: Path) -> None:
    content = b"abc" * 100_000
    path = tmp_path / "media.mkv"
    path.write_bytes(content)
    assert fingerprint_fileobj(io.BytesIO(content), len(content)) == fingerprint_path(path)


def test_truncated_reader_is_an_error() -> None:
    with pytest.raises(OSError, match="changed while being fingerprinted"):
        fingerprint_fileobj(io.BytesIO(b"short"), 100)


def test_negative_size_is_rejected() -> None:
    with pytest.raises(ValueError, match="negative"):
        fingerprint_fileobj(io.BytesIO(b""), -1)
