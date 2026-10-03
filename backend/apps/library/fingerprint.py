"""Fast content fingerprint of a media file (SPEC §7.1).

xxHash64 over the first 16 MiB, the last 16 MiB and the size (8 bytes,
little-endian), so a 50 GB remux costs two 16 MiB reads instead of a full
read. A file of 32 MiB or less is hashed whole. The reconciliation scan uses
the fingerprint to recognise moved files (same fingerprint, new path) and to
notice content that changed under the same path, size and mtime.

Reads are streamed in 1 MiB blocks, so memory stays flat whatever the file
size. `fingerprint_fileobj` accepts any seekable binary file, such as an S3
object wrapped in a ranged reader.
"""

import os
from typing import BinaryIO, Final

import xxhash

__all__ = ["EDGE_BYTES", "fingerprint_fileobj", "fingerprint_path"]

#: Bytes hashed from each end of the file.
EDGE_BYTES: Final = 16 * 1024 * 1024
_BLOCK: Final = 1024 * 1024


def fingerprint_path(path: str | os.PathLike[str]) -> str:
    """Fingerprint of the file at `path`, as 16 lowercase hex digits."""
    with open(path, "rb") as handle:
        size = os.fstat(handle.fileno()).st_size
        return fingerprint_fileobj(handle, size)


def fingerprint_fileobj(handle: BinaryIO, size: int) -> str:
    """Fingerprint of a seekable binary file of `size` bytes (the position is not restored)."""
    if size < 0:
        msg = "size must not be negative"
        raise ValueError(msg)
    digest = xxhash.xxh64()
    if size <= 2 * EDGE_BYTES:
        _update(digest, handle, 0, size)
    else:
        _update(digest, handle, 0, EDGE_BYTES)
        _update(digest, handle, size - EDGE_BYTES, EDGE_BYTES)
    digest.update(size.to_bytes(8, "little"))
    return digest.hexdigest()


def _update(digest: "xxhash.xxh64", handle: BinaryIO, offset: int, length: int) -> None:
    handle.seek(offset)
    remaining = length
    while remaining > 0:
        block = handle.read(min(_BLOCK, remaining))
        if not block:
            msg = f"file ended {remaining} bytes early; it changed while being fingerprinted"
            raise OSError(msg)
        digest.update(block)
        remaining -= len(block)
