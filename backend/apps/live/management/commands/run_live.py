"""`manage.py run_live`: the live packager service (ADR-0017, `apps.live.packager`).

Loads the enabled, licensed channels from Postgres every few seconds, listens for
`live:wake` on redis-state, and supervises one ffmpeg per channel it decides to run.
SIGTERM and SIGINT stop every ffmpeg cleanly before the process exits.
"""

import signal
import time
from collections.abc import Mapping
from types import FrameType
from typing import Any, cast

import redis
import structlog
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.db.models import Q
from django.utils import timezone

from apps.accounts import crypto
from apps.core.services import get_setting
from apps.core.stores import state_redis
from apps.live import conf, egress, layout
from apps.live.models import ChannelTranscode, LiveChannel
from apps.live.packager import TICK_S, ChannelSpec, Limits, Packager
from apps.media.profiles import default_profiles

logger = structlog.get_logger(__name__)


#: Integration hosts sources may point at (refreshed with every channel load).
ALLOWED_HOSTS: set[str] = set()


def guard(url: str) -> None:
    """Refuse sources that point at our own services or special addresses (G-11)."""
    egress.check_url(url, allowed=ALLOWED_HOSTS)


def load_specs() -> Mapping[str, ChannelSpec]:
    """Enabled channels with a valid licence, keyed by channel key."""
    close_old_connections()
    ALLOWED_HOSTS.clear()
    ALLOWED_HOSTS.update(egress.integration_hosts())
    rows = LiveChannel.objects.filter(enabled=True).filter(
        Q(license_expires_at__isnull=True) | Q(license_expires_at__gt=timezone.now())
    )
    specs: dict[str, ChannelSpec] = {}
    for channel in rows.only("id", "source_encrypted", "transcode", "catchup_days", "always_on"):
        try:
            url = crypto.decrypt(channel.source_encrypted)
        except crypto.DecryptionError:
            logger.warning("live.source_unreadable", channel=channel.storage_key[:12])
            continue
        specs[channel.storage_key] = ChannelSpec(
            key=channel.storage_key,
            source_url=url,
            transcode=channel.transcode == ChannelTranscode.H264,
            catchup_days=channel.catchup_days,
            always_on=channel.always_on,
        )
    return specs


def load_limits() -> Limits:
    return Limits(
        idle_stop_s=float(cast("int", get_setting("live.idle_stop_s"))),
        max_running=int(cast("int", get_setting("live.max_running_channels"))),
        max_transcode=int(cast("int", get_setting("live.realtime_transcode_max"))),
        archive_max_bytes=conf.archive_max_bytes(),
    )


class Command(BaseCommand):
    help = "Run the live packager: one ffmpeg per watched (or recording) channel."

    def handle(self, *args: Any, **options: Any) -> None:
        stopping = False

        def stop(signum: int, frame: FrameType | None) -> None:
            nonlocal stopping
            stopping = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        root = conf.live_root()
        root.mkdir(parents=True, exist_ok=True)
        client = state_redis()
        packager = Packager(
            root=root,
            state=client,
            profile=default_profiles().live,
            specs=load_specs,
            limits=load_limits,
            ffmpeg=conf.ffmpeg_binary(),
            guard=guard,
        )
        packager.clean_live_dirs()
        pubsub = client.pubsub(ignore_subscribe_messages=True)
        logger.info("live.packager_started", root=str(root))
        try:
            while not stopping:
                started = time.monotonic()
                try:
                    if not pubsub.subscribed:
                        pubsub.subscribe(layout.WAKE_CHANNEL)
                    message = pubsub.get_message(timeout=0.05)
                    while message is not None:
                        data = message.get("data")
                        if isinstance(data, bytes):
                            packager.wake(data.decode("ascii", "replace"))
                        message = pubsub.get_message(timeout=0)
                except redis.RedisError:
                    logger.warning("live.wake_unavailable")
                    pubsub = client.pubsub(ignore_subscribe_messages=True)
                packager.tick()
                time.sleep(max(0.0, TICK_S - (time.monotonic() - started)))
        finally:
            packager.shutdown()
            pubsub.close()
            logger.info("live.packager_stopped")
