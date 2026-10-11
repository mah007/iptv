#!/usr/bin/env python3
"""Reference for the edge's S3 origin mode (ADR-0022), standard library only.

Two pure functions the edge's njs (`nginx/njs/s3.js`) and the backend
(`apps.media.s3`, `apps.media.publisher`) must agree with, checked through
`tests/sigv4_vectors.json`:

- `presign()`: the query string of an AWS Signature Version 4 presigned GET (the edge
  signs every origin read, slice by slice, with the edges' read-only key);
- `map_object()`: the bucket object a verified token's path names. Object storage has no
  links, so a capped presentation's rungs live once, under `hls/v<n>-h<box>/`, and are
  reached only when their height fits the ceiling (`hls720/v1-h720/x` → `hls/v1-h720/x`).

    python3 streaming/tools/s3_presign.py vectors > streaming/tests/sigv4_vectors.json
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import sys
from urllib.parse import quote

UNSIGNED = "UNSIGNED-PAYLOAD"
SHARED = ("subs", "thumbs")
CAPPED = re.compile(r"hls([0-9]{3,4})")
RUNG = re.compile(r"v[0-9]+-h([0-9]{1,4})")
AUDIO = re.compile(r"a[0-9]+")


def uri_encode(path: str) -> str:
    return "/".join(quote(segment, safe="-_.~") for segment in path.split("/"))


def _query(params: dict[str, str]) -> str:
    return "&".join(
        f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}" for k, v in sorted(params.items())
    )


def _hmac(key: bytes, text: str) -> bytes:
    return hmac.new(key, text.encode(), hashlib.sha256).digest()


def presign(
    *, host: str, path: str, access_key: str, secret_key: str, region: str, amz_date: str,
    expires: int, method: str = "GET",
) -> str:  # fmt: skip
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
    to_sign = "\n".join(
        ["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()]
    )
    key = _hmac(f"AWS4{secret_key}".encode(), date)
    for part in (region, "s3", "aws4_request"):
        key = _hmac(key, part)
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    return f"{_query(params)}&X-Amz-Signature={signature}"


def map_object(title: str, rendition: str, tail: str) -> str | None:
    """The object (relative to the origin prefix) for a token's title, rendition and an
    in-scope tail; None when the path reaches past the token's ceiling."""
    if "/" not in tail:
        return f"{title}/{tail}"
    rest = tail[len(rendition) + 1 :]
    first, _, after = rest.partition("/")
    if first in SHARED and after:
        return f"{title}/{first}/{after}"
    if rendition == "hls":
        return f"{title}/hls/{rest}"
    capped = CAPPED.fullmatch(rendition)
    if capped is None:
        return f"{title}/{tail}"
    ceiling = int(capped.group(1))
    if rest == "master.m3u8":
        return f"{title}/{rendition}/master.m3u8"
    if rendition == "hls2160" and first == "uhd" and after:
        return f"{title}/hls2160/uhd/{after}"
    if AUDIO.fullmatch(first) and after:
        return f"{title}/hls/{first}/{after}"
    rung = RUNG.fullmatch(first)
    if rung is not None and after and int(rung.group(1)) <= ceiling:
        return f"{title}/hls/{first}/{after}"
    return None


# Public test credentials: AWS's documented example key pair (not a real account),
# built from parts so no scanner mistakes the file for a leak.
EXAMPLE_ACCESS = "AKIA" + "IOSFODNN7" + "EXAMPLE"
EXAMPLE_SECRET = "wJalrXUtnFEMI/K7MDENG/" + "bPxRfiCY" + "EXAMPLEKEY"


def vectors() -> dict[str, object]:
    presigns = [
        # AWS's own example (S3 docs, "Authenticating Requests: Using Query Parameters"):
        # its expected signature is fixed below, independent of this implementation.
        {
            "host": "examplebucket.s3.amazonaws.com",
            "path": "/test.txt",
            "region": "us-east-1",
            "amz_date": "20130524T000000Z",
            "expires": 86400,
            "expect_signature": "aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404",
        },
        {
            "host": "seaweedfs:8333",
            "path": "/media/renditions/0192f3a4b5c67d8e9f00112233445566-g3/hls/v1-h720/seg_00001.m4s",
            "region": "us-east-1",
            "amz_date": "20261011T120000Z",
            "expires": 900,
        },
        {
            "host": "account.r2.cloudflarestorage.com",
            "path": "/smart-iptv/renditions/edgeprobe/probe.txt",
            "region": "auto",
            "amz_date": "20261231T235959Z",
            "expires": 60,
        },
        {
            "host": "s3.us-west-004.backblazeb2.com",
            "path": "/bucket/a b/~tilde_dash-dot.mp4",
            "region": "us-west-004",
            "amz_date": "20270101T000000Z",
            "expires": 900,
        },
    ]
    for case in presigns:
        query = presign(
            host=str(case["host"]),
            path=str(case["path"]),
            access_key=EXAMPLE_ACCESS,
            secret_key=EXAMPLE_SECRET,
            region=str(case["region"]),
            amz_date=str(case["amz_date"]),
            expires=int(str(case["expires"])),
        )
        case["query"] = query
        signature = query.rsplit("=", 1)[1]
        if "expect_signature" in case and case["expect_signature"] != signature:
            raise SystemExit("the reference disagrees with AWS's published example")
    title = "0192f3a4b5c67d8e9f00112233445566-g3"
    maps = [
        ("compat", "compat.mp4"),
        ("source", "source.mkv"),
        ("compat", "compat/thumbs/thumbs.vtt"),
        ("compat", "compat/subs/3.eng.vtt"),
        ("hls", "hls/master.m3u8"),
        ("hls", "hls/v0-h1080/seg_00001.m4s"),
        ("hls", "hls/a0/index.m3u8"),
        ("hls", "hls/subs/x1.ara.m3u8"),
        ("hls720", "hls720/master.m3u8"),
        ("hls720", "hls720/v1-h720/index.m3u8"),
        ("hls720", "hls720/v2-h540/init.mp4"),
        ("hls720", "hls720/v0-h1080/seg_00001.m4s"),
        ("hls720", "hls720/v0/seg_00001.m4s"),
        ("hls720", "hls720/a1/seg_00002.m4s"),
        ("hls720", "hls720/thumbs/sprite_0.jpg"),
        ("hls480", "hls480/v3-h360/index.m3u8"),
        ("hls480", "hls480/v2-h540/index.m3u8"),
        ("hls2160", "hls2160/v0-h1080/seg_00001.m4s"),
        ("hls2160", "hls2160/uhd/init.mp4"),
        ("hls720", "hls720/uhd/init.mp4"),
        ("hls720", "hls720/other/file.ts"),
        ("probe", "probe.txt"),
    ]
    return {
        "access_key": EXAMPLE_ACCESS,
        "secret_key": EXAMPLE_SECRET,
        "presign": presigns,
        "map": [
            {"title": title, "rendition": r, "tail": t, "object": map_object(title, r, t)}
            for r, t in maps
        ],
    }


def main(argv: list[str]) -> int:
    if argv[1:] == ["vectors"]:
        json.dump(vectors(), sys.stdout, indent=1)
        sys.stdout.write("\n")
        return 0
    sys.stderr.write(__doc__ or "")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
