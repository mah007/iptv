"""Credentials and tokens never survive redaction (SPEC §11)."""

import datetime as dt
import uuid
from typing import Any

import pytest

from apps.core.redaction import (
    MASK,
    is_sensitive_key,
    redact_event_dict,
    redact_text,
    redact_value,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Xtream play URLs carry credentials in the path.
        ("/movie/alice/s3cr3t/1.mp4", "/movie/***/***/1.mp4"),
        ("/series/alice/s3cr3t/42.mkv", "/series/***/***/42.mkv"),
        ("/live/alice/s3cr3t/7.ts", "/live/***/***/7.ts"),
        (
            "/timeshift/alice/s3cr3t/60/2026-10-03:12-00/7.ts",
            "/timeshift/***/***/60/2026-10-03:12-00/7.ts",
        ),
        ("GET /MOVIE/Alice/S3cr3t/1.mp4", "GET /MOVIE/***/***/1.mp4"),
        ("/movie/alice/s3cr3t", "/movie/***/***"),
        ("/movie/alice/s3cr3t?x=1", "/movie/***/***?x=1"),
        # Decoded paths: a typed password may hold ? # spaces or slashes, or be numeric.
        ("/movie/alice/pa?ss word#x/1.mp4", "/movie/***/***/1.mp4"),
        ("Not Found: /series/bob/with space/55.mkv", "Not Found: /series/***/***/55.mkv"),
        ("/live/u/a/b/c/7.ts", "/live/***/***/7.ts"),
        ("/movie/alice/123/1.mp4", "/movie/***/***/1.mp4"),
        ("/movie/alice/s3cr3t/1.mp4?token=x", "/movie/***/***/1.mp4?token=***"),
        (
            "/timeshift/alice/p w#?/60/2026-10-03:12-00/7.ts",
            "/timeshift/***/***/60/2026-10-03:12-00/7.ts",
        ),
        # Live TV (M12): every play URL shape, the short form included.
        ("/live/alice/s3cr3t/7.m3u8", "/live/***/***/7.m3u8"),
        ("/alice/s3cr3t/7", "/***/***/7"),
        ("/alice/s3cr3t/7.ts", "/***/***/7.ts"),
        ("/alice/s3cr3t/7.m3u8", "/***/***/7.m3u8"),
        ("Not Found: /alice/s3cr3t/3001", "Not Found: /***/***/3001"),
        ("http://tv.example.com:8080/alice/s3cr3t/3001", "http://tv.example.com:8080/***/***/3001"),
        ('"GET /alice/s3cr3t/3001 HTTP/1.1"', '"GET /***/***/3001 HTTP/1.1"'),
        (
            "/timeshift/alice/s3cr3t/60/2026-10-03:12-00-30/7.ts",
            "/timeshift/***/***/60/2026-10-03:12-00-30/7.ts",
        ),
        (
            "/timeshift/alice/s3cr3t/90/2026-10-03:12-00/7.m3u8",
            "/timeshift/***/***/90/2026-10-03:12-00/7.m3u8",
        ),
        # Signed media URLs, live and catch-up included.
        ("/v/eyJhbGciOi.abc-def/master.m3u8", "/v/***/master.m3u8"),
        ("/v/k2.abc.def.live.1.sig/live.ts", "/v/***/live.ts"),
        ("/v/k2.abc.def.archive.1.sig/archive/1791279220-60.ts", "/v/***/archive/1791279220-60.ts"),
        # Query-string and form parameters.
        (
            "/player_api.php?username=alice&password=s3cr3t",
            "/player_api.php?username=***&password=***",
        ),
        ("get.php?username=alice&pass=s3cr3t&type=m3u", "get.php?username=***&pass=***&type=m3u"),
        ("token=abc123", "token=***"),
        ("access_token=a&refresh_token=b", "access_token=***&refresh_token=***"),
        ("api_key=k1&key=k2&code=123456", "api_key=***&key=***&code=***"),
        ("secret=x; other=1", "secret=***; other=1"),
        ("PASSWORD=Hunter2", "PASSWORD=***"),
        # Bearer tokens.
        ("Authorization: Bearer eyJ0eXAi.payload.sig", "Authorization: Bearer ***"),
        ("bearer abcdefgh1234", "bearer ***"),
        # Credentials inside URLs, e.g. in a connection error.
        ("redis://:hunter2@redis-state:6379/0", "redis://***:***@redis-state:6379/0"),
        ("postgresql://iptv:hunter2@postgres/iptv", "postgresql://***:***@postgres/iptv"),
        # Quoted pairs inside text (request bodies, dict reprs).
        ('{"username": "alice", "password": "s3cr3t"}', '{"username": "***", "password": "***"}'),
        ("{'new_password': 'x', 'name': 'y'}", "{'new_password': '***', 'name': 'y'}"),
        ('{"otpauth_uri": "otpauth://totp/x?secret=ABC"}', '{"otpauth_uri": "***"}'),
    ],
)
def test_redact_text_masks_credentials(text: str, expected: str) -> None:
    assert redact_text(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "/api/v1/admin/settings",
        "/movies/popular",
        "monkey=1&sort_key=name",
        "Basic plan activated",
        "the bearer of bad news",
        "/v/",
        '{"name": "Smart IPTV"}',
        "https://tv.example.com/player_api.php",
    ],
)
def test_redact_text_leaves_harmless_text_alone(text: str) -> None:
    assert redact_text(text) == text


