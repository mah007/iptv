"""An in-memory S3-compatible bucket for tests (ADR-0022): the operations the
publisher uses, behind httpx.MockTransport. It checks that every request is signed
for the key it knows (Credential and SignedHeaders), and keeps who wrote what."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from urllib.parse import parse_qs, unquote, urlsplit

import httpx

_CREDENTIAL = re.compile(r"Credential=([^/]+)/(\d{8})/([^/]+)/s3/aws4_request")


@dataclass
class FakeBucket:
    name: str = "media"
    #: access key -> may it write?
    keys: dict[str, bool] = field(default_factory=lambda: {"PUBLISHER": True, "EDGEREAD": False})
    objects: dict[str, bytes] = field(default_factory=dict)
    headers: dict[str, dict[str, str]] = field(default_factory=dict)
    uploads: dict[str, dict[int, bytes]] = field(default_factory=dict)
    log: list[tuple[str, str]] = field(default_factory=list)
    fail_puts: int = 0

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def _error(self, status: int, code: str) -> httpx.Response:
        body = f"<Error><Code>{code}</Code><BucketName>{self.name}</BucketName></Error>"
        return httpx.Response(status, content=body.encode())

    def handle(self, request: httpx.Request) -> httpx.Response:  # noqa: PLR0911, PLR0912
        auth = request.headers.get("authorization", "")
        match = _CREDENTIAL.search(auth)
        if match is None or match.group(1) not in self.keys:
            return self._error(403, "InvalidAccessKeyId")
        if "x-amz-date" not in auth or "x-amz-content-sha256" not in auth:
            return self._error(403, "SignatureDoesNotMatch")
        writer = self.keys[match.group(1)]
        url = urlsplit(str(request.url))
        path = unquote(url.path)
        query = parse_qs(url.query, keep_blank_values=True)
        bucket, _, key = path.lstrip("/").partition("/")
        if bucket != self.name:
            return self._error(404, "NoSuchBucket")
        method = request.method
        self.log.append((method, key))
        if method in ("PUT", "POST", "DELETE") and not writer:
            return self._error(403, "AccessDenied")
        if method == "GET" and query.get("list-type") == ["2"]:
            prefix = query.get("prefix", [""])[0]
            keys = sorted(k for k in self.objects if k.startswith(prefix))
            items = "".join(
                f"<Contents><Key>{k}</Key><Size>{len(self.objects[k])}</Size></Contents>"
                for k in keys
            )
            return httpx.Response(
                200,
                content=f"<ListBucketResult><IsTruncated>false</IsTruncated>{items}</ListBucketResult>".encode(),
            )
        if method == "POST" and "delete" in query:
            root = ET.fromstring(request.content)  # noqa: S314 (test data)
            for item in root.iter("Key"):
                self.objects.pop(item.text or "", None)
            return httpx.Response(200, content=b"<DeleteResult/>")
        if method == "POST" and "uploads" in query:
            upload = f"up{len(self.uploads) + 1}"
            self.uploads[upload] = {}
            return httpx.Response(
                200,
                content=f"<InitiateMultipartUploadResult><UploadId>{upload}</UploadId></InitiateMultipartUploadResult>".encode(),
            )
        if method == "PUT" and self.fail_puts > 0:
            self.fail_puts -= 1
            return self._error(500, "InternalError")
        if method == "PUT" and "partNumber" in query:
            upload = query["uploadId"][0]
            self.uploads[upload][int(query["partNumber"][0])] = request.read()
            return httpx.Response(200, headers={"ETag": f'"etag{query["partNumber"][0]}"'})
        if method == "POST" and "uploadId" in query:
            parts = self.uploads.pop(query["uploadId"][0])
            self.objects[key] = b"".join(parts[n] for n in sorted(parts))
            return httpx.Response(200, content=b"<CompleteMultipartUploadResult/>")
        if method == "DELETE" and "uploadId" in query:
            self.uploads.pop(query["uploadId"][0], None)
            return httpx.Response(204)
        if method == "PUT":
            source = request.headers.get("x-amz-copy-source")
            if source:
                _, _, source_key = unquote(source).lstrip("/").partition("/")
                if source_key not in self.objects:
                    return self._error(404, "NoSuchKey")
                self.objects[key] = self.objects[source_key]
                return httpx.Response(200, content=b"<CopyObjectResult/>")
            self.objects[key] = request.read()
            self.headers[key] = {
                "content-type": request.headers.get("content-type", ""),
                "cache-control": request.headers.get("cache-control", ""),
            }
            return httpx.Response(200)
        if method in ("GET", "HEAD"):
            if key not in self.objects:
                return self._error(404, "NoSuchKey")
            data = self.objects[key]
            return httpx.Response(
                200,
                content=b"" if method == "HEAD" else data,
                headers={"Content-Length": str(len(data))},
            )
        return self._error(400, "Unsupported")
