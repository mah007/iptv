"""The restore drill, now (`make restore-test`; ADR-0020).

    manage.py restore_drill [--wait] [--timeout 7200]

The backup service restores the latest backup into a throwaway PostgreSQL, replays the
archived WAL, and reports the migrations and row counts it finds; Django compares them
with the live database. When no backup exists yet, one is taken first. With `--wait`
it prints the restore time and the checks, and exits non-zero when the drill failed.
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.ops import backups
from apps.ops.models import DB_KINDS, BackupKind, RunTrigger
from apps.ops.waiting import print_run, wait_for


class Command(BaseCommand):
    help = "Run the restore drill now; --wait for the verdict and the restore time."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--wait", action="store_true", help="wait for the verdict")
        parser.add_argument("--timeout", type=int, default=7200, help="seconds to wait")

    def handle(self, *args: Any, **options: Any) -> None:
        if not backups.agent_state().healthy:
            msg = "The backup service is not running (docker compose ... ps backup)."
            raise CommandError(msg)
        runs = []
        if backups.last_success(DB_KINDS) is None and not backups.pending_db():
            runs += backups.queue_nightly(trigger=RunTrigger.COMMAND, full=True)
            self.stdout.write("No backup yet: taking one first.")
        runs.append(backups.queue(BackupKind.DRILL, trigger=RunTrigger.COMMAND))
        if not options["wait"]:
            self.stdout.write("Queued the restore drill.")
            return
        failed = False
        for run in wait_for(runs, timeout=options["timeout"]):
            print_run(self.stdout, run)
            failed = failed or run.status != "succeeded"
        if failed:
            msg = "The restore drill failed; see above and the admin's Backups page."
            raise CommandError(msg)
