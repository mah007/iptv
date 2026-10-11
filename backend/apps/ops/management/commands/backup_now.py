"""Back up now, as the admin's "Back up now" does (`make backup`; ADR-0020).

    manage.py backup_now [--wait] [--timeout 3600] [--full]

Queues the database backup (differential, or `--full`), the files snapshot and, when a
bucket is configured, the offsite copy; the backup service runs them in that order.
With `--wait` it prints each result and exits non-zero when one failed.
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.ops import backups
from apps.ops.models import RunTrigger
from apps.ops.waiting import print_run, wait_for


class Command(BaseCommand):
    help = "Queue a backup now (database, files, offsite); --wait for the results."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--wait", action="store_true", help="wait for the results")
        parser.add_argument("--timeout", type=int, default=3600, help="seconds to wait")
        parser.add_argument("--full", action="store_true", help="a full database backup")

    def handle(self, *args: Any, **options: Any) -> None:
        if not backups.agent_state().healthy:
            msg = "The backup service is not running (docker compose ... ps backup)."
            raise CommandError(msg)
        if backups.pending_db():
            msg = "A backup is already queued or running."
            raise CommandError(msg)
        runs = backups.queue_nightly(trigger=RunTrigger.COMMAND, full=options["full"])
        self.stdout.write(f"Queued: {', '.join(run.kind for run in runs)}")
        if not options["wait"]:
            return
        failed = False
        for run in wait_for(runs, timeout=options["timeout"]):
            print_run(self.stdout, run)
            failed = failed or run.status != "succeeded"
        if failed:
            msg = "A backup job failed; see above and the admin's Backups page."
            raise CommandError(msg)
