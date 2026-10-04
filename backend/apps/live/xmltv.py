"""A streaming, defensive XMLTV reader (ADR-0017).

expat, fed chunk by chunk, so a large guide never sits in memory as a tree:
- a DOCTYPE with an internal subset, or any entity declaration, is refused (XMLTV
  needs neither; this closes entity-expansion and external-entity tricks);
- gzip input is recognised by its magic bytes and inflated on the fly;
- the uncompressed size is capped.

Times follow XMLTV: `YYYYMMDDhhmmss +zzzz` (seconds, minutes and the offset are
optional; no offset means UTC). Everything comes out in UTC.
"""

import re
import zlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from xml.parsers import expat

_TIME = re.compile(
    r"(?P<y>\d{4})(?P<mo>\d{2})(?P<d>\d{2})(?P<h>\d{2})?(?P<mi>\d{2})?(?P<s>\d{2})?"
    r"\s*(?P<tz>[+-]\d{4}|Z|UTC|GMT)?"
)
_LANG = re.compile(r"[A-Za-z]{2,3}")
_GZIP_MAGIC = b"\x1f\x8b"
MAX_TEXT = 4000


class XmltvError(Exception):
    """The document is not a usable XMLTV guide (the message names why, never the URL)."""


@dataclass(slots=True)
class XmltvChannel:
    xmltv_id: str
    names: dict[str, str] = field(default_factory=dict)
    icon: str = ""


@dataclass(slots=True)
class XmltvProgramme:
    channel: str
    start: datetime
    stop: datetime | None
    titles: dict[str, str] = field(default_factory=dict)
    descriptions: dict[str, str] = field(default_factory=dict)
    category: str = ""


def parse_time(value: str) -> datetime | None:
    match = _TIME.fullmatch((value or "").strip())
    if match is None:
        return None
    offset = match["tz"]
    if offset in (None, "Z", "UTC", "GMT"):
        zone: timezone = UTC
    else:
        sign = 1 if offset[0] == "+" else -1
        hours, minutes = int(offset[1:3]), int(offset[3:5])
        if hours > 14 or minutes > 59:
            return None
        zone = timezone(sign * timedelta(hours=hours, minutes=minutes))
    try:
        moment = datetime(
            int(match["y"]),
            int(match["mo"]),
            int(match["d"]),
            int(match["h"] or 0),
            int(match["mi"] or 0),
            int(match["s"] or 0),
            tzinfo=zone,
        )
    except ValueError:
        return None
    return moment.astimezone(UTC)


def language(value: str | None) -> str:
    """An ISO 639 code from a `lang` attribute ("ar-SA" -> "ar"), else ""."""
    head = (value or "").strip().split("-", 1)[0].split("_", 1)[0].lower()
    return head if _LANG.fullmatch(head) else ""


def _clean(text: str) -> str:
    return " ".join(text.split())[:MAX_TEXT]


