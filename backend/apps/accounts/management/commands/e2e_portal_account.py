"""`manage.py e2e_portal_account`: the customer the portal end-to-end journey signs in
as (`make e2e-portal`, frontend/apps/portal/e2e). DEBUG only; idempotent.

The username and password come from E2E_PORTAL_USER and E2E_PORTAL_PASS (the Makefile
makes a fresh password for every run) and are never printed. The customer has an
open-ended access profile for every category, three streams at once and 1080p, so the
journey can play whatever the sample media holds. Failed sign-ins of earlier runs are
cleared, and the customer's watch history, favourites and ratings are emptied, so
"continue watching" shows only what this run played.
"""

import os
from typing import Any

from axes.utils import reset as axes_reset
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts import services
from apps.accounts.models import User, UserStatus
from apps.engagement.models import Favorite, Rating, WatchProgress
from apps.playback import entitlements

CUSTOMER_NAME = "Portal end-to-end checks"
ACCESS = {
    "expires_at": None,
    "max_streams": 3,
    "max_devices": 2,
    "max_quality": 1080,
    "allow_movies": True,
    "allow_series": True,
}


class Command(BaseCommand):
    help = "Create or update the portal end-to-end customer from E2E_PORTAL_USER/E2E_PORTAL_PASS."

    def handle(self, *args: Any, **options: Any) -> None:
        if not settings.DEBUG:
            msg = "e2e_portal_account only runs with DEBUG on (development and tests)."
            raise CommandError(msg)
        username = os.environ.get("E2E_PORTAL_USER", "").strip()
        password = os.environ.get("E2E_PORTAL_PASS", "")
        if not username or not password:
            msg = "Set E2E_PORTAL_USER and E2E_PORTAL_PASS."
            raise CommandError(msg)
        with transaction.atomic():
            user = User.objects.select_for_update().filter(username=username).first()
            created = user is None
            if user is None:
                profile = {
                    "username": username,
                    "name": CUSTOMER_NAME,
                    # Emails are unique: one per end-to-end username.
                    "email": f"{username}@example.com",
                    "locale": "en",
                }
                user, _ = services.create_customer(
                    profile, access={**ACCESS, "category_ids": []}, actor=None
                )
            elif user.is_staff:
                msg = f"{username} is an admin account, not a customer."
                raise CommandError(msg)
            else:
                services.update_access(user, ACCESS, category_ids=[], actor=None)
            user.status = UserStatus.ACTIVE
            user.set_password(password)
            user.save(update_fields=["status", "password", "updated_at"])
            WatchProgress.objects.filter(user=user).delete()
            Favorite.objects.filter(user=user).delete()
            Rating.objects.filter(user=user).delete()
            entitlements.schedule_refresh(user.pk)
        axes_reset(username=username)
        self.stdout.write(f"{'Created' if created else 'Updated'} the portal customer {username}.")
