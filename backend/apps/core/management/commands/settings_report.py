"""`manage.py settings_report`: which settings still take their value from `.env`.

Names only, never values (ADR-0019). The installer prints the count after an update,
pointing the owner to Admin → Settings → Integrations, where each can be moved in.

    python manage.py settings_report           # one line per setting: key (VARIABLE)
    python manage.py settings_report --count   # just the number
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from apps.core.services import from_server_config


class Command(BaseCommand):
    help = "List the settings whose value comes from the server config (.env); names only."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--count", action="store_true", help="Print only how many.")

    def handle(self, *args: Any, **options: Any) -> None:
        states = from_server_config()
        if options["count"]:
            self.stdout.write(str(len(states)))
            return
        for state in states:
            self.stdout.write(f"{state.definition.key} ({', '.join(state.definition.env)})")
