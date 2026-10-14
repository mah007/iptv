"""Bunny CDN token authentication (ADR-0022, docs/runbooks/cdn-bunny.md).

Verified against vectors only, no live pull zone:

- `data/bunny_vectors.json`: URLs of our shapes (a presentation's folder, a file, an
  address bound, IPv6 cut to its /64) produced by Bunny's reference implementation
  (BunnyWay/BunnyCDN.TokenAuthentication, python3/token.py at the commit named in the
  file), run once as an oracle on our inputs; its code is not copied here.
- Bunny's own published vectors (that repository's e2e/vectors.json, 15 cases with
  countries, limits, token_path and addresses) matched `bunny.token` when checked on
  2026-10-14; they are not copied either (the repository has no licence).
"""

import json
from pathlib import Path
from typing import Any

import pytest

from apps.edges import bunny

VECTORS: list[dict[str, Any]] = json.loads(
    (Path(__file__).parent / "data" / "bunny_vectors.json").read_text()
)["vectors"]


@pytest.mark.parametrize("vector", VECTORS, ids=[v["name"] for v in VECTORS])
def test_signed_urls_match_the_reference(vector: dict[str, Any]) -> None:
    url = bunny.signed_url(
        vector["base"],
        vector["path"],
        key=vector["key"],
        expires=vector["expires"],
        directory=vector["directory"],
        ip=vector["ip"],
    )
    assert url == vector["url"]


def test_one_token_covers_a_presentation_folder() -> None:
    folder = [v for v in VECTORS if v["directory"] and v["directory"].endswith("/hls720/")]
    tokens = {v["url"].split("&", 1)[0] for v in folder if v["expires"] == folder[0]["expires"]}
    assert len(tokens) == 1
    master = bunny.signed_url(
        "https://cdn.example.test", "/t-g1/hls/master.m3u8",
        key="k", expires=1_900_000_000, directory="/t-g1/hls/",
    )  # fmt: skip
    segment = bunny.signed_url(
        "https://cdn.example.test", "/t-g1/hls/v1-h720/seg_00001.m4s",
        key="k", expires=1_900_000_000, directory="/t-g1/hls/",
    )  # fmt: skip
    assert master.split("&", 1)[0] == segment.split("&", 1)[0]


def test_an_address_flags_the_token() -> None:
    plain = bunny.token("k", signature_path="/a/", expires=1, params={})
    bound = bunny.token("k", signature_path="/a/", expires=1, params={}, ip="192.0.2.1")
    assert plain.startswith("HS256-")
    assert not plain.startswith("HS256-1-")
    assert bound.startswith("HS256-1-")


def test_ipv6_is_signed_by_its_64() -> None:
    one = bunny.token("k", signature_path="/a/", expires=1, params={}, ip="2001:db8:1:2::1")
    other = bunny.token("k", signature_path="/a/", expires=1, params={}, ip="2001:db8:1:2:ffff::9")
    elsewhere = bunny.token("k", signature_path="/a/", expires=1, params={}, ip="2001:db8:1:3::1")
    assert one == other
    assert one != elsewhere


def test_an_ipv4_mapped_address_signs_as_ipv4() -> None:
    """Our choice, not the reference's: a viewer seen as ::ffff:a.b.c.d (a dual-stack
    socket) reaches Bunny over IPv4, so the token binds that address."""
    mapped = bunny.token("k", signature_path="/a/", expires=1, params={}, ip="::ffff:192.0.2.1")
    plain = bunny.token("k", signature_path="/a/", expires=1, params={}, ip="192.0.2.1")
    assert mapped == plain