class _Reader:
    """expat handlers: build channels and programmes, hand each to the callbacks."""

    def __init__(
        self,
        on_channel: Callable[[XmltvChannel], None],
        on_programme: Callable[[XmltvProgramme], None],
    ) -> None:
        self.on_channel = on_channel
        self.on_programme = on_programme
        self.depth = 0
        self.root_seen = False
        self.channel: XmltvChannel | None = None
        self.programme: XmltvProgramme | None = None
        self.text: list[str] | None = None
        self.text_lang = ""
        self.skipped = 0

    # Security: refuse what XMLTV never needs.
    def doctype(self, name: str, system_id: Any, public_id: Any, has_internal_subset: int) -> None:
        if has_internal_subset:
            msg = "the document declares a DOCTYPE internal subset"
            raise XmltvError(msg)

    def entity(self, *args: Any) -> None:
        msg = "the document declares entities"
        raise XmltvError(msg)

    def start(self, tag: str, attributes: dict[str, str]) -> None:
        self.depth += 1
        if self.depth == 1:
            if tag != "tv":
                msg = "the root element is not <tv>"
                raise XmltvError(msg)
            self.root_seen = True
            return
        if self.depth == 2:
            if tag == "channel":
                self.channel = XmltvChannel(xmltv_id=(attributes.get("id") or "").strip())
            elif tag == "programme":
                start = parse_time(attributes.get("start", ""))
                stop = parse_time(attributes.get("stop", "")) if attributes.get("stop") else None
                channel = (attributes.get("channel") or "").strip()
                if start is None or not channel:
                    self.skipped += 1
                    self.programme = None
                else:
                    self.programme = XmltvProgramme(channel=channel, start=start, stop=stop)
            return
        if self.depth == 3:
            if self.channel is not None:
                if tag == "display-name":
                    self._begin_text(attributes)
                elif tag == "icon":
                    src = (attributes.get("src") or "").strip()
                    if src.startswith(("http://", "https://")) and not self.channel.icon:
                        self.channel.icon = src[:1024]
            elif self.programme is not None and tag in ("title", "desc", "category"):
                self._begin_text(attributes)

    def _begin_text(self, attributes: dict[str, str]) -> None:
        self.text = []
        self.text_lang = language(attributes.get("lang"))

    def data(self, text: str) -> None:
        if self.text is not None:
            self.text.append(text)

    def end(self, tag: str) -> None:
        if self.depth == 3 and self.text is not None:
            value = _clean("".join(self.text))
            self.text = None
            if value:
                self._store(tag, value)
        elif self.depth == 2:
            if tag == "channel" and self.channel is not None:
                if self.channel.xmltv_id:
                    self.on_channel(self.channel)
                self.channel = None
            elif tag == "programme" and self.programme is not None:
                if self.programme.titles:
                    self.on_programme(self.programme)
                else:
                    self.skipped += 1
                self.programme = None
        self.depth -= 1

    def _store(self, tag: str, value: str) -> None:
        lang = self.text_lang
        if self.channel is not None and tag == "display-name":
            self.channel.names.setdefault(lang, value[:255])
        elif self.programme is not None:
            if tag == "title":
                self.programme.titles.setdefault(lang, value[:500])
            elif tag == "desc":
                self.programme.descriptions.setdefault(lang, value)
            elif tag == "category" and not self.programme.category:
                self.programme.category = value[:100]


def read_xmltv(
    chunks: Iterable[bytes],
    *,
    max_bytes: int,
    on_channel: Callable[[XmltvChannel], None],
    on_programme: Callable[[XmltvProgramme], None],
) -> int:
    """Feed `chunks` (raw or gzip) through the reader; returns skipped programmes.

    Raises XmltvError for a malformed, oversized or unsafe document.
    """
    reader = _Reader(on_channel, on_programme)
    parser = expat.ParserCreate()
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    parser.StartDoctypeDeclHandler = reader.doctype
    parser.EntityDeclHandler = reader.entity
    parser.UnparsedEntityDeclHandler = reader.entity
    parser.StartElementHandler = reader.start
    parser.EndElementHandler = reader.end
    parser.CharacterDataHandler = reader.data
    inflater: Any = None
    total = 0
    first = True
    try:
        for chunk in chunks:
            if not chunk:
                continue
            if first:
                first = False
                if chunk[:2] == _GZIP_MAGIC:
                    inflater = zlib.decompressobj(wbits=31)
            data = inflater.decompress(chunk, max_bytes + 1 - total) if inflater else chunk
            total += len(data)
            if total > max_bytes or (inflater is not None and inflater.unconsumed_tail):
                msg = f"the document is larger than {max_bytes // (1024 * 1024)} MB"
                raise XmltvError(msg)
            parser.Parse(data, False)
        if inflater is not None:
            tail = inflater.flush()
            total += len(tail)
            if total > max_bytes:
                msg = f"the document is larger than {max_bytes // (1024 * 1024)} MB"
                raise XmltvError(msg)
            parser.Parse(tail, False)
        parser.Parse(b"", True)
    except expat.ExpatError as exc:
        msg = f"not well-formed XML ({expat.ErrorString(exc.code)}, line {exc.lineno})"
        raise XmltvError(msg) from None
    except zlib.error:
        msg = "the gzip data is damaged"
        raise XmltvError(msg) from None
    if not reader.root_seen:
        msg = "the document is empty"
        raise XmltvError(msg)
    return reader.skipped
