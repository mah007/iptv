"""`manage.py fire_drill`: prove that an alert reaches email and Telegram (SPEC §17).

It sets the `iptv_fire_drill` gauge to 1 for `--minutes`. Prometheus scrapes it from
web's /metrics, its FireDrill rule fires, and Alertmanager sends the alert to Django,
which emails and messages it as configured in Settings (group "monitoring"), exactly as
a real incident would travel. When the gauge expires (or with `--stop`) the alert
resolves and a "resolved" message follows. The drill is audited. The admin's
Monitoring page has the same as "Send a test alert".
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from apps.dashboard.metrics import (
    FIRE_DRILL_MAX_MINUTES,
    FIRE_DRILL_MIN_MINUTES,
    start_fire_drill,
    stop_fire_drill,
)


class Command(BaseCommand):
    help = "Fire the FireDrill alert for a few minutes, or stop it (--stop)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--minutes",
            type=int,
            default=10,
            help=(
                f"How long the alert stays firing ({FIRE_DRILL_MIN_MINUTES}-"
                f"{FIRE_DRILL_MAX_MINUTES}, default 10)."
            ),
        )
        parser.add_argument(
            "--stop", action="store_true", help="End a running drill now (the alert resolves)."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if options["stop"]:
            stop_fire_drill()
            self.stdout.write("Fire drill stopped: the alert resolves within a minute or two.")
            return
        until = start_fire_drill(options["minutes"])
        self.stdout.write(
            f"Fire drill running until {until:%Y-%m-%d %H:%M:%S} UTC. Expect the FireDrill "
            "alert by email and Telegram within about 2 minutes."
        )
