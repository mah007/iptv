"""EPG import: XMLTV sources into `EpgChannel` and `EpgProgram` (SPEC §6 live, ADR-0017).

`import_source(source)` runs on the worker (it has internet access):

1. Read the document: the source URL with a conditional GET (ETag, Last-Modified),
   or the uploaded file. A 304 counts as a successful run with nothing to do.
2. Keep every `<channel>`, but programmes only for XMLTV ids a live channel maps
   (a mapping change queues a refresh), within `[now - live.epg_past_days,
   now + live.epg_future_days]`. A programme without `stop` ends when the next one
   of its channel starts.
3. Create the monthly partitions the programmes need (their own short transaction:
   creating a partition locks the parent), then, in one transaction, replace each
   mapped channel's programmes from its first imported start onwards.

Errors are stored redacted on the source (`last_error`); a failed run keeps the
previous guide. Every import retires the Xtream response cache.
"""

import gzip
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, cast

import httpx
import structlog
from celery.schedules import crontab
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts import crypto
from apps.core.partitions import ensure_monthly, month_start
from apps.core.redaction import redact_text
from apps.core.services import get_setting
from apps.live import egress, sources
from apps.live.models import EpgChannel, EpgProgram, EpgSource, EpgSourceKind, LiveChannel
from apps.live.xmltv import XmltvChannel, XmltvError, XmltvProgramme, read_xmltv

logger = structlog.get_logger(__name__)

PROGRAM_TABLE = "live_epgprogram"
FETCH_TIMEOUT_S = 60.0
CHUNK_BYTES = 256 * 1024
BATCH = 2000
USER_AGENT = "SmartIPTV-EPG/1"


class EpgFetchError(Exception):
    """The source could not be read (the message never holds the URL)."""


@dataclass(slots=True)
class ImportResult:
    channels: int = 0
    programmes: int = 0
    mapped: int = 0
    skipped: int = 0
    not_modified: bool = False
    window: tuple[str, str] | None = None
    unknown_channels: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "channels": self.channels,
            "programmes": self.programmes,
            "mapped": self.mapped,
            "skipped": self.skipped,
            "not_modified": self.not_modified,
        }


# --- Validation ----------------------------------------------------------------------------


def validate_cron(expression: str) -> str:
    """Five cron fields (minute hour day month weekday), checked with celery's parser."""
    text = " ".join((expression or "").split())
    fields = text.split(" ")
    if len(fields) != 5:
        raise ValidationError("Enter five cron fields.", code="invalid_cron")
    try:
        crontab(*fields)
    except (ValueError, TypeError):
        raise ValidationError("Enter five cron fields.", code="invalid_cron") from None
    return text


def is_due(source: EpgSource, now: datetime | None = None) -> bool:
    """Whether the source's cron schedule has a run since its last run."""
    if not source.enabled:
        return False
    if source.last_run_at is None:
        return True
    moment = now or timezone.now()
    try:
        schedule = crontab(*source.refresh_cron.split(" "), nowfun=lambda: moment)
    except (ValueError, TypeError):
        return False
    return bool(schedule.is_due(source.last_run_at).is_due)


# --- Reading -------------------------------------------------------------------------------


def _upload_chunks(source: EpgSource) -> Iterator[bytes]:
    data = bytes(source.upload or b"")
    if not data:
        msg = "no file was uploaded"
        raise EpgFetchError(msg)
    yield data  # gzip: read_xmltv inflates it


@dataclass(slots=True)
class _Fetched:
    chunks: Iterator[bytes] | None  # None: not modified
    etag: str = ""
    last_modified: str = ""


def _fetch(source: EpgSource, client: httpx.Client) -> _Fetched:
    try:
        url = sources.validate_http_url(crypto.decrypt(source.url_encrypted))
    except (crypto.DecryptionError, ValidationError):
        msg = "the source URL is missing or invalid"
        raise EpgFetchError(msg) from None
    headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}
    if source.etag:
        headers["If-None-Match"] = source.etag
    if source.last_modified:
        headers["If-Modified-Since"] = source.last_modified
    redactor = sources.Redactor(url)
    try:
        response = client.send(client.build_request("GET", url, headers=headers), stream=True)
    except egress.UnsafeDestination as refused:
        msg = f"the URL points where guides are never fetched from ({refused.reason})"
        raise EpgFetchError(msg) from None
    except httpx.HTTPError as exc:
        msg = redactor(f"the request failed ({type(exc).__name__})")
        raise EpgFetchError(msg) from None
    if response.status_code == 304:
        response.close()
        return _Fetched(None, source.etag, source.last_modified)
    if response.status_code != 200:
        response.close()
        msg = f"the server answered HTTP {response.status_code}"
        raise EpgFetchError(msg)

    def chunks() -> Iterator[bytes]:
        try:
            yield from response.iter_bytes(CHUNK_BYTES)
        except httpx.HTTPError as exc:
            msg = redactor(f"the download failed ({type(exc).__name__})")
            raise EpgFetchError(msg) from None
        finally:
            response.close()

    return _Fetched(
        chunks(),
        response.headers.get("ETag", "")[:255],
        response.headers.get("Last-Modified", "")[:64],
    )


