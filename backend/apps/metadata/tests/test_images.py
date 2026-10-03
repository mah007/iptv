"""Artwork conversion to WebP/AVIF, blurhash and storage, with generated images (no network)."""

import hashlib
import io
import json
import re
from pathlib import Path

import httpx
import pytest
from PIL import Image, ImageCms, features

from apps.metadata import images
from apps.metadata.images import (
    ImageError,
    LocalImageStore,
    blurhash,
    download_image,
    fetch_and_store,
    store_image,
    tmdb_source_size,
)
from apps.metadata.tmdb import TMDBClient, image_url

KEY = re.compile(
    r"^images/(?P<owner>[a-z0-9/_-]+)/(?P<kind>[a-z]+)/(?P<size>w\d+|original)"
    r"\.(?P<hash>[0-9a-f]{16})\.(?P<ext>webp|avif)$"
)


def picture(width: int, height: int, *, mode: str = "RGB", fmt: str = "JPEG") -> bytes:
    """A two-colour gradient, so resizing and blurhash have something to work with."""
    gradient = Image.linear_gradient("L").resize((width, height))
    image = Image.merge(
        "RGB",
        (
            gradient,
            gradient.transpose(Image.Transpose.ROTATE_90).resize((width, height)),
            Image.new("L", (width, height), 90),
        ),
    )
    if mode == "RGBA":
        alpha = Image.new("L", (width, height), 255)
        alpha.paste(0, (0, 0, width // 2, height))  # left half fully transparent
        image.putalpha(alpha)
    buffer = io.BytesIO()
    image.save(buffer, format=fmt)
    return buffer.getvalue()


@pytest.fixture
def store(tmp_path: Path) -> LocalImageStore:
    return LocalImageStore(tmp_path)


def test_this_pillow_encodes_avif_and_webp() -> None:
    assert features.check("avif")
    assert features.check("webp")


def test_poster_becomes_three_sizes_in_two_formats(store: LocalImageStore) -> None:
    stored = store_image(picture(600, 900), owner="movie/603", kind="poster", store=store)
    assert (stored.width, stored.height) == (600, 900)
    assert {(r.size, r.format) for r in stored.renditions} == {
        (size, fmt) for size in ("w185", "w500", "original") for fmt in ("webp", "avif")
    }
    dimensions = {r.size: (r.width, r.height) for r in stored.renditions}
    assert dimensions == {"w185": (185, 278), "w500": (500, 750), "original": (600, 900)}
    for rendition in stored.renditions:
        match = KEY.match(rendition.key)
        assert match, rendition.key
        assert (match["owner"], match["kind"], match["size"], match["ext"]) == (
            "movie/603",
            "poster",
            rendition.size,
            rendition.format,
        )
        data = store.path(rendition.key).read_bytes()
        assert match["hash"] == hashlib.sha256(data).hexdigest()[:16]
        assert len(data) == rendition.bytes
        decoded = Image.open(io.BytesIO(data))
        assert decoded.format == rendition.format.upper()
        assert decoded.size == (rendition.width, rendition.height)
    assert stored.key("w500", "avif") == stored.keys()["w500"]["avif"]
    assert stored.key("w1280") is None
    assert json.loads(json.dumps(stored.to_json()))["blurhash"] == stored.blurhash


def test_backdrops_get_w780_and_w1280(store: LocalImageStore) -> None:
    stored = store_image(picture(1920, 1080), owner="series/1396", kind="backdrop", store=store)
    assert {r.size: (r.width, r.height) for r in stored.renditions} == {
        "w780": (780, 439),
        "w1280": (1280, 720),
    }
    assert set(stored.keys()) == {"w780", "w1280"}


def test_small_sources_are_never_upscaled(store: LocalImageStore) -> None:
    stored = store_image(picture(120, 180), owner="person/6384", kind="profile", store=store)
    assert {(r.width, r.height) for r in stored.renditions} == {(120, 180)}


def test_names_follow_content_and_rewrites_are_skipped(store: LocalImageStore) -> None:
    source = picture(300, 450)
    first = store_image(source, owner="movie/603", kind="poster", store=store, formats=("webp",))
    path = store.path(first.renditions[0].key)
    written_at = path.stat().st_mtime_ns
    second = store_image(source, owner="movie/603", kind="poster", store=store, formats=("webp",))
    assert first == second
    assert path.stat().st_mtime_ns == written_at
    other = store_image(
        picture(300, 451), owner="movie/603", kind="poster", store=store, formats=("webp",)
    )
    assert other.source_sha256 != first.source_sha256
    assert other.keys() != first.keys()


def test_transparency_survives_for_logos(store: LocalImageStore) -> None:
    stored = store_image(
        picture(400, 160, mode="RGBA", fmt="PNG"), owner="movie/603", kind="logo", store=store
    )
    for rendition in stored.renditions:
        decoded = Image.open(store.path(rendition.key))
        assert decoded.mode == "RGBA", rendition.key
        alpha = decoded.getchannel("A").tobytes()
        row = 2 * decoded.width
        assert alpha[row + 2] < 16  # left half: transparent
        assert alpha[row + decoded.width - 3] > 240  # right half: opaque


def test_exif_orientation_is_applied(store: LocalImageStore) -> None:
    buffer = io.BytesIO()
    exif = Image.Exif()
    exif[0x0112] = 6  # stored sideways: rotate 90 degrees clockwise to display
    Image.new("RGB", (200, 100), (200, 30, 30)).save(buffer, format="JPEG", exif=exif.tobytes())
    stored = store_image(buffer.getvalue(), owner="movie/1", kind="poster", store=store)
    assert (stored.width, stored.height) == (100, 200)


@pytest.mark.parametrize(
    "data",
    [
        b"not an image",
        b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>',
        b"",
        picture(64, 64)[:200],  # truncated JPEG
    ],
)
def test_undecodable_data_is_an_image_error(store: LocalImageStore, data: bytes) -> None:
    with pytest.raises(ImageError):
        store_image(data, owner="movie/1", kind="poster", store=store)


def test_huge_images_are_refused_before_decoding(
    store: LocalImageStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(images, "MAX_PIXELS", 50 * 50)
    with pytest.raises(ImageError, match="pixels"):
        store_image(picture(60, 60, fmt="PNG"), owner="movie/1", kind="poster", store=store)


@pytest.mark.parametrize(
    "owner", ["../etc", "movie/../x", "Movie/603", "/movie/603", "movie//1", ""]
)
def test_owner_must_be_a_safe_relative_name(store: LocalImageStore, owner: str) -> None:
    with pytest.raises(ValueError, match="owner"):
        store_image(picture(10, 10), owner=owner, kind="poster", store=store)


def test_sizes_are_validated(store: LocalImageStore) -> None:
    with pytest.raises(ValueError, match="size"):
        store_image(picture(10, 10), owner="movie/1", kind="poster", store=store, sizes=("x9",))


def test_local_store_keeps_keys_inside_its_root(store: LocalImageStore) -> None:
    for key in ("../outside.webp", "/etc/passwd", "images/../../x"):
        with pytest.raises(ValueError, match="relative"):
            store.path(key)


def test_blurhash_matches_the_reference_encoder() -> None:
    # Golden value from the reference algorithm (woltapp/blurhash, cross-checked).
    gradient = Image.linear_gradient("L").resize((32, 32)).convert("RGB")
    assert blurhash(gradient, (4, 3)) == "L#HetWoffQof00WBfQWBxuj[fQj["


def test_blurhash_shape_and_average_colour() -> None:
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz#$%*+,-.:;=?@[]^_{|}~"
    flat = Image.new("RGB", (40, 20), (255, 0, 0))
    value = blurhash(flat)
    assert value[0] == "L"  # 4x3 components for landscape images
    assert len(value) == 4 + 2 * 4 * 3
    dc = 0
    for char in value[2:6]:
        dc = dc * 83 + alphabet.index(char)
    assert dc == 0xFF0000  # the average colour, exactly
    assert blurhash(Image.new("RGB", (20, 40)))[0] == "T"  # portraits use 3x4
    assert len(blurhash(Image.new("RGBA", (8, 8)), (1, 1))) == 6
    with pytest.raises(ValueError, match="components"):
        blurhash(flat, (10, 1))


def test_tmdb_source_size() -> None:
    assert tmdb_source_size("/poster.jpg") == "original"
    assert tmdb_source_size("/logo.svg") == "w500"


def test_download_image() -> None:
    body = picture(10, 10)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/ok.jpg":
            return httpx.Response(200, content=body)
        if request.url.path == "/big.jpg":
            return httpx.Response(200, content=b"x" * 2048)
        if request.url.path == "/lying.jpg":
            return httpx.Response(200, content=body, headers={"Content-Length": "999999999"})
        if request.url.path == "/down.jpg":
            raise httpx.ConnectError("refused")
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert download_image("https://img.example/ok.jpg", client=client) == body
        with pytest.raises(ImageError, match="404"):
            download_image("https://img.example/missing.jpg", client=client)
        with pytest.raises(ImageError, match="larger"):
            download_image("https://img.example/big.jpg", client=client, max_bytes=1024)
        with pytest.raises(ImageError, match="larger"):
            download_image("https://img.example/lying.jpg", client=client)
        with pytest.raises(ImageError, match="ConnectError"):
            download_image("https://img.example/down.jpg", client=client)


def test_fixture_mode_artwork_end_to_end(store: LocalImageStore) -> None:
    tmdb = TMDBClient.offline()
    details = tmdb.movie_details(603)
    with tmdb.open_image_client() as http:
        poster = fetch_and_store(
            image_url(details["poster_path"], tmdb_source_size(details["poster_path"])),
            client=http,
            owner="movie/603",
            kind="poster",
            store=store,
        )
        backdrop = fetch_and_store(
            image_url(details["backdrop_path"]),
            client=http,
            owner="movie/603",
            kind="backdrop",
            store=store,
        )
    assert (poster.width, poster.height) == (600, 900)
    assert len(poster.renditions) == 6
    assert {r.size for r in backdrop.renditions} == {"w780", "w1280"}
    assert all(store.path(r.key).is_file() for r in (*poster.renditions, *backdrop.renditions))


def test_without_avif_support_artwork_is_webp_only(
    store: LocalImageStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    images.available_formats.cache_clear()
    monkeypatch.setattr(features, "check", lambda name: name != "avif")
    try:
        assert images.available_formats() == ("webp",)
        stored = store_image(picture(300, 450), owner="movie/1", kind="poster", store=store)
        assert {r.format for r in stored.renditions} == {"webp"}
    finally:
        images.available_formats.cache_clear()


def test_icc_profiles_are_kept_only_when_they_still_describe_the_pixels(
    store: LocalImageStore,
) -> None:
    srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    rgb, cmyk = io.BytesIO(), io.BytesIO()
    Image.new("RGB", (64, 96), (10, 120, 200)).save(rgb, format="JPEG", icc_profile=srgb)
    Image.new("CMYK", (64, 96), (200, 40, 0, 10)).save(cmyk, format="JPEG", icc_profile=srgb)
    kept = store_image(rgb.getvalue(), owner="movie/1", kind="poster", store=store)
    dropped = store_image(cmyk.getvalue(), owner="movie/2", kind="poster", store=store)
    for rendition in kept.renditions:
        assert Image.open(store.path(rendition.key)).info.get("icc_profile") == srgb
    for rendition in dropped.renditions:
        decoded = Image.open(store.path(rendition.key))
        assert not decoded.info.get("icc_profile")
        assert decoded.mode == "RGB"
