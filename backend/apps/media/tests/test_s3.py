"""The S3 client (ADR-0022): SigV4 against AWS's published example and our shared
vectors (the edge's njs passes the same file), and the operations on a fake bucket."""

import json
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings

from apps.media import s3
from apps.media.s3 import S3Client, S3Config, S3Error
from apps.media.tests.fake_s3 import FakeBucket

VECTORS_COPY = Path(__file__).parent / "data" / "sigv4_vectors.json"
CANONICAL = (
    Path("/streaming/tests/sigv4_vectors.json"),
    settings.BASE_DIR.parent / "streaming" / "tests" / "sigv4_vectors.json",
)
VECTORS: dict[str, Any] = json.loads(VECTORS_COPY.read_text(encoding="utf-8"))


def test_the_copy_matches_the_streaming_vectors() -> None:
    canonical = next((path for path in CANONICAL if path.is_file()), None)
    if canonical is None:
        pytest.skip("streaming/tests is not mounted here")
    assert VECTORS_COPY.read_bytes() == canonical.read_bytes()


@pytest.mark.parametrize("case", VECTORS["presign"], ids=lambda c: str(c["host"]))
def test_presign_matches_the_vectors(case: dict[str, Any]) -> None:
    query = s3.presign(
        method="GET",
        host=case["host"],
        path=case["path"],
        access_key=VECTORS["access_key"],
        secret_key=VECTORS["secret_key"],
        region=case["region"],
        amz_date=case["amz_date"],
        expires=case["expires"],
    )
    assert query == case["query"]
    if "expect_signature" in case:
        # AWS's own example (S3 docs): independent of our implementation.
        assert query.endswith(case["expect_signature"])


def test_uri_encoding() -> None:
    assert s3.uri_encode("/a b/c~d_e-f.g/h+i") == "/a%20b/c~d_e-f.g/h%2Bi"


def client(bucket: FakeBucket, access: str = "PUBLISHER", **kwargs: Any) -> S3Client:
    config = S3Config(
        endpoint="https://s3.example.test",
        bucket=bucket.name,
        access_key=access,
        secret_key="secret-" + "value",
        region="auto",
    )
    return S3Client(config, transport=bucket.transport(), **kwargs)


def test_put_get_head_list_copy_delete(tmp_path: Path) -> None:
    bucket = FakeBucket()
    with client(bucket) as store:
        store.put_bytes("p/a.m3u8", b"#EXTM3U\n", content_type="application/vnd.apple.mpegurl",
                        cache_control="public, max-age=60")  # fmt: skip
        file = tmp_path / "seg.m4s"
        file.write_bytes(b"s" * 1000)
        immutable = "public, max-age=31536000, immutable"
        size = store.put_file("p/v0-h720/seg.m4s", file, content_type="video/mp4",
                              cache_control=immutable)  # fmt: skip
        assert size == 1000
        assert store.get("p/a.m3u8") == b"#EXTM3U\n"
        assert store.head("p/v0-h720/seg.m4s") == 1000
        assert store.head("p/missing") is None
        store.copy("p/v0-h720/seg.m4s", "q/v0-h720/seg.m4s")
        assert bucket.objects["q/v0-h720/seg.m4s"] == b"s" * 1000
        assert sorted(item.key for item in store.list_objects("p/")) == [
            "p/a.m3u8",
            "p/v0-h720/seg.m4s",
        ]
        assert store.delete_prefix("p/") == 2
    assert set(bucket.objects) == {"q/v0-h720/seg.m4s"}
    assert bucket.headers["p/a.m3u8"]["cache-control"] == "public, max-age=60"


def test_large_files_go_up_in_parts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(s3, "MULTIPART_THRESHOLD", 1000)
    monkeypatch.setattr(s3, "PART_SIZE", 400)
    data = bytes(range(256)) * 10
    file = tmp_path / "compat.mp4"
    file.write_bytes(data)
    bucket = FakeBucket()
    with client(bucket) as store:
        store.put_file("p/compat.mp4", file, content_type="video/mp4", cache_control="x")
    assert bucket.objects["p/compat.mp4"] == data
    assert not bucket.uploads
    assert [m for m, _ in bucket.log].count("PUT") == 7  # 2560 bytes in 400-byte parts


def test_a_failed_part_aborts_the_upload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(s3, "MULTIPART_THRESHOLD", 100)
    monkeypatch.setattr(s3, "PART_SIZE", 100)
    file = tmp_path / "big.mp4"
    file.write_bytes(b"x" * 350)
    bucket = FakeBucket(fail_puts=3)
    with client(bucket) as store, pytest.raises(S3Error) as error:
        store.put_file("p/big.mp4", file, content_type="video/mp4", cache_control="x")
    assert (error.value.status, error.value.code) == (500, "InternalError")
    assert not bucket.uploads  # aborted
    assert "p/big.mp4" not in bucket.objects


def test_errors_keep_the_code_but_not_the_body() -> None:
    bucket = FakeBucket()
    with client(bucket, access="EDGEREAD") as store, pytest.raises(S3Error) as error:
        store.put_bytes("p/x", b"1", content_type="text/plain", cache_control="no-store")
    assert (error.value.status, error.value.code) == (403, "AccessDenied")
    assert bucket.name not in str(error.value)
    with client(bucket, access="UNKNOWN") as store, pytest.raises(S3Error) as error:
        store.get("p/x")
    assert error.value.code == "InvalidAccessKeyId"


def test_documents_with_entities_are_refused() -> None:
    assert s3._parse(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><x>&a;</x>') is None


def test_the_rate_limit_paces_uploads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []
    monkeypatch.setattr("apps.media.s3.time.sleep", slept.append)
    file = tmp_path / "f.bin"
    file.write_bytes(b"z" * (3 * 1024 * 1024))
    bucket = FakeBucket()
    with client(bucket, rate_bps=8 * 1024 * 1024) as store:
        store.put_file("p/f.bin", file, content_type="x", cache_control="x")
    assert sum(slept) > 2.0  # 3 MiB at 1 MiB/s
