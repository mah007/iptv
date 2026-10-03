"""Artwork: download, convert to WebP and AVIF, blurhash, store (SPEC §7.2 step 7).

Each source image becomes one rendition per size and format:

- posters, logos, stills and profiles at `w185`, `w500` and `original`;
- backdrops at `w780` and `w1280`.

A `wNNN` rendition is NNN pixels wide (never upscaled: a smaller source keeps
its width). Files go to `images/{owner}/{kind}/{size}.{hash}.{ext}`, where
`hash` is the first 16 hex digits of the file's SHA-256: names change whenever
content does, so the edge and CDN can cache them for a year as immutable.
EXIF orientation is applied, transparency is kept (logos), metadata other than
the ICC profile is dropped, and a blurhash of the image is returned for
placeholders.

Untrusted input is bounded: downloads stop at `MAX_DOWNLOAD_BYTES`, only JPEG,
PNG, WebP, AVIF and GIF are decoded, and images over `MAX_PIXELS` are refused
before they are decoded.
"""

import functools
import hashlib
import io
import logging
import math
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, Literal, Protocol

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError, features

__all__ = [
    "FORMATS",
    "MAX_DOWNLOAD_BYTES",
    "MAX_PIXELS",
    "SIZES",
    "ImageError",
    "ImageFormat",
    "ImageKind",
    "ImageStore",
    "LocalImageStore",
    "Rendition",
    "StoredImage",
    "available_formats",
    "blurhash",
    "download_image",
    "fetch_and_store",
    "store_image",
    "tmdb_source_size",
]

logger = logging.getLogger(__name__)

type ImageKind = Literal["poster", "backdrop", "logo", "still", "profile"]
type ImageFormat = Literal["webp", "avif"]

SIZES: Final[Mapping[ImageKind, tuple[str, ...]]] = MappingProxyType(
    {
        "poster": ("w185", "w500", "original"),
        "backdrop": ("w780", "w1280"),
        "logo": ("w185", "w500", "original"),
        "still": ("w185", "w500", "original"),
        "profile": ("w185", "w500", "original"),
    }
)
FORMATS: Final[tuple[ImageFormat, ...]] = ("webp", "avif")
MAX_DOWNLOAD_BYTES: Final = 20 * 1024 * 1024
MAX_PIXELS: Final = 40_000_000

_DECODERS: Final = ("JPEG", "PNG", "WEBP", "AVIF", "GIF")
# Modes whose ICC profile still describes the pixels after conversion to RGB(A).
_ICC_SAFE_MODES: Final = frozenset({"RGB", "RGBA", "RGBX", "L", "LA", "P", "PA"})
_CONTENT_TYPES: Final[Mapping[ImageFormat, str]] = {"webp": "image/webp", "avif": "image/avif"}
_ENCODER_OPTIONS: Final[Mapping[ImageFormat, Mapping[str, Any]]] = {
    "webp": {"format": "WEBP", "quality": 80, "method": 4},
    "avif": {"format": "AVIF", "quality": 60, "speed": 6},
}
_OWNER: Final = re.compile(r"^[a-z0-9]+(?:[_-][a-z0-9]+)*(?:/[a-z0-9]+(?:[_-][a-z0-9]+)*)*$")
_SIZE: Final = re.compile(r"^(?:original|w\d{2,4})$")
_BLURHASH_SIDE: Final = 32
_BASE83: Final = (
    "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz#$%*+,-.:;=?@[]^_{|}~"
)
_SRGB_TO_LINEAR: Final = tuple(
    value / 255 / 12.92 if value / 255 <= 0.04045 else ((value / 255 + 0.055) / 1.055) ** 2.4
    for value in range(256)
)


class ImageError(Exception):
    """The image could not be downloaded, decoded or converted."""


class ImageStore(Protocol):
    """Where renditions are written; keys are relative paths such as `images/movie/603/…`."""

    def save(self, key: str, data: bytes, content_type: str) -> None: ...