# --- Importing -----------------------------------------------------------------------------


def mapped_ids(source: EpgSource) -> set[str]:
    """XMLTV ids some live channel takes its guide from (this source, or any)."""
    return set(
        LiveChannel.objects.exclude(epg_channel_id="")
        .filter(Q(epg_source__isnull=True) | Q(epg_source=source))
        .values_list("epg_channel_id", flat=True)
    )


def _title(texts: dict[str, str]) -> tuple[str, str, str]:
    """(title, title_ar, lang of title): Arabic goes to the `_ar` field."""
    arabic = texts.get("ar", "")
    others = [(lang, text) for lang, text in texts.items() if lang != "ar"]
    preferred = next(((lang, text) for lang, text in others if lang == "en"), None)
    lang, text = preferred or (others[0] if others else ("ar", arabic))
    return text, arabic if lang != "ar" else "", lang


def _programme_rows(
    items: list[XmltvProgramme], window: tuple[datetime, datetime]
) -> list[XmltvProgramme]:
    """Stops filled from the next start, overlaps and out-of-window ones dropped."""
    items.sort(key=lambda item: item.start)
    kept: list[XmltvProgramme] = []
    for index, item in enumerate(items):
        following = items[index + 1].start if index + 1 < len(items) else None
        stop = item.stop or following
        if stop is None or stop <= item.start:
            continue
        if stop <= window[0] or item.start >= window[1]:
            continue
        if kept and item.start < (kept[-1].stop or kept[-1].start):
            continue  # overlaps the previous one: XMLTV feeds sometimes repeat a slot
        item.stop = stop
        kept.append(item)
    return kept


def _write(
    source: EpgSource,
    channels: dict[str, XmltvChannel],
    programmes: dict[str, list[XmltvProgramme]],
) -> tuple[int, int]:
    """Upsert channels and replace programmes; returns (channels, programmes)."""
    existing = {channel.xmltv_id: channel for channel in EpgChannel.objects.filter(source=source)}
    created, changed = [], []
    for xmltv_id, item in channels.items():
        row = existing.get(xmltv_id)
        if row is None:
            created.append(
                EpgChannel(source=source, xmltv_id=xmltv_id, names=item.names, icon_url=item.icon)
            )
        elif row.names != item.names or row.icon_url != item.icon:
            row.names, row.icon_url = item.names, item.icon
            changed.append(row)
    EpgChannel.objects.bulk_create(created, batch_size=BATCH)
    EpgChannel.objects.bulk_update(changed, ["names", "icon_url", "updated_at"], batch_size=BATCH)
    gone = [row.pk for xmltv_id, row in existing.items() if xmltv_id not in channels]
    if gone:
        EpgChannel.objects.filter(pk__in=gone).delete()  # programmes cascade in the database
    ids = {row.xmltv_id: row.pk for row in [*existing.values(), *created] if row.pk not in gone}
    replace = [(ids[xmltv_id], rows[0].start) for xmltv_id, rows in programmes.items() if rows]
    if replace:
        values = ", ".join(["(%s::uuid, %s::timestamptz)"] * len(replace))
        params: list[Any] = [value for pair in replace for value in pair]
        with connection.cursor() as cursor:
            cursor.execute(
                f"DELETE FROM {PROGRAM_TABLE} p USING (VALUES {values}) AS v(channel_id, since) "  # noqa: S608 (placeholders only)
                "WHERE p.channel_id = v.channel_id AND p.start >= v.since",
                params,
            )
    rows = []
    for xmltv_id, items in programmes.items():
        channel_id = ids[xmltv_id]
        for programme in items:
            title, title_ar, lang = _title(programme.titles)
            description, description_ar, _ = (
                _title(programme.descriptions) if programme.descriptions else ("", "", "")
            )
            rows.append(
                EpgProgram(
                    channel_id=channel_id,
                    start=programme.start,
                    stop=cast("datetime", programme.stop),
                    title=title or title_ar,
                    title_ar=title_ar,
                    description=description,
                    description_ar=description_ar,
                    category=programme.category,
                    lang=lang,
                )
            )
    EpgProgram.objects.bulk_create(rows, batch_size=BATCH)
    return len(channels), len(rows)


