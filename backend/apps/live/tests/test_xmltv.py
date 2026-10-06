"""The XMLTV reader: times, languages, safety limits (apps.live.xmltv)."""

import gzip
from datetime import UTC, datetime

import pytest

from apps.live.xmltv import (
    XmltvChannel,
    XmltvError,
    XmltvProgramme,
    language,
    parse_time,
    read_xmltv,
)

GUIDE = b"""<?xml version="1.0" encoding="UTF-8"?>
<tv generator-info-name="test">
  <channel id="one.example">
    <display-name lang="en">One</display-name>
    <display-name lang="ar">\xd9\x88\xd8\xa7\xd8\xad\xd8\xaf</display-name>
    <icon src="https://img.example.com/one.png"/>
  </channel>
  <channel id="">
    <display-name>No id</display-name>
  </channel>
  <programme start="20261004120000 +0300" stop="20261004130000 +0300" channel="one.example">
    <title lang="en">  Morning   news </title>
    <title lang="ar-SA">\xd8\xa3\xd8\xae\xd8\xa8\xd8\xa7\xd8\xb1</title>
    <desc lang="en">Headlines.</desc>
    <category lang="en">News</category>
  </programme>
  <programme start="20261004100000" channel="one.example">
    <title>Late</title>
  </programme>
  <programme start="bad" channel="one.example"><title>Broken</title></programme>
  <programme start="20261004100000 +0000" channel="one.example"></programme>
</tv>
"""


def read(
    document: bytes, max_bytes: int = 10_000_000
) -> tuple[list[XmltvChannel], list[XmltvProgramme], int]:
    channels: list[XmltvChannel] = []
    programmes: list[XmltvProgramme] = []
    skipped = read_xmltv(
        [document[i : i + 50] for i in range(0, len(document), 50)],
        max_bytes=max_bytes,
        on_channel=channels.append,
        on_programme=programmes.append,
    )
    return channels, programmes, skipped


def test_reads_channels_and_programmes_in_utc() -> None:
    channels, programmes, skipped = read(GUIDE)
    assert [channel.xmltv_id for channel in channels] == ["one.example"]
    assert channels[0].names == {"en": "One", "ar": "واحد"}
    assert channels[0].icon == "https://img.example.com/one.png"
    first = programmes[0]
    assert first.start == datetime(2026, 10, 4, 9, 0, tzinfo=UTC)
    assert first.stop == datetime(2026, 10, 4, 10, 0, tzinfo=UTC)
    assert first.titles == {"en": "Morning news", "ar": "أخبار"}
    assert first.descriptions == {"en": "Headlines."}
    assert first.category == "News"
    assert programmes[1].stop is None  # filled by the importer from the next start
    assert skipped == 2  # a bad start, and a programme without a title


def test_gzip_is_recognised() -> None:
    channels, programmes, _ = read(gzip.compress(GUIDE))
    assert len(channels) == 1
    assert len(programmes) == 2


@pytest.mark.parametrize(
    "document",
    [
        b'<?xml version="1.0"?><!DOCTYPE tv [<!ENTITY a "aaaa">]><tv>&a;</tv>',
        b'<?xml version="1.0"?><!DOCTYPE tv SYSTEM "x" [<!ENTITY x SYSTEM "file:///etc/passwd">]><tv/>',
    ],
)
def test_entities_and_internal_subsets_are_refused(document: bytes) -> None:
    with pytest.raises(XmltvError, match=r"DOCTYPE|entities"):
        read(document)


@pytest.mark.parametrize(
    ("document", "message"),
    [
        (b"<tv><channel>", "not well-formed"),
        (b"<guide/>", "root element"),
        (b"", "not well-formed"),
        (b"\x1f\x8b\x08\x00broken", "not well-formed|gzip"),
    ],
)
def test_broken_documents(document: bytes, message: str) -> None:
    with pytest.raises(XmltvError, match=message):
        read(document)


def test_the_size_is_capped_also_after_inflating() -> None:
    big = b"<tv>" + b"<!-- padding -->" * 10_000 + b"</tv>"
    with pytest.raises(XmltvError, match="larger than"):
        read(big, max_bytes=50_000)
    with pytest.raises(XmltvError, match="larger than"):
        read(gzip.compress(big), max_bytes=50_000)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("20261004120000 +0000", datetime(2026, 10, 4, 12, 0, tzinfo=UTC)),
        ("20261004120000 -0130", datetime(2026, 10, 4, 13, 30, tzinfo=UTC)),
        ("202610041200", datetime(2026, 10, 4, 12, 0, tzinfo=UTC)),
        ("20261004", datetime(2026, 10, 4, 0, 0, tzinfo=UTC)),
        ("20261004120000 Z", datetime(2026, 10, 4, 12, 0, tzinfo=UTC)),
        ("20260230120000 +0000", None),
        ("20261004120000 +9900", None),
        ("2026-10-04 12:00", None),
    ],
)
def test_parse_time(text: str, expected: datetime | None) -> None:
    assert parse_time(text) == expected


def test_language_codes() -> None:
    assert language("ar-SA") == "ar"
    assert language("EN") == "en"
    assert language("") == ""
    assert language("english") == ""
