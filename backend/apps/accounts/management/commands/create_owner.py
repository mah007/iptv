"""`manage.py create_owner`: the production bootstrap.

The RBAC catalogue, the system roles and one owner admin, and nothing else: no demo
categories or customers (`seed_demo` adds those for development and demos).
Idempotent.

The owner is `admin` unless `--username` names another; `--email` and `--name` fill
in the profile. The password comes from the OWNER_PASSWORD environment variable when
it is set (checked against the password rules, never echoed), otherwise it is
generated and printed once, here only. `scripts/install.sh` passes the installer's
answers this way. Re-run with `--reset-admin-password` for a new password, or
`--reset-admin-mfa` to enrol a new authenticator at the next sign-in.
"""

import os
from typing import Any

from axes.utils import reset as axes_reset
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import CommandError, CommandParser
from django.core.validators import validate_email
from django.db import transaction

from apps.accounts import services
from apps.accounts.management.commands import seed_demo
from apps.accounts.models import MfaTotp, Role, User
from apps.accounts.rbac import OWNER_ROLE, sync_rbac
from apps.audit import services as audit

PASSWORD_ENV = "OWNER_PASSWORD"  # noqa: S105 (the variable's name, not a password)


class Command(seed_demo.Command):
    help = "Create the RBAC catalogue and the owner admin (no demo data); prints its password once."

    def add_arguments(self, parser: CommandParser) -> None:
        super().add_arguments(parser)
        parser.add_argument(
            "--username", default=seed_demo.ADMIN_USERNAME, help="the owner's sign-in name (admin)"
        )
        parser.add_argument("--email", default="", help="the owner's email address")
        parser.add_argument("--name", default="Owner", help="the owner's display name (Owner)")

    def handle(self, *args: Any, **options: Any) -> None:
        username = options["username"].strip()
        email = options["email"].strip().lower()
        name = options["name"].strip() or "Owner"
        _check_username(username)
        if email:
            try:
                validate_email(email)
            except ValidationError as error:
                msg = f"--email is not a valid address: {email}"
                raise CommandError(msg) from error
        sync_rbac()
        self._owner(
            username=username,
            email=email,
            name=name,
            reset_password=options["reset_admin_password"],
        )
        if options["reset_admin_mfa"]:
            self._owner_mfa_reset(username)

    def _owner(self, *, username: str, email: str, name: str, reset_password: bool) -> None:
        user = User.objects.filter(username=username).first()
        if user is not None and not user.is_staff:
            msg = f"'{username}' is a customer account; choose another --username."
            raise CommandError(msg)
        if user is not None and not reset_password:
            self.stdout.write(
                f"Admin '{username}' already exists; "
                "re-run with --reset-admin-password for a new password."
            )
            return
        if email and User.objects.filter(email__iexact=email).exclude(username=username).exists():
            msg = f"Another account already uses {email}."
            raise CommandError(msg)
        chosen = os.environ.get(PASSWORD_ENV, "")
        password = chosen or services.generate_admin_password()
        created = user is None
        with transaction.atomic():
            if user is None:
                user = User(username=username, email=email, name=name, is_staff=True)
            elif email:
                user.email = email
            if chosen:
                try:
                    validate_password(chosen, user)
                except ValidationError as error:
                    msg = f"{PASSWORD_ENV} is too weak: {' '.join(error.messages)}"
                    raise CommandError(msg) from error
            user.set_password(password)
            user.save()
            user.roles.add(Role.objects.get(name=OWNER_ROLE))
            if created:
                audit.record(
                    "admin.create",
                    actor=None,
                    target=user,
                    after=services.admin_snapshot(user, [OWNER_ROLE]),
                )
            else:
                audit.record("admin.password_reset", actor=None, target=user)
        axes_reset(username=username)
        action = "created" if created else "password reset"
        self.stdout.write(self.style.SUCCESS(f"Admin '{username}' {action}."))
        if chosen:
            self.stdout.write(f"  Password: the one given in {PASSWORD_ENV}.")
        else:
            self.stdout.write(f"  Password (shown once, store it now): {password}")
        self.stdout.write("  At first sign-in the admin enrols an authenticator app (TOTP MFA).")

    def _owner_mfa_reset(self, username: str) -> None:
        with transaction.atomic():
            user = User.objects.select_for_update().filter(username=username, is_staff=True).first()
            if user is None:
                msg = f"No admin '{username}'."
                raise CommandError(msg)
            MfaTotp.objects.filter(user=user).delete()
            user.mfa_enabled = False
            user.save(update_fields=["mfa_enabled", "updated_at"])
            audit.record("admin.mfa_reset", actor=None, target=user)
        self.stdout.write(
            self.style.SUCCESS(
                f"Admin '{username}' MFA reset: the next sign-in enrols a new authenticator."
            )
        )


def _check_username(username: str) -> None:
    field = User._meta.get_field("username")
    try:
        field.run_validators(username)
        if not username:
            raise ValidationError("empty")
    except ValidationError as error:
        msg = f"--username is not a valid sign-in name: {username!r}"
        raise CommandError(msg) from error
