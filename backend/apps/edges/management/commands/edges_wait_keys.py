"""Wait until every enabled standalone edge holds a key id (ADR-0022).

scripts/rotate-keys.sh media-token runs it between "stage" and "promote": Django must
not sign with a new key before every edge accepts it. Standalone edges pick keys up on
their control channel within one poll (15 s).

    manage.py edges_wait_keys [--kid K] [--timeout 300]

Exits 0 when every enabled standalone edge reports the kid (immediately when there are
none), 1 on timeout, naming the edges that lag.
"""

import time
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.edges.models import Edge, EdgeKind
from apps.playback import tokens


class Command(BaseCommand):
    help = "Wait until every enabled standalone edge reports a key id."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--kid", help="the key id (default: every kid of the key file)")
        parser.add_argument("--timeout", type=int, default=300)
        parser.add_argument("--interval", type=float, default=5.0)

    def handle(self, *args: Any, **options: Any) -> None:
        if options["kid"]:
            wanted = {str(options["kid"])}
        else:
            keyset = tokens.keyring()
            wanted = {key.kid for key in (keyset.current, keyset.previous) if key is not None}
        deadline = time.monotonic() + max(0, int(options["timeout"]))
        while True:
            lagging = [
                edge.edge_id
                for edge in Edge.objects.filter(kind=EdgeKind.STANDALONE, enabled=True)
                if not wanted <= set((edge.report or {}).get("kids") or [])
            ]
            if not lagging:
                self.stdout.write(f"every standalone edge holds {', '.join(sorted(wanted))}")
                return
            if time.monotonic() >= deadline:
                msg = (
                    f"these edges don't hold {', '.join(sorted(wanted))} yet: "
                    f"{', '.join(sorted(lagging))}. Fix or disable them (admin → Edges), "
                    "then run the rotation again; nothing was promoted."
                )
                raise CommandError(msg)
            time.sleep(float(options["interval"]))
