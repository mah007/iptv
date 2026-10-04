"""`manage.py e2e_admin_account`: the admin the admin end-to-end suite signs in as
(`make e2e-admin`, frontend/e2e). DEBUG only; idempotent.

The username and password come from E2E_ADMIN_USER and E2E_ADMIN_PASS (the Makefile
makes a fresh password for every run) and are never printed. The admin holds the
owner role; its authenticator stays enrolled between runs, and the suite reads the
codes with `manage.py totp_code`. Failed sign-ins of earlier runs are cleared.

`--open-review` makes sure the review queue has an open item for the suite to resolve:
when none is open, the latest decided movie review is opened again with the same
candidates, the one chosen last time first, so resolving it keeps the catalogue as it was.
"""

import os
from typing import Any

from axes.utils import reset as axes_reset
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction

from apps.accounts.models import Role, User, UserStatus
from apps.accounts.rbac import OWNER_ROLE, sync_rbac
from apps.catalog.models import MatchReview, ReviewKind, ReviewStatus

ADMIN_NAME = "End-to-end checks"


def _chosen_first(candidates: list[Any], chosen: int | None) -> list[Any]:
    def rank(candidate: Any) -> int:
        provider_id = candidate.get("id") if isinstance(candidate, dict) else None
        return 0 if chosen is not None and provider_id == chosen else 1

    return sorted(candidates, key=rank)


class Command(BaseCommand):
    help = "Create or update the end-to-end admin from E2E_ADMIN_USER and E2E_ADMIN_PASS."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--open-review",
            action="store_true",
            help="Reopen the latest decided movie review when the queue has no open item.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if not settings.DEBUG:
            msg = "e2e_admin_account only runs with DEBUG on (development and tests)."
            raise CommandError(msg)
        username = os.environ.get("E2E_ADMIN_USER", "").strip()
        password = os.environ.get("E2E_ADMIN_PASS", "")
        if not username or not password:
            msg = "Set E2E_ADMIN_USER and E2E_ADMIN_PASS."
            raise CommandError(msg)
        sync_rbac()
        with transaction.atomic():
            user = User.objects.filter(username=username).first()
            created = user is None
            if user is None:
                user = User(username=username, name=ADMIN_NAME, is_staff=True)
            elif not user.is_staff:
                msg = f"{username} is a customer account, not an admin."
                raise CommandError(msg)
            user.status = UserStatus.ACTIVE
            user.set_password(password)
            user.save()
            user.roles.add(Role.objects.get(name=OWNER_ROLE))
        axes_reset(username=username)
        self.stdout.write(f"{'Created' if created else 'Updated'} the end-to-end admin {username}.")
        if options["open_review"]:
            self._open_review()

    def _open_review(self) -> None:
        if MatchReview.objects.filter(status=ReviewStatus.OPEN).exists():
            self.stdout.write("The review queue already has an open item.")
            return
        decided = (
            MatchReview.objects.filter(kind=ReviewKind.MOVIE)
            .exclude(candidates=[])
            .order_by("-decided_at", "-created_at")
            .first()
        )
        if decided is None:
            msg = (
                "No decided movie review to reopen: scan the sample media first (make media-ready)."
            )
            raise CommandError(msg)
        MatchReview.objects.create(
            media_file=decided.media_file,
            kind=decided.kind,
            reason=decided.reason,
            parse_result=decided.parse_result,
            candidates=_chosen_first(list(decided.candidates), decided.chosen_provider_id),
        )
        self.stdout.write("Reopened a review for the suite to resolve.")
