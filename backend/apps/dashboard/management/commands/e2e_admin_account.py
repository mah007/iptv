"""`manage.py e2e_admin_account`: the admin the admin end-to-end suite signs in as
(`make e2e-admin`, frontend/e2e). DEBUG only; idempotent.

The username and password come from E2E_ADMIN_USER and E2E_ADMIN_PASS (the Makefile
makes a fresh password for every run) and are never printed. The admin holds the
owner role; its authenticator stays enrolled between runs, and the suite reads the
codes with `manage.py totp_code`. Failed sign-ins of earlier runs are cleared, and so are
security keys left by earlier runs (their virtual authenticators died with the browser).

`--open-review` makes sure the review queue has an open item with candidates for the suite
to resolve: when none is open, the latest decided movie review is opened again with the same
candidates, the one chosen last time first, so resolving it keeps the catalogue as it was.

`--manual-review` sets the sample home video (`scripts/sample_media.sh`, a file no provider
knows) back to a skipped review, for the manual title journey (ADR-0026) to reopen and turn
into a manual movie. The movie an earlier run made is deleted, but only a manual movie
whose single file is that sample; a provider's title that claimed the file just loses it.
"""

import os
from typing import Any

from axes.utils import reset as axes_reset
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction
from django.utils import timezone

from apps.accounts import auth, stepup
from apps.accounts.models import Role, User, UserStatus, WebAuthnCredential
from apps.accounts.rbac import OWNER_ROLE, sync_rbac
from apps.catalog.models import (
    FileState,
    MatchReview,
    MediaFile,
    MetadataSource,
    ReviewKind,
    ReviewStatus,
)
from apps.catalog.services import refresh_movie_status
from apps.catalog.signals import notify_catalog_changed

ADMIN_NAME = "End-to-end checks"
#: The sample no metadata provider knows (scripts/sample_media.sh, movies library).
HOME_VIDEO = "Smart IPTV Sample Home Video (2026).mp4"


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
        parser.add_argument(
            "--manual-review",
            action="store_true",
            help="Set the sample home video back to a skipped review (the manual title journey).",
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
            # Somewhere for the Integrations journey's test email (Mailpit catches it).
            if not user.email:
                user.email = f"{username}@example.test"
            user.set_password(password)
            user.save()
            user.roles.add(Role.objects.get(name=OWNER_ROLE))
            WebAuthnCredential.objects.filter(user=user).delete()
        axes_reset(username=username)
        auth.clear_mfa_failures(user)
        stepup.clear_failures(user)
        self.stdout.write(f"{'Created' if created else 'Updated'} the end-to-end admin {username}.")
        if options["open_review"]:
            self._open_review()
        if options["manual_review"]:
            self._manual_review()

    def _manual_review(self) -> None:
        file = (
            MediaFile.objects.select_related("movie")
            .filter(storage_key=HOME_VIDEO, removed_at__isnull=True)
            .first()
        )
        if file is None:
            msg = f"{HOME_VIDEO} isn't scanned yet: run make media-ready first."
            raise CommandError(msg)
        now = timezone.now()
        with transaction.atomic():
            movie = file.movie
            if movie is not None:
                others = MediaFile.objects.filter(movie=movie).exclude(pk=file.pk).exists()
                if movie.metadata_source == MetadataSource.MANUAL and not others:
                    pk = movie.pk
                    movie.delete()
                    notify_catalog_changed("movie", [pk])
                else:
                    MediaFile.objects.filter(pk=file.pk).update(movie=None)
                    refresh_movie_status(movie)
            MediaFile.objects.filter(pk=file.pk).update(
                movie=None, state=FileState.REVIEW, match_confidence=None, updated_at=now
            )
            reviews = MatchReview.objects.filter(
                media_file=file, status__in=(ReviewStatus.OPEN, ReviewStatus.SKIPPED)
            ).order_by("-created_at")
            kept = reviews.first()
            if kept is None:
                kept = MatchReview.objects.create(
                    media_file=file,
                    kind=ReviewKind.MOVIE,
                    reason="no_candidates",
                    parse_result=file.parse_result,
                )
            reviews.exclude(pk=kept.pk).delete()
            kept.status = ReviewStatus.SKIPPED
            kept.decided_at = now
            kept.save()
        self.stdout.write(f"{HOME_VIDEO} waits in the skipped reviews.")

    def _open_review(self) -> None:
        open_reviews = MatchReview.objects.filter(status=ReviewStatus.OPEN)
        if open_reviews.exclude(candidates=[]).exists():
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
