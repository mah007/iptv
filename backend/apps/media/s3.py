"""A small S3 client for the origin (M15, ADR-0022): AWS Signature Version 4 on httpx.

Only what publishing needs: put (single or multipart), server-side copy, head, list,
delete, and presigned GET URLs (the edge signs its reads the same way, in njs; both
pass `streaming/tests/sigv4_vectors.json`). It works with any S3-compatible service:
AWS S3, Cloudflare R2 (region `auto`), Backblaze B2, SeaweedFS.

Bodies are sent with `UNSIGNED-PAYLOAD` (TLS protects them in transit; the services
above accept it), small XML bodies with their SHA-256. Error bodies name the bucket
and the key, so messages keep only the status and the S3 error code. Credentials never
leave this module except inside the Authorization header and presigned queries.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, Final
from urllib.parse import quote, urlsplit

import httpx

UNSIGNED: Final = "UNSIGNED-PAYLOAD"
EMPTY_SHA256: Final = hashlib.sha256(b"").hexdigest()
#: Files above this size go up in parts (S3's single PUT allows 5 GB).
MULTIPART_THRESHOLD: Final = 64 * 1024 * 1024
PART_SIZE: Final = 32 * 1024 * 1024
_CHUNK: Final = 1024 * 1024
_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"


class S3Error(Exception):
    """A request failed: `status` (0: no answer) and the service's error `code`."""

    def __init__(self, status: int, code: str, operation: str) -> None:
        super().__init__(f"{operation}: {status} {code}".strip())
        self.status = status
        self.code = code
        self.operation = operation


@dataclass(frozen=True, slots=True)
class S3Config:
    endpoint: str
    bucket: str
    access_key: str = field(repr=False)
    secret_key: str = field(repr=False)
    region: str = "us-east-1"
    path_style: bool = True

    @property
    def scheme_host(self) -> tuple[str, str]:
        parts = urlsplit(self.endpoint.rstrip("/"))
        host = parts.netloc
        if not self.path_style:
            host = f"{self.bucket}.{host}"
        return parts.scheme or "https", host


def uri_encode(path: str) -> str:
    """S3's canonical URI: every segment percent-encoded, unreserved characters kept."""
    return "/".join(quote(segment, safe="-_.~") for segment in path.split("/"))


def _query(params: Mapping[str, str]) -> str:
    return "&".join(
        f"{quote(name, safe='-_.~')}={quote(value, safe='-_.~')}"
        for name, value in sorted(params.items())
    )


def _hmac(key: bytes, text: str) -> bytes:
    return hmac.new(key, text.encode(), hashlib.sha256).digest()


def signing_key(secret_key: str, date: str, region: str, service: str = "s3") -> bytes:
    key = _hmac(f"AWS4{secret_key}".encode(), date)
    key = _hmac(key, region)
    key = _hmac(key, service)
    return _hmac(key, "aws4_request")


def string_to_sign(canonical_request: str, amz_date: str, scope: str) -> str:
    digest = hashlib.sha256(canonical_request.encode()).hexdigest()
    return f"AWS4-HMAC-SHA256\n{amz_date}\n{scope}\n{digest}"


def presign(  # noqa: PLR0913 (the presigned request's facts)
    *,
    method: str,
    host: str,
    path: str,
    access_key: str,
    secret_key: str,
    region: str,
    amz_date: str,
    expires: int,
) -> str:
    """The query string of a presigned request (`X-Amz-*` and the signature).

    `path` is the object's path as sent (it is URI-encoded here); `amz_date` is
    YYYYMMDD'T'HHMMSS'Z'. Shared with the edge's njs (`streaming/nginx/njs/s3.js`).
    """
    date = amz_date[:8]
    scope = f"{date}/{region}/s3/aws4_request"
    params = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": f"{access_key}/{scope}",
        "X-Amz-Date": amz_date,
        "X-Amz-Expires": str(expires),
        "X-Amz-SignedHeaders": "host",
    }
    canonical = "\n".join(
        [method, uri_encode(path), _query(params), f"host:{host}\n", "host", UNSIGNED]
    )
    key = signing_key(secret_key, date, region)
    signature = hmac.new(
        key, string_to_sign(canonical, amz_date, scope).encode(), hashlib.sha256
    ).hexdigest()
    return f"{_query(params)}&X-Amz-Signature={signature}"


def amz_now(now: float | None = None) -> str:
    moment = datetime.fromtimestamp(time.time() if now is None else now, tz=UTC)
    return moment.strftime("%Y%m%dT%H%M%SZ")


