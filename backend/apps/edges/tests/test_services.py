"""Edges' services (ADR-0022): secrets, the auth mirror, reports, status, the snapshot,
what an edge is sent, and playback URLs (ours and Bunny's)."""

import time
from datetime import UTC, datetime
from typing import Any, cast

import httpx
import pytest
from django.utils import timezone

from apps.core.services import set_setting
from apps.core.stores import cache_redis, state_redis
from apps.edges import services
from apps.edges.models import Audience, Edge, EdgeKind, EdgeStorage
from apps.edges.selection import EdgeView
from apps.playback import tokens

pytestmark = pytest.mark.django_db

NOW = 1_800_000_000.0


def standalone(**fields: Any) -> Edge:
    values: dict[str, Any] = {
        "edge_id": "media2",
        "name": "Media 2",
        "kind": EdgeKind.STANDALONE,
        "storage": EdgeStorage.ORIGIN,
        "base_url": "https://media2.example.test",
    }
    values.update(fields)
    return Edge.objects.create(**values)


def test_the_first_migration_made_the_local_edge() -> None:
    local = services.local_edge()
    assert local is not None
    assert (local.kind, local.storage, local.base_url, local.enabled) == ("local", "disk", "", True)


def test_secrets_are_kept_as_hashes_and_checked_in_constant_time() -> None:
    secret = services.new_secret()
    assert services.SECRET_PATTERN.fullmatch(secret)
    stored = services.hash_secret(secret)
    assert stored != secret
    assert services.secret_matches(secret, stored)
    assert not services.secret_matches(secret[:-1] + ("A" if secret[-1] != "A" else "B"), stored)
    assert not services.secret_matches("short", stored)
    assert not services.secret_matches(secret, "")


@pytest.mark.parametrize(
    ("header", "found"),
    [
        ("Bearer {s}", True),
        ("bearer {s}", True),
        ("Basic {s}", False),
        ("Bearer", False),
        ("Bearer {s}x", False),
        ("", False),
    ],
)
def test_bearer_parsing(header: str, found: bool) -> None:
    secret = services.new_secret()
    assert (services.bearer(header.format(s=secret)) == secret) is found


def test_the_auth_mirror_holds_enabled_standalone_edges_only() -> None:
    on = standalone(edge_id="on", secret_hash="a" * 64)
    standalone(edge_id="off", secret_hash="b" * 64, enabled=False)
    standalone(edge_id="nosecret")
    assert services.rebuild_auth_mirror() == 1
    assert state_redis().hgetall(services.AUTH_KEY) == {b"on": b"a" * 64}
    secret = services.rotate_secret(on)
    services.rebuild_auth_mirror()
    stored = cast("bytes | None", state_redis().hget(services.AUTH_KEY, "on"))
    assert stored == services.hash_secret(secret).encode()


def test_only_standalone_edges_rotate_secrets() -> None:
    local = services.local_edge()
    assert local is not None
    with pytest.raises(ValueError, match="standalone"):
        services.rotate_secret(local)


def test_reports_are_cleaned_and_the_egress_rate_comes_from_counters() -> None:
    edge = standalone()
    report = services.record_report(
        edge,
        {
            "kids": ["k1", "k0", "bad kid", 7],
            "connections": {"active": 12, "reading": -1, "requests": "many"},
            "tx_bytes": 1_000_000,
            "version": "nginx/1.30.5 njs/1.0.1",
            "origin_url": "https://bucket.example.test/renditions",
            "secret": "never stored",
        },
        now=NOW,
    )
    assert report["kids"] == ["k0", "k1"]
    assert report["connections"] == {"active": 12}
    assert report["egress_bps"] is None
    assert "secret" not in report
    report = services.record_report(edge, {"kids": ["k1"], "tx_bytes": 3_500_000}, now=NOW + 10)
    assert report["egress_bps"] == pytest.approx(2_000_000.0)
    # A restart resets the counter: no negative rate.
    report = services.record_report(edge, {"kids": ["k1"], "tx_bytes": 10}, now=NOW + 20)
    assert report["egress_bps"] is None
    edge.refresh_from_db()
    assert edge.last_seen_at == datetime.fromtimestamp(NOW + 20, tz=UTC)


def test_status_follows_reports_and_keys(media_keys: tokens.KeySet) -> None:
    edge = standalone()
    kid = media_keys.current.kid
    assert services.status_of(edge, now=NOW) == "never_seen"
    services.record_report(edge, {"kids": [kid]}, now=NOW)
    assert services.status_of(edge, now=NOW + 5) == "healthy"
    assert services.status_of(edge, now=NOW + 5, signing_kid="k9") == "keys_outdated"
    assert services.status_of(edge, now=NOW + 90) == "stale"
    assert services.status_of(edge, now=NOW + 3600) == "down"
    edge.draining = True
    assert services.status_of(edge, now=NOW + 5) == "draining"
    edge.enabled = False
    assert services.status_of(edge, now=NOW + 5) == "disabled"