@pytest.mark.parametrize(
    ("key", "sensitive"),
    [
        ("password", True),
        ("new_password", True),
        ("password_hash", True),
        ("passwd", True),
        ("HTTP_AUTHORIZATION", True),
        ("HTTP_COOKIE", True),
        ("csrftoken", True),
        ("sessionid", True),
        ("refresh_token", True),
        ("tmdb_api_key", True),
        ("secret_encrypted", True),
        ("otp", True),
        ("mfa_otp", True),
        ("otpauth_uri", True),
        ("totp_secret", True),
        ("credentials", True),
        ("mfa_code", True),
        ("source_url", True),
        ("username", False),
        ("key", False),
        ("value", False),
        ("footprint", False),
        ("request_id", False),
        ("user_id", False),
    ],
)
def test_sensitive_keys(key: str, sensitive: bool) -> None:
    assert is_sensitive_key(key) is sensitive


def test_redact_value_walks_structures_and_masks_sensitive_keys() -> None:
    marker = uuid.uuid4()
    when = dt.datetime(2026, 10, 3, tzinfo=dt.UTC)
    value: dict[str, Any] = {
        "username": "alice",
        "password": "s3cr3t",
        "nested": {"api_key": "k", "url": "/movie/alice/s3cr3t/1.mp4", "count": 3},
        "items": [{"token": "t"}, "Bearer abc.def.ghi", 7, None, True],
        "pair": ("pass=x", 1),
        "tags": {"secret=y"},
        "raw": b"password=z",
        "id": marker,
        "at": when,
    }
    result = redact_value(value)
    assert result == {
        "username": "alice",
        "password": MASK,
        "nested": {"api_key": MASK, "url": "/movie/***/***/1.mp4", "count": 3},
        "items": [{"token": MASK}, "Bearer ***", 7, None, True],
        "pair": ("pass=***", 1),
        "tags": ["secret=***"],
        "raw": "password=***",
        "id": marker,
        "at": when,
    }
    # The input is never modified.
    assert value["password"] == "s3cr3t"  # noqa: S105 (the fake credential under test)
    assert value["nested"]["api_key"] == "k"


def test_redact_value_stops_at_absurd_depth() -> None:
    deep: dict[str, Any] = {}
    node = deep
    for _ in range(50):
        node["child"] = {}
        node = node["child"]
    node["password"] = "x"  # noqa: S105 (the fake credential under test)
    result = redact_value(deep)
    for _ in range(33):
        result = result["child"]
    assert result == MASK


def test_event_dict_processor_redacts_values_and_keeps_structlog_meta() -> None:
    record = object()
    event = {
        "event": "login failed for /live/alice/s3cr3t/1.ts",
        "password": "s3cr3t",
        "query": "username=alice&password=s3cr3t",
        "_record": record,
        "_from_structlog": True,
        "status": 401,
    }
    result = redact_event_dict(None, "info", event)
    assert result == {
        "event": "login failed for /live/***/***/1.ts",
        "password": MASK,
        "query": "username=***&password=***",
        "_record": record,
        "_from_structlog": True,
        "status": 401,
    }
