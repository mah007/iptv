"""`manage.py create_owner`: the production bootstrap.

The RBAC catalogue, the system roles and the owner admin `admin`, and nothing
else: no demo categories or customers (`seed_demo` adds those for development
and demos). Idempotent. The password is printed once, here only; re-run with
`--reset-admin-password` for a new one, or `--reset-admin-mfa` to enrol a new
authenticator at the next sign-in.
"""

from typing import Any

from apps.accounts.management.commands import seed_demo
from apps.accounts.rbac import sync_rbac


class Command(seed_demo.Command):
    help = "Create the RBAC catalogue and the owner admin (no demo data); prints its password once."

    def handle(self, *args: Any, **options: Any) -> None:
        sync_rbac()
        self._admin(reset_password=options["reset_admin_password"])
        if options["reset_admin_mfa"]:
            self._reset_admin_mfa()
