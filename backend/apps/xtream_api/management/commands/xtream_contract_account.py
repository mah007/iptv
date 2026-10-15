"""`manage.py xtream_contract_account [--max-quality Q] [--wait-capped S]`: the Xtream
account `make compat-live` and the IPTVnator journey sign in with (compat/README.md).
Idempotent.

The username and password come from the XC_USER and XC_PASS environment variables
(the Makefile reads them from .env) and are never printed. The customer sees every
category and may play three streams at once: `validate.py --play` and the journey
each hold slots until their sessions expire.

`--max-quality 720` makes (or updates) a plan capped at that ceiling instead, for the
capped compat MP4 check (ADR-0023); `--wait-capped S` then queues the capped MP4s the
ceiling calls for and waits up to S seconds until every ready title has its own.
"""

import os
import time
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction

from apps.accounts import services
from apps.accounts.models import MaxQuality, XtreamCredential

CUSTOMER_NAME = "Xtream contract checks"
CUSTOMER_EMAIL = "xtream-contract@example.com"
MAX_STREAMS = 3


class Command(BaseCommand):
    help = "Create or update the Xtream contract-check account from XC_USER and XC_PASS."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--max-quality",
            type=int,
            choices=list(MaxQuality.values),
            help="the account's quality ceiling (a capped plan; default: unchanged)",
        )
        parser.add_argument(
            "--wait-capped",
            type=int,
            default=0,
            metavar="SECONDS",
            help="queue the capped compat MP4s and wait until every ready title has them",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        username = os.environ.get("XC_USER", "").strip()
        password = os.environ.get("XC_PASS", "")
        if not username or not password:
            msg = "Set XC_USER and XC_PASS (COMPAT_XC_USER and COMPAT_XC_PASS in .env)."
            raise CommandError(msg)
        quality: int | None = options["max_quality"]
        access: dict[str, Any] = {"max_streams": MAX_STREAMS}
        name, email = CUSTOMER_NAME, CUSTOMER_EMAIL
        if quality is not None:
            access["max_quality"] = quality
            name, email = f"{CUSTOMER_NAME} ({quality}p)", f"xtream-contract-{quality}@example.com"
        with transaction.atomic():
            credential = (
                XtreamCredential.objects.select_related("device__user")
                .filter(username__iexact=username)
                .first()
            )
            if credential is None:
                services.create_customer(
                    {"name": name, "email": email, "locale": "en"},
                    access={**access, "max_devices": 2},
                    device={"name": "Contract checks", "username": username, "password": password},
                    actor=None,
                )
                self.stdout.write(f"Created the contract account {username}.")
            else:
                services.update_access(credential.device.user, access, actor=None)
                services.reset_credential(
                    credential.device, username=username, password=password, actor=None
                )
                self.stdout.write(f"Updated the contract account {username}.")
        if options["wait_capped"] > 0:
            self._wait_capped(options["wait_capped"])

    def _wait_capped(self, seconds: int) -> None:
        from apps.catalog.models import MediaFile  # noqa: PLC0415 (media sits above accounts)
        from apps.media import capped  # noqa: PLC0415
        from apps.media import services as media  # noqa: PLC0415

        ceilings = capped.wanted_ceilings()
        deadline = time.monotonic() + seconds
        while True:
            media.queue_capped_backlog()
            files = MediaFile.objects.filter(
                removed_at__isnull=True, renditions__kind="hls_master", renditions__name="hls"
            ).distinct()
            waiting = [
                file for file in files.prefetch_related("renditions")
                if capped.missing(file.renditions.all(), ceilings)
            ]  # fmt: skip
            if not waiting:
                self.stdout.write(f"Capped compat MP4s ready for {list(ceilings)}.")
                return
            if time.monotonic() > deadline:
                msg = f"{len(waiting)} files still wait for their capped compat MP4."
                raise CommandError(msg)
            time.sleep(3)
