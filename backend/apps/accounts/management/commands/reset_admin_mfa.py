"""Reset an admin's sign-in confirmation from the host: the owner's last resort (ADR-0021).

Removes the admin's authenticator app and security keys and ends their sessions; their
next sign-in (with their password) enrols a new authenticator app. It works in
production, unlike `totp_code`: whoever can run it already has root on the server. The
reset is audited as `admin.mfa_reset` with no actor (the host). Nothing secret is
printed.

    docker compose ... exec -T web python manage.py reset_admin_mfa <username>
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.accounts import auth
from apps.accounts.models import User


class Command(BaseCommand):
    help = "Remove an admin's authenticator app and security keys (they enrol again)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("username", help="The admin's username or email address.")

    def handle(self, *args: Any, **options: Any) -> None:
        login = str(options["username"]).strip()
        admin = (
            User.objects.filter(is_staff=True, email__iexact=login).first()
            if "@" in login
            else User.objects.filter(is_staff=True, username__iexact=login).first()
        )
        if admin is None:
            msg = "No admin has that username or email."
            raise CommandError(msg)
        auth.reset_mfa(admin, actor=None)
        self.stdout.write(
            f"Reset {admin.username}: their sessions ended; their next sign-in enrols a new "
            "authenticator app."
        )
