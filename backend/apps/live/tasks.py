"""Celery tasks of live TV (ADR-0017), on the worker's default queue.

The worker has internet access, FFmpeg and the media volume; the web process has
none of them, so guide imports, source tests, integration syncs and logo storing run
here. Beat runs the schedule checks, the partition upkeep and the licence job.
"""

import base64
from datetime import timedelta
from typing import Any, Final

import httpx
import orjson
import redis
import structlog
from celery import shared_task
from django.utils import timezone

from apps.accounts import crypto
from apps.core.partitions import add_months, drop_monthly_before, ensure_monthly, month_start
from apps.core.services import get_setting
from apps.core.stores import cache_redis
from apps.live import epg, integrations, probe, services, sources
from apps.live.models import EpgSource, LiveChannel, LiveIntegration
from apps.metadata import images
from apps.metadata.services import image_store
from apps.xtream_api.cache import invalidate_on_commit

logger = structlog.get_logger(__name__)

#: redis-cache: a source test's result, read by the admin while it waits.
PROBE_PREFIX: Final = "live:probe:"
PROBE_TTL_S: Final = 600
PARTITION_MONTHS_AHEAD: Final = 3


def probe_key(request_id: str) -> str:
    return f"{PROBE_PREFIX}{request_id}"


def store_probe(request_id: str, result: dict[str, Any]) -> None:
    try:
        cache_redis().set(probe_key(request_id), orjson.dumps(result), ex=PROBE_TTL_S)
    except redis.RedisError:
        logger.warning("live.probe_result_unstored")


def read_probe(request_id: str) -> dict[str, Any] | None:
    try:
        raw = cache_redis().get(probe_key(request_id))
    except redis.RedisError:
        return None
    return orjson.loads(raw) if isinstance(raw, bytes) else None


@shared_task(name="apps.live.tasks.import_epg_source")
def import_epg_source(source_id: str) -> int:
    source = EpgSource.objects.filter(pk=source_id).first()
    if source is None:
        return 0
    result = epg.import_source(source)
    return result.programmes if result else 0


@shared_task(name="apps.live.tasks.refresh_due_epg_sources")
def refresh_due_epg_sources() -> int:
    """Beat, every 5 minutes: queue the sources whose cron schedule is due."""
    due = [source for source in EpgSource.objects.filter(enabled=True) if epg.is_due(source)]
    for source in due:
        import_epg_source.delay(str(source.pk))
    return len(due)


@shared_task(name="apps.live.tasks.maintain_epg_partitions")
def maintain_epg_partitions() -> dict[str, list[str]]:
    """Daily: create the coming months, drop the months older than the guide keeps
    (at least the longest catch-up window)."""
    now = timezone.now()
    created = ensure_monthly(epg.PROGRAM_TABLE, month_start(now), PARTITION_MONTHS_AHEAD)
    keep_days = max(
        int(get_setting("live.epg_past_days")), int(get_setting("live.catchup_max_days"))
    )
    cutoff = add_months(month_start(now - timedelta(days=keep_days)), 0)
    dropped = drop_monthly_before(epg.PROGRAM_TABLE, cutoff)
    if created or dropped:
        logger.info("live.partitions", created=created, dropped=dropped)
    return {"created": created, "dropped": dropped}


@shared_task(name="apps.live.tasks.enforce_licences")
def enforce_licences() -> int:
    return services.enforce_licences()


@shared_task(name="apps.live.tasks.probe_channel")
def probe_channel(channel_id: str, request_id: str) -> bool:
    """Test a saved channel's source; the result is kept on the channel too."""
    channel = LiveChannel.objects.filter(pk=channel_id).first()
    if channel is None:
        return False
    try:
        url = sources.decrypt(channel.source_encrypted)
    except crypto.DecryptionError:
        store_probe(request_id, {"ok": False, "error": "unreadable_source", "detail": ""})
        return False
    result = probe.probe(url)
    store_probe(request_id, result)
    if result.get("ok"):
        LiveChannel.objects.filter(pk=channel.pk).update(probe=result, updated_at=timezone.now())
        invalidate_on_commit()
    return bool(result.get("ok"))


@shared_task(name="apps.live.tasks.probe_url")
def probe_url(encrypted_url: str, request_id: str) -> bool:
    """Test a URL before it is saved (sent encrypted: task payloads sit in Redis)."""
    try:
        url = sources.decrypt(encrypted_url)
    except crypto.DecryptionError:
        store_probe(request_id, {"ok": False, "error": "unreadable_source", "detail": ""})
        return False
    result = probe.probe(url)
    store_probe(request_id, result)
    return bool(result.get("ok"))


@shared_task(name="apps.live.tasks.sync_integration")
def sync_integration(integration_id: str) -> dict[str, Any]:
    integration = LiveIntegration.objects.filter(pk=integration_id).first()
    if integration is None:
        return {}
    return integrations.sync(integration).as_dict()


def store_logo_bytes(channel: LiveChannel, data: bytes) -> bool:
    try:
        stored = images.store_image(
            data, owner=f"channel/{channel.pk.hex}", kind="logo", store=image_store()
        )
    except (images.ImageError, ValueError) as exc:
        logger.warning("live.logo_rejected", channel=str(channel.pk), error=str(exc)[:120])
        return False
    LiveChannel.objects.filter(pk=channel.pk).update(logo=stored.keys(), updated_at=timezone.now())
    invalidate_on_commit()
    return True


@shared_task(name="apps.live.tasks.store_channel_logo")
def store_channel_logo(channel_id: str, upload_b64: str = "", url: str = "") -> bool:
    """Store an uploaded logo, or one downloaded from an http(s) URL, as WebP and AVIF."""
    channel = LiveChannel.objects.filter(pk=channel_id).first()
    if channel is None:
        return False
    if upload_b64:
        return store_logo_bytes(channel, base64.b64decode(upload_b64))
    if url:
        try:
            with httpx.Client(timeout=20.0, follow_redirects=True) as client:
                data = images.download_image(sources.validate_http_url(url), client=client)
        except Exception:
            logger.warning("live.logo_download_failed", channel=str(channel.pk))
            return False
        return store_logo_bytes(channel, data)
    return False