def import_document(
    source: EpgSource, chunks: Iterator[bytes], *, now: datetime | None = None
) -> ImportResult:
    """Read one XMLTV document into the guide (see the module docstring)."""
    moment = now or timezone.now()
    window = (
        moment - timedelta(days=int(get_setting("live.epg_past_days"))),
        moment + timedelta(days=int(get_setting("live.epg_future_days"))),
    )
    wanted = mapped_ids(source)
    channels: dict[str, XmltvChannel] = {}
    collected: defaultdict[str, list[XmltvProgramme]] = defaultdict(list)

    def on_channel(item: XmltvChannel) -> None:
        if len(item.xmltv_id) <= 255 and item.xmltv_id not in channels:
            channels[item.xmltv_id] = item

    def on_programme(item: XmltvProgramme) -> None:
        if item.channel in wanted:
            collected[item.channel].append(item)

    max_bytes = int(get_setting("live.epg_max_document_mb")) * 1024 * 1024
    skipped = read_xmltv(
        chunks, max_bytes=max_bytes, on_channel=on_channel, on_programme=on_programme
    )
    programmes = {
        xmltv_id: _programme_rows(items, window)
        for xmltv_id, items in collected.items()
        if xmltv_id in channels
    }
    result = ImportResult(skipped=skipped, mapped=len(programmes))
    starts = [item.start for items in programmes.values() for item in items]
    if starts:
        first, last = month_start(min(starts)), month_start(max(starts))
        months = (last.year - first.year) * 12 + last.month - first.month + 1
        ensure_monthly(PROGRAM_TABLE, first, months)
    with transaction.atomic():
        result.channels, result.programmes = _write(source, channels, programmes)
    result.unknown_channels = sorted(set(collected) - set(channels))[:20]
    return result


def _fail(source: EpgSource, message: str, moment: datetime) -> None:
    source.last_run_at = moment
    source.last_error = redact_text(message)[:1000]
    source.save(update_fields=["last_run_at", "last_error", "updated_at"])
    logger.warning("epg.import_failed", source=str(source.pk), error=source.last_error)


def import_source(source: EpgSource, *, client: httpx.Client | None = None) -> ImportResult | None:
    """Fetch and import one source, recording the outcome on it. None when it failed."""
    from apps.xtream_api import cache  # noqa: PLC0415 (the Xtream app imports live)

    moment = timezone.now()
    own_client = client is None
    http = client or egress.guarded_client(
        allowed=egress.integration_hosts(),
        timeout=FETCH_TIMEOUT_S,
        follow_redirects=True,
        max_redirects=5,
    )
    try:
        if source.kind == EpgSourceKind.UPLOAD:
            fetched = _Fetched(_upload_chunks(source))
        else:
            fetched = _fetch(source, http)
        if fetched.chunks is None:
            result = ImportResult(not_modified=True)
        else:
            result = import_document(source, fetched.chunks, now=moment)
    except (EpgFetchError, XmltvError) as exc:
        _fail(source, str(exc), moment)
        return None
    finally:
        if own_client:
            http.close()
    source.last_run_at = moment
    source.last_ok_at = moment
    source.last_error = ""
    if source.kind == EpgSourceKind.URL:
        source.etag, source.last_modified = fetched.etag, fetched.last_modified
    if not result.not_modified:
        source.stats = {**result.as_dict(), "imported_at": moment.isoformat()}
    source.save(
        update_fields=[
            "last_run_at", "last_ok_at", "last_error", "etag", "last_modified", "stats",
            "updated_at",
        ]
    )  # fmt: skip
    if not result.not_modified:
        cache.invalidate_on_commit()
    logger.info("epg.imported", source=str(source.pk), **result.as_dict())
    return result


def compress_upload(data: bytes) -> bytes:
    """Store uploads gzip-compressed (they stay on the row: the web has no media volume)."""
    return data if data[:2] == b"\x1f\x8b" else gzip.compress(data, compresslevel=6)
