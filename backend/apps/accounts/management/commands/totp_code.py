"""Print an admin's current TOTP code. DEBUG only: for end-to-end tests and demos.

The code printed is one the server will accept next: when the current 30-second
step was already used (codes are single-use), it is the next step's code, which
the server accepts within its one-step drift window; if even that was used, the
command waits for the clock to catch up.
"""

import time
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.accounts import crypto, mfa
from apps.accounts.models import MfaTotp


class Command(BaseCommand):
    help = "Print the next acceptable TOTP code of an admin (refuses unless DEBUG is on)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("username")

    def handle(self, *args: Any, **options: Any) -> None:
        if not settings.DEBUG:
            msg = "totp_code only runs with DEBUG on (development and tests)."
            raise CommandError(msg)
        totp = (
            MfaTotp.objects.select_related("user")
            .filter(user__username=options["username"])
            .first()
        )
        if totp is None:
            msg = "That admin has no authenticator yet: sign in with the password first."
            raise CommandError(msg)
        secret = crypto.decrypt(totp.secret_encrypted)
        now_step = mfa.current_step()
        step = now_step if totp.last_used_step is None else max(now_step, totp.last_used_step + 1)
        wait_s = (step - mfa.DRIFT_STEPS) * mfa.STEP_S - time.time()
        if wait_s > 0:
            time.sleep(wait_s)
        self.stdout.write(mfa.code_at(secret, step))
