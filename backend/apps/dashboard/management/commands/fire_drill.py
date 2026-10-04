"""`manage.py fire_drill`: prove that an alert reaches email and Telegram (SPEC §17).

It sets the `iptv_fire_drill` gauge to 1 for `--minutes`. Prometheus scrapes it from
web's /metrics, its FireDrill rule fires, and Alertmanager sends the alert to every
receiver, exactly as a real incident would travel. When the gauge expires (or with
`--stop`) the alert resolves and a "resolved" message follows. The drill is audited.
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandParser
from django.db import transaction
from django.utils import timezone

from apps.audit import services as audit
from apps.core.metrics import FIRE_DRILL, SnapshotGauge

MIN_MINUTES = 1
MAX_MINUTES = 60


class Command(BaseCommand):
    help = "Fire the FireDrill alert for a few minutes, or stop it (--stop)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--minutes",
            type=int,
            default=10,
            help=f"How long the alert stays firing ({MIN_MINUTES}-{MAX_MINUTES}, default 10).",
        )
        parser.add_argument(
            "--stop", action="store_true", help="End a running drill now (the alert resolves)."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if options["stop"]:
            FIRE_DRILL.publish({})
            self._audit("stop", 0)
            self.stdout.write("Fire drill stopped: the alert resolves within a minute or two.")
            return
        minutes = max(MIN_MINUTES, min(MAX_MINUTES, int(options["minutes"])))
        drill = SnapshotGauge(
            FIRE_DRILL.name, FIRE_DRILL.documentation, FIRE_DRILL.labelnames, ttl_s=minutes * 60
        )
        drill.publish({(): 1})
        self._audit("start", minutes)
        self.stdout.write(
            f"Fire drill started at {timezone.now():%Y-%m-%d %H:%M:%S} UTC for {minutes} min. "
            "Expect the FireDrill alert by email and Telegram within about 2 minutes."
        )

    def _audit(self, step: str, minutes: int) -> None:
        with transaction.atomic():
            audit.record(
                "monitoring.fire_drill", actor=None, after={"step": step, "minutes": minutes}
            )