class LocalImageStore:
    """A directory (the media volume the edge serves `images/` from)."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, key: str) -> Path:
        if key.startswith("/") or ".." in key.split("/"):
            msg = "storage keys are relative and stay inside the root"
            raise ValueError(msg)
        return self.root / key

    def save(self, key: str, data: bytes, content_type: str) -> None:
        target = self.path(key)
        if target.is_file() and target.stat().st_size == len(data):
            return  # content-hashed name: the same bytes are already there
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=target.parent, prefix=".tmp-")
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
            Path(temporary).chmod(0o644)
            Path(temporary).replace(target)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise


@dataclass(frozen=True, slots=True)
class Rendition:
    size: str
    format: ImageFormat
    key: str
    width: int
    height: int
    bytes: int


@dataclass(frozen=True, slots=True)
class StoredImage:
    """What `MediaImage` keeps: source size, blurhash and a storage key per size and format."""

    kind: ImageKind
    width: int
    height: int
    blurhash: str
    source_sha256: str
    renditions: tuple[Rendition, ...]

    def key(self, size: str, image_format: ImageFormat = "webp") -> str | None:
        for rendition in self.renditions:
            if rendition.size == size and rendition.format == image_format:
                return rendition.key
        return None

    def keys(self) -> dict[str, dict[str, str]]:
        """`{"w185": {"webp": key, "avif": key}, …}`."""
        keys: dict[str, dict[str, str]] = {}
        for rendition in self.renditions:
            keys.setdefault(rendition.size, {})[rendition.format] = rendition.key
        return keys

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "width": self.width,
            "height": self.height,
            "blurhash": self.blurhash,
            "source_sha256": self.source_sha256,
            "renditions": [
                {
                    "size": r.size,
                    "format": r.format,
                    "key": r.key,
                    "width": r.width,
                    "height": r.height,
                    "bytes": r.bytes,
                }
                for r in self.renditions
            ],
        }


@functools.cache
def available_formats() -> tuple[ImageFormat, ...]:
    """`FORMATS` this Pillow can encode: AVIF needs a Pillow built with libavif."""
    supported = {"webp": features.check("webp"), "avif": features.check("avif")}
    missing = [name for name, ok in supported.items() if not ok]
    if missing:
        logger.warning("Pillow cannot encode %s; artwork is stored without it", ", ".join(missing))
    return tuple(name for name in FORMATS if supported[name])


def tmdb_source_size(file_path: str) -> str:
    """The TMDB size to download: `original`, except `w500` for SVG logos (served rasterised)."""
    return "w500" if file_path.lower().endswith(".svg") else "original"


def download_image(url: str, *, client: httpx.Client, max_bytes: int = MAX_DOWNLOAD_BYTES) -> bytes:
    """GET `url` into memory, refusing bodies over `max_bytes`."""
    try:
        with client.stream("GET", url) as response:
            if response.status_code != httpx.codes.OK:
                msg = f"image download failed with HTTP {response.status_code}"
                raise ImageError(msg)
            declared = response.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                msg = f"image is larger than {max_bytes} bytes"
                raise ImageError(msg)
            chunks: list[bytes] = []
            received = 0
            for chunk in response.iter_bytes():
                received += len(chunk)
                if received > max_bytes:
                    msg = f"image is larger than {max_bytes} bytes"
                    raise ImageError(msg)
                chunks.append(chunk)
    except httpx.HTTPError as exc:
        msg = f"image download failed ({type(exc).__name__})"
        raise ImageError(msg) from exc
    return b"".join(chunks)


def store_image(  # noqa: PLR0913
    data: bytes,
    *,
    owner: str,
    kind: ImageKind,
    store: ImageStore,
    sizes: Sequence[str] | None = None,
    formats: Sequence[ImageFormat] | None = None,
) -> StoredImage:
    """Convert `data` into every size and format, write them to `store` and describe them.

    `owner` names the catalog object, as lowercase path segments (`movie/603`).
    `formats` defaults to `available_formats()`.
    """
    if not _OWNER.match(owner) or len(owner) > 128:
        msg = f"invalid image owner {owner!r}"
        raise ValueError(msg)
    wanted = tuple(sizes) if sizes is not None else SIZES[kind]
    for size in wanted:
        if not _SIZE.match(size):
            msg = f"invalid image size {size!r}"
            raise ValueError(msg)
    image = _decode(data)
    renditions: list[Rendition] = []
    for size in wanted:
        resized = _resize(image, size)
        for image_format in formats if formats is not None else available_formats():
            encoded = _encode(resized, image_format, image.info.get("icc_profile"))
            digest = hashlib.sha256(encoded).hexdigest()[:16]
            key = f"images/{owner}/{kind}/{size}.{digest}.{image_format}"
            store.save(key, encoded, _CONTENT_TYPES[image_format])
            renditions.append(
                Rendition(
                    size=size,
                    format=image_format,
                    key=key,
                    width=resized.width,
                    height=resized.height,
                    bytes=len(encoded),
                )
            )
    return StoredImage(
        kind=kind,
        width=image.width,
        height=image.height,
        blurhash=blurhash(image),
        source_sha256=hashlib.sha256(data).hexdigest(),
        renditions=tuple(renditions),
    )


def fetch_and_store(  # noqa: PLR0913
    url: str,
    *,
    client: httpx.Client,
    owner: str,
    kind: ImageKind,
    store: ImageStore,
    sizes: Sequence[str] | None = None,
    formats: Sequence[ImageFormat] | None = None,
) -> StoredImage:
    """`download_image` then `store_image`."""
    data = download_image(url, client=client)
    return store_image(data, owner=owner, kind=kind, store=store, sizes=sizes, formats=formats)


def blurhash(image: Image.Image, components: tuple[int, int] | None = None) -> str:
    """The BlurHash (https://blurha.sh) of `image`: 4x3 components, 3x4 for portraits.

    Computed on a copy at most 32 pixels on its longer side; transparency is
    flattened onto mid-grey.
    """
    x_components, y_components = components or ((3, 4) if image.height > image.width else (4, 3))
    if not (1 <= x_components <= 9 and 1 <= y_components <= 9):
        msg = "BlurHash takes 1 to 9 components per axis"
        raise ValueError(msg)
    small = _flatten(image)
    small.thumbnail((_BLURHASH_SIDE, _BLURHASH_SIDE), Image.Resampling.BILINEAR)
    width, height = small.size
    data = small.tobytes()
    pixels = [
        (_SRGB_TO_LINEAR[data[k]], _SRGB_TO_LINEAR[data[k + 1]], _SRGB_TO_LINEAR[data[k + 2]])
        for k in range(0, len(data), 3)
    ]
    cos_x = [[math.cos(math.pi * i * x / width) for x in range(width)] for i in range(x_components)]
    cos_y = [
        [math.cos(math.pi * j * y / height) for y in range(height)] for j in range(y_components)
    ]
    factors: list[tuple[float, float, float]] = []
    for j in range(y_components):
        for i in range(x_components):
            red = green = blue = 0.0
            for y in range(height):
                row_basis = cos_y[j][y]
                offset = y * width
                for x in range(width):
                    basis = cos_x[i][x] * row_basis
                    pixel = pixels[offset + x]
                    red += basis * pixel[0]
                    green += basis * pixel[1]
                    blue += basis * pixel[2]
            scale = (1.0 if i == 0 and j == 0 else 2.0) / (width * height)
            factors.append((red * scale, green * scale, blue * scale))
    dc, ac = factors[0], factors[1:]
    parts = [_base83(x_components - 1 + (y_components - 1) * 9, 1)]
    if ac:
        actual_max = max(abs(value) for factor in ac for value in factor)
        quantised_max = max(0, min(82, math.floor(actual_max * 166 - 0.5)))
        max_value = (quantised_max + 1) / 166
        parts.append(_base83(quantised_max, 1))
    else:
        max_value = 1.0
        parts.append(_base83(0, 1))
    parts.append(
        _base83(
            (_linear_to_srgb(dc[0]) << 16) + (_linear_to_srgb(dc[1]) << 8) + _linear_to_srgb(dc[2]),
            4,
        )
    )
    for factor in ac:
        red_q, green_q, blue_q = (_quantise_ac(value / max_value) for value in factor)
        parts.append(_base83(red_q * 19 * 19 + green_q * 19 + blue_q, 2))
    return "".join(parts)


def _decode(data: bytes) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(data), formats=_DECODERS)
        if image.width * image.height > MAX_PIXELS:
            msg = f"image is larger than {MAX_PIXELS} pixels"
            raise ImageError(msg)
        image.load()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError) as exc:
        msg = f"not a supported image ({type(exc).__name__})"
        raise ImageError(msg) from exc
    icc_profile = image.info.get("icc_profile") if image.mode in _ICC_SAFE_MODES else None
    oriented = ImageOps.exif_transpose(image)
    has_alpha = oriented.mode in {"RGBA", "LA", "PA"} or (
        oriented.mode == "P" and "transparency" in oriented.info
    )
    converted = oriented.convert("RGBA" if has_alpha else "RGB")
    converted.info.pop("icc_profile", None)  # convert() copies the source's, CMYK ones too
    if icc_profile:
        converted.info["icc_profile"] = icc_profile
    return converted


def _resize(image: Image.Image, size: str) -> Image.Image:
    if size == "original" or int(size[1:]) >= image.width:
        return image
    width = int(size[1:])
    height = max(1, round(image.height * width / image.width))
    return image.resize((width, height), Image.Resampling.LANCZOS, reducing_gap=3.0)


def _encode(image: Image.Image, image_format: ImageFormat, icc_profile: bytes | None) -> bytes:
    buffer = io.BytesIO()
    options = dict(_ENCODER_OPTIONS[image_format])
    if icc_profile:
        options["icc_profile"] = icc_profile
    try:
        image.save(buffer, **options)
    except (OSError, ValueError, KeyError) as exc:
        msg = f"could not encode {image_format} ({type(exc).__name__})"
        raise ImageError(msg) from exc
    return buffer.getvalue()


def _flatten(image: Image.Image) -> Image.Image:
    if image.mode == "RGB":
        return image.copy()
    rgba = image.convert("RGBA")
    background = Image.new("RGBA", rgba.size, (128, 128, 128, 255))
    return Image.alpha_composite(background, rgba).convert("RGB")


def _linear_to_srgb(value: float) -> int:
    clamped = max(0.0, min(1.0, value))
    if clamped <= 0.0031308:
        return int(clamped * 12.92 * 255 + 0.5)
    return int((1.055 * clamped ** (1 / 2.4) - 0.055) * 255 + 0.5)


def _quantise_ac(value: float) -> int:
    signed_root = math.copysign(abs(value) ** 0.5, value)
    return max(0, min(18, math.floor(signed_root * 9 + 9.5)))


def _base83(value: int, length: int) -> str:
    return "".join(_BASE83[(value // 83 ** (length - 1 - i)) % 83] for i in range(length))
