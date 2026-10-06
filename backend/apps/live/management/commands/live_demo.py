"""`manage.py live_demo [--wait S]`: the dev stack's live test channel (DEBUG only).

Creates or updates, idempotently:
- the live group "Showcase" (a live category);
- the channel "Test Pattern", read from the dev MediaMTX (`rtsp://mediamtx:8554/
  test-pattern`, which loops synthetic media from `make sample-media`), with one day
  of catch-up and rights held by the platform itself (synthetic test media);
- the guide source "Demo guide (synthetic)": an uploaded XMLTV, regenerated around
  now on every run (30-minute programmes, English and Arabic titles), imported at once.

`--wait S` then waits up to S seconds until the channel is live and its archive holds
three minutes (what `make compat-live` plays back); on later runs it returns at once,
because the channel keeps recording while the stack runs. Credentials are never printed.
"""

import time
from datetime import UTC, datetime, timedelta
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils import timezone

from apps.catalog.models import Category, CategoryKind
from apps.live import epg, sources, state
from apps.live.models import EpgSource, EpgSourceKind, LiveChannel

GROUP_SLUG = "showcase"
CHANNEL_NAME = "Test Pattern"
CHANNEL_NAME_AR = "نمط الاختبار"
EPG_ID = "test-pattern.smart-iptv"
SOURCE_URL = "rtsp://mediamtx:8554/test-pattern"
GUIDE_NAME = "Demo guide (synthetic)"
ARCHIVE_NEEDED_S = 180
_PROGRAMMES = (
    ("Studio Notes", "ملاحظات الاستوديو", "Short notes from the production team."),
    ("Inside the Studio", "داخل الاستوديو", "How a title reaches your screen."),
    ("Behind the Scenes", "خلف الكواليس", "A look at the platform's own workshop."),
    ("Evening Showcase", "عرض المساء", "A curated selection of synthetic test media."),
)


def _xmltv_time(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y%m%d%H%M%S +0000")


def demo_guide(now: datetime) -> bytes:
    """Programmes every 30 minutes from two days back to three days ahead."""
    start = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(days=2)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<tv generator-info-name="Smart IPTV demo">',
        f'  <channel id="{EPG_ID}">',
        f'    <display-name lang="en">{CHANNEL_NAME}</display-name>',
        f'    <display-name lang="ar">{CHANNEL_NAME_AR}</display-name>',
        "  </channel>",
    ]
    slot = timedelta(minutes=30)
    for index in range(5 * 48):
        begin = start + index * slot
        title, title_ar, description = _PROGRAMMES[index % len(_PROGRAMMES)]
        lines += [
            f'  <programme start="{_xmltv_time(begin)}" stop="{_xmltv_time(begin + slot)}"'
            f' channel="{EPG_ID}">',
            f'    <title lang="en">{title}</title>',
            f'    <title lang="ar">{title_ar}</title>',
            f'    <desc lang="en">{description}</desc>',
            "    <category>Showcase</category>",
            "  </programme>",
        ]
    lines.append("</tv>")
    return ("\n".join(lines) + "\n").encode()


class Command(BaseCommand):
    help = "Create the dev live test channel and its guide (DEBUG only)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--wait", type=int, default=0, help="seconds to wait for catch-up")

    def handle(self, *args: Any, **options: Any) -> None:
        if not settings.DEBUG:
            raise CommandError("live_demo only runs with DEBUG on (the dev stack).")
        group, _ = Category.objects.update_or_create(
            kind=CategoryKind.LIVE,
            slug=GROUP_SLUG,
            defaults={"name_en": "Showcase", "name_ar": "عروض"},
        )
        source = EpgSource.objects.filter(name=GUIDE_NAME).first() or EpgSource(name=GUIDE_NAME)
        source.kind = EpgSourceKind.UPLOAD
        source.upload = epg.compress_upload(demo_guide(timezone.now()))
        source.upload_name = "demo-guide.xml"
        source.save()
        channel = LiveChannel.objects.filter(name=CHANNEL_NAME, group=group).first()
        if channel is None:
            channel = LiveChannel(name=CHANNEL_NAME, group=group)
        channel.name_ar = CHANNEL_NAME_AR
        channel.epg_channel_id = EPG_ID
        channel.epg_source = source
        if not channel.source_encrypted or sources.decrypt(channel.source_encrypted) != SOURCE_URL:
            channel.source_encrypted = sources.encrypt(SOURCE_URL)
        channel.catchup_days = 1
        channel.rights_holder = "Smart IPTV (synthetic test media)"
        channel.license_ref = "sample-media"
        channel.enabled = True
        channel.save()
        result = epg.import_source(source)
        programmes = result.programmes if result else 0
        self.stdout.write(f"live demo: channel {channel.xc_id}, {programmes} programmes")
        state.wake(channel.storage_key)
        if options["wait"] > 0:
            self._wait(channel, options["wait"])

    def _wait(self, channel: LiveChannel, seconds: int) -> None:
        deadline = time.monotonic() + seconds
        while True:
            window = state.archive_window(channel.storage_key)
            covered = (
                (timezone.now() - window.first).total_seconds()
                if window and window.last >= timezone.now() - timedelta(seconds=30)
                else 0
            )
            if covered >= ARCHIVE_NEEDED_S:
                self.stdout.write(f"live demo: recording, {int(covered)} s of catch-up")
                return
            if time.monotonic() > deadline:
                raise CommandError(
                    f"the channel has {int(covered)} s of catch-up after {seconds} s "
                    f"(needs {ARCHIVE_NEEDED_S}); see `make logs s=live`"
                )
            time.sleep(5)