def _parse(body: bytes) -> ET.Element | None:
    if b"<!DOCTYPE" in body[:2048].upper() or b"<!ENTITY" in body[:2048].upper():
        return None
    try:
        return ET.fromstring(body)  # noqa: S314 (DOCTYPE and entities refused above)
    except ET.ParseError:
        return None


def _text(element: ET.Element | None, name: str) -> str:
    if element is None:
        return ""
    found = element.find(f"{_NS}{name}")
    if found is None:
        found = element.find(name)
    return (found.text or "") if found is not None else ""


@dataclass(frozen=True, slots=True)
class ObjectInfo:
    key: str
    size: int


class S3Client:
    def __init__(
        self,
        config: S3Config,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 60.0,
        rate_bps: int = 0,
    ) -> None:
        self.config = config
        self.rate_bps = rate_bps
        self.http = httpx.Client(timeout=timeout, transport=transport, follow_redirects=False)

    def close(self) -> None:
        self.http.close()

    def __enter__(self) -> S3Client:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- signing ------------------------------------------------------------------------

    def _path(self, key: str = "") -> str:
        base = f"/{self.config.bucket}" if self.config.path_style else ""
        return f"{base}/{key}" if key else (base or "/")

    def _request(  # noqa: PLR0913 (one signed request)
        self,
        method: str,
        key: str = "",
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        content: bytes | Iterator[bytes] | None = None,
        payload_hash: str = EMPTY_SHA256,
        operation: str,
        ok: tuple[int, ...] = (200,),
    ) -> httpx.Response:
        scheme, host = self.config.scheme_host
        path = self._path(key)
        query = _query(params or {})
        amz_date = amz_now()
        date = amz_date[:8]
        region = self.config.region or "us-east-1"
        scope = f"{date}/{region}/s3/aws4_request"
        sent = {name.lower(): value for name, value in (headers or {}).items()}
        sent.update({"host": host, "x-amz-date": amz_date, "x-amz-content-sha256": payload_hash})
        signed = sorted(
            name for name in sent if name in ("host", "content-md5") or name.startswith("x-amz-")
        )
        canonical = "\n".join(
            [
                method,
                uri_encode(path),
                query,
                "".join(f"{name}:{sent[name].strip()}\n" for name in signed),
                ";".join(signed),
                payload_hash,
            ]
        )
        key_bytes = signing_key(self.config.secret_key, date, region)
        signature = hmac.new(
            key_bytes, string_to_sign(canonical, amz_date, scope).encode(), hashlib.sha256
        ).hexdigest()
        sent["authorization"] = (
            f"AWS4-HMAC-SHA256 Credential={self.config.access_key}/{scope}, "
            f"SignedHeaders={';'.join(signed)}, Signature={signature}"
        )
        sent.pop("host")
        url = f"{scheme}://{host}{uri_encode(path)}" + (f"?{query}" if query else "")
        try:
            response = self.http.request(method, url, headers=sent, content=content)
        except httpx.HTTPError as exc:
            raise S3Error(0, type(exc).__name__, operation) from None
        if response.status_code not in ok:
            code = _text(_parse(response.content), "Code") if response.content else ""
            raise S3Error(response.status_code, code[:64], operation)
        return response

    # --- operations ---------------------------------------------------------------------

    def put_bytes(self, key: str, data: bytes, *, content_type: str, cache_control: str) -> None:
        headers = {
            "Content-Type": content_type,
            "Cache-Control": cache_control,
            "Content-Length": str(len(data)),
        }
        self._request(
            "PUT",
            key,
            headers=headers,
            content=data,
            payload_hash=hashlib.sha256(data).hexdigest(),
            operation="put",
        )

    def _chunks(self, handle: BinaryIO, size: int) -> Iterator[bytes]:
        """`size` bytes from `handle`, at most `rate_bps` per second when limited."""
        left = size
        started = time.monotonic()
        sent = 0
        while left > 0:
            chunk = handle.read(min(_CHUNK, left))
            if not chunk:
                break
            left -= len(chunk)
            sent += len(chunk)
            yield chunk
            if self.rate_bps > 0:
                ahead = sent * 8 / self.rate_bps - (time.monotonic() - started)
                if ahead > 0:
                    time.sleep(ahead)

    def put_file(self, key: str, path: Path, *, content_type: str, cache_control: str) -> int:
        size = path.stat().st_size
        if size > MULTIPART_THRESHOLD:
            self._multipart(key, path, size, content_type=content_type, cache_control=cache_control)
            return size
        headers = {
            "Content-Type": content_type,
            "Cache-Control": cache_control,
            "Content-Length": str(size),
        }
        with path.open("rb") as handle:
            self._request(
                "PUT",
                key,
                headers=headers,
                content=self._chunks(handle, size),
                payload_hash=UNSIGNED,
                operation="put",
            )
        return size

    def _multipart(
        self, key: str, path: Path, size: int, *, content_type: str, cache_control: str
    ) -> None:
        created = self._request(
            "POST",
            key,
            params={"uploads": ""},
            headers={"Content-Type": content_type, "Cache-Control": cache_control},
            operation="multipart_create",
        )
        upload_id = _text(_parse(created.content), "UploadId")
        if not upload_id:
            raise S3Error(created.status_code, "NoUploadId", "multipart_create")
        parts: list[tuple[int, str]] = []
        try:
            with path.open("rb") as handle:
                number = 0
                left = size
                while left > 0:
                    number += 1
                    length = min(PART_SIZE, left)
                    left -= length
                    response = self._request(
                        "PUT",
                        key,
                        params={"partNumber": str(number), "uploadId": upload_id},
                        headers={"Content-Length": str(length)},
                        content=self._chunks(handle, length),
                        payload_hash=UNSIGNED,
                        operation="multipart_part",
                    )
                    parts.append((number, response.headers.get("ETag", "")))
            body = (
                "<CompleteMultipartUpload>"
                + "".join(
                    f"<Part><PartNumber>{n}</PartNumber><ETag>{etag}</ETag></Part>"
                    for n, etag in parts
                )
                + "</CompleteMultipartUpload>"
            ).encode()
            completed = self._request(
                "POST",
                key,
                params={"uploadId": upload_id},
                headers={"Content-Type": "application/xml", "Content-Length": str(len(body))},
                content=body,
                payload_hash=hashlib.sha256(body).hexdigest(),
                operation="multipart_complete",
            )
            # A 200 can still carry an error document.
            if _text(_parse(completed.content), "Code"):
                raise S3Error(200, _text(_parse(completed.content), "Code")[:64], "multipart")
        except BaseException:
            with contextlib.suppress(S3Error):
                self._request(
                    "DELETE",
                    key,
                    params={"uploadId": upload_id},
                    operation="multipart_abort",
                    ok=(200, 204, 404),
                )
            raise

    def copy(self, source_key: str, key: str) -> None:
        source = uri_encode(f"/{self.config.bucket}/{source_key}")
        response = self._request(
            "PUT", key, headers={"x-amz-copy-source": source}, operation="copy"
        )
        if _text(_parse(response.content), "Code"):
            raise S3Error(200, _text(_parse(response.content), "Code")[:64], "copy")

    def head(self, key: str) -> int | None:
        """The object's size, None when it doesn't exist."""
        try:
            response = self._request("HEAD", key, operation="head", ok=(200,))
        except S3Error as exc:
            if exc.status == 404:
                return None
            raise
        return int(response.headers.get("Content-Length", "0"))

    def get(self, key: str) -> bytes:
        return self._request("GET", key, operation="get").content

    def list_objects(self, prefix: str) -> Iterator[ObjectInfo]:
        token = ""
        while True:
            params = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
            if token:
                params["continuation-token"] = token
            root = _parse(self._request("GET", params=params, operation="list").content)
            if root is None:
                raise S3Error(200, "BadListing", "list")
            for item in [*root.iter(f"{_NS}Contents"), *root.iter("Contents")]:
                yield ObjectInfo(_text(item, "Key"), int(_text(item, "Size") or 0))
            if _text(root, "IsTruncated") != "true":
                return
            token = _text(root, "NextContinuationToken")
            if not token:
                return

    def delete_many(self, keys: list[str]) -> None:
        for start in range(0, len(keys), 1000):
            batch = keys[start : start + 1000]
            body = (
                "<Delete><Quiet>true</Quiet>"
                + "".join(f"<Object><Key>{_xml(key)}</Key></Object>" for key in batch)
                + "</Delete>"
            ).encode()
            md5 = base64.b64encode(hashlib.md5(body, usedforsecurity=False).digest()).decode()
            response = self._request(
                "POST",
                params={"delete": ""},
                headers={
                    "Content-Type": "application/xml",
                    "Content-MD5": md5,
                    "Content-Length": str(len(body)),
                },
                content=body,
                payload_hash=hashlib.sha256(body).hexdigest(),
                operation="delete",
            )
            root = _parse(response.content)
            errors = [] if root is None else [*root.iter(f"{_NS}Error"), *root.iter("Error")]
            if errors:
                raise S3Error(200, _text(errors[0], "Code")[:64] or "DeleteFailed", "delete")

    def delete_prefix(self, prefix: str) -> int:
        keys = [item.key for item in self.list_objects(prefix)]
        self.delete_many(keys)
        return len(keys)


def _xml(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