def test_the_snapshot_is_cached_and_forgotten_on_change() -> None:
    canary = standalone(audience=Audience.CANARY)
    services.record_report(canary, {"kids": ["k1"], "tx_bytes": 0}, now=time.time())
    services.reset_memo()
    cache_redis().delete(services.SNAPSHOT_KEY)
    views = {view.edge_id: view for view in services.snapshot()}
    assert set(views) == {"local", "media2"}
    assert views["media2"].canary
    assert views["media2"].healthy
    assert cache_redis().get(services.SNAPSHOT_KEY) is not None
    # Within the memo, no Redis or Postgres read.
    assert services.snapshot() is services.snapshot()


def test_a_stale_snapshot_row_is_unhealthy(settings: Any) -> None:
    row = services._row(standalone(), [])
    row["seen"] = NOW
    fresh = services._view(row, now=NOW + 10, stale_s=60)
    late = services._view(row, now=NOW + 61, stale_s=60)
    assert fresh.healthy
    assert not late.healthy


def test_the_local_probe_reads_health_and_connections(settings: Any) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/healthz":
            return httpx.Response(200, text="ok\n")
        return httpx.Response(
            200,
            text="Active connections: 4 \nserver accepts handled requests\n 9 9 30 \n"
            "Reading: 0 Writing: 1 Waiting: 3 \n",
        )

    with httpx.Client(transport=httpx.MockTransport(answer)) as client:
        report = services.probe_local(client, now=NOW)
    assert report["ok"] is True
    assert report["connections"]["active"] == 4
    local = services.local_edge()
    assert local is not None
    assert local.last_seen_at is not None

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with httpx.Client(transport=httpx.MockTransport(down)) as client:
        report = services.probe_local(client, now=NOW + 1)
    assert report["ok"] is False
    assert report["error"] == "ConnectError"


def test_what_a_standalone_edge_is_sent(media_keys: tokens.KeySet) -> None:
    edge = standalone()
    answer = services.poll_answer(edge)
    assert answer["keys"]["current"]["kid"] == media_keys.current.kid
    parsed = tokens.parse_keyset(answer["keys"])
    assert parsed.current.secret == media_keys.current.secret
    assert answer["origin"] is None
    assert answer["cdn_origin_secret"] is None
    set_setting("storage.s3_endpoint", "https://s3.example.test", actor=None)
    set_setting("storage.s3_bucket", "media-bucket", actor=None)
    set_setting("storage.s3_prefix", "renditions", actor=None)
    set_setting("storage.s3_region", "auto", actor=None)
    set_setting("storage.s3_edge_access_key", "EDGE" + "KEYID123", actor=None)
    set_setting("storage.s3_edge_secret_key", "edge-" + "secret-value", actor=None)
    set_setting("cdn.origin_secret", "origin-" + "secret-123", actor=None)
    answer = services.poll_answer(edge)
    assert answer["origin"] == {
        "access_key": "EDGEKEYID123",
        "secret_key": "edge-secret-value",
        "region": "auto",
    }
    assert answer["origin_url"] == "https://s3.example.test/media-bucket/renditions"
    assert answer["cdn_origin_secret"] == "origin-" + "secret-123"
    set_setting("storage.s3_path_style", False, actor=None)
    assert services.origin_url() == "https://media-bucket.s3.example.test/renditions"
    # A disk edge (a mirror) gets no origin key.
    edge.storage = EdgeStorage.DISK
    assert services.poll_answer(edge)["origin"] is None


def test_playback_urls_on_our_edges() -> None:
    local = services.Target(EdgeView("local", "local", "disk", ""), "https://media.x", "asset")
    url = services.media_url(
        local,
        token="tok",  # noqa: S106 (a placeholder token)
        rendition="hls",
        entry="hls/master.m3u8",
        exp=1,
    )
    assert url == "https://media.x/v/tok/hls/master.m3u8"


def test_playback_urls_on_a_bunny_zone() -> None:
    set_setting("cdn.bunny_token_key", "bunny-" + "token-key-1", actor=None)
    target = services.Target(
        EdgeView("bunny", "cdn", "disk", "https://cdn.example.test"),
        "https://cdn.example.test",
        "asset-g2",
    )
    hls = services.media_url(
        target,
        token="ignored",  # noqa: S106 (Bunny's URL doesn't carry our token)
        rendition="hls720",
        entry="hls720/master.m3u8",
        exp=1_800_000_000,
    )
    assert hls.startswith("https://cdn.example.test/bcdn_token=HS256-")
    tail = "&token_path=%2Fasset-g2%2Fhls720%2F&expires=1800000000/asset-g2/hls720/master.m3u8"
    assert tail in hls
    mp4 = services.media_url(
        target,
        token="ignored",  # noqa: S106 (Bunny's URL doesn't carry our token)
        rendition="compat",
        entry="compat.mp4",
        exp=1_800_000_000,
    )
    assert "token_path" not in mp4
    assert mp4.endswith("&expires=1800000000/asset-g2/compat.mp4")


def test_a_rotated_secret_has_its_time() -> None:
    edge = standalone()
    before = timezone.now()
    services.rotate_secret(edge)
    edge.refresh_from_db()
    assert edge.secret_set_at is not None
    assert edge.secret_set_at >= before
