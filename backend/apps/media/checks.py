"""The S3 origin's Test on the Integrations page (M15, ADR-0022).

With the saved settings it checks, in order:
1. the publisher's keys can write, read and delete an object in the bucket;
2. the edges' keys can read (the edges' probe object, written by the publisher);
3. the edges' keys can't write: they must be read-only (G-44).

It answers a code and a message, never a key, a bucket error body or a URL.
"""

from __future__ import annotations

import contextlib
import secrets

from apps.core import integration
from apps.core.integration import TestCode, TestResult
from apps.core.services import get_secret
from apps.media import publisher
from apps.media.s3 import S3Client, S3Config, S3Error

SERVICE = "The bucket"


def _client(config: S3Config) -> S3Client:
    return S3Client(config, transport=integration._transport, timeout=integration.TIMEOUT_S)


def _failed(step: str, exc: S3Error) -> TestResult:
    if exc.status == 0:
        return TestResult.failed(TestCode.UNREACHABLE, f"{SERVICE} could not be reached ({step}).")
    if exc.status == 403 or exc.code in {"AccessDenied", "SignatureDoesNotMatch"}:
        code = TestCode.AUTH_FAILED if "Signature" in exc.code else TestCode.FORBIDDEN
        return TestResult.failed(code, f"{SERVICE} refused the {step} ({exc.code or exc.status}).")
    return TestResult.failed(
        TestCode.REJECTED, f"{SERVICE} answered {exc.status} {exc.code} to the {step}."
    )


def check_origin() -> TestResult:  # noqa: PLR0911 (one answer per step)
    try:
        config = publisher.config()
    except publisher.NotConfiguredError:
        return TestResult.failed(
            TestCode.NOT_CONFIGURED, "Set the address, the bucket and the publisher's keys."
        )
    key = publisher.object_key(f"integration-test-{secrets.token_hex(8)}.txt")
    body = b"smart-iptv integration test\n"
    with _client(config) as s3:
        try:
            s3.put_bytes(key, body, content_type="text/plain", cache_control="no-store")
            if s3.get(key) != body:
                return TestResult.failed(TestCode.UNEXPECTED, "The object read back differs.")
            s3.delete_many([key])
            publisher.ensure_probe(s3)
        except S3Error as exc:
            return _failed("publisher's request", exc)
    access, secret = (
        get_secret("storage.s3_edge_access_key"),
        get_secret("storage.s3_edge_secret_key"),
    )
    if not access or not secret:
        return TestResult.passed(
            "The publisher's keys work. Set the edges' read-only keys for standalone edges.",
            edges=False,
        )
    edge_config = S3Config(
        endpoint=config.endpoint,
        bucket=config.bucket,
        access_key=access,
        secret_key=secret,
        region=config.region,
        path_style=config.path_style,
    )
    probe = publisher.object_key(publisher.PROBE_KEY)
    with _client(edge_config) as s3:
        try:
            if s3.get(probe) != publisher.PROBE_BODY:
                return TestResult.failed(TestCode.UNEXPECTED, "The edges' keys read other bytes.")
        except S3Error as exc:
            return _failed("edges' read", exc)
        try:
            s3.put_bytes(key, body, content_type="text/plain", cache_control="no-store")
        except S3Error as exc:
            if exc.status in (401, 403):
                return TestResult.passed(
                    "The publisher's keys write, and the edges' keys read but can't write.",
                    edges=True,
                )
            return _failed("edges' write check", exc)
    with _client(config) as s3, contextlib.suppress(S3Error):
        s3.delete_many([key])
    return TestResult.failed(
        TestCode.REJECTED,
        "The edges' keys can write to the bucket: give the edges a read-only key (G-44).",
    )
