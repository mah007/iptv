"""`manage.py xtream_contract_account`: the Xtream account `make compat-live` and the
IPTVnator journey sign in with (compat/README.md). Idempotent.

The username and password come from the XC_USER and XC_PASS environment variables
(the Makefile reads them from .env) and are never printed. The customer sees every
category and may play three streams at once: `validate.py --play` and the journey
each hold slots until their sessions expire.
"""

import os
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts import services
from apps.accounts.models import XtreamCredential

CUSTOMER_NAME = "Xtream contract checks"
CUSTOMER_EMAIL = "xtream-contract@example.com"
MAX_STREAMS = 3


class Command(BaseCommand):
    help = "Create or update the Xtream contract-check account from XC_USER and XC_PASS."

    def handle(self, *args: Any, **options: Any) -> None:
        username = os.environ.get("XC_USER", "").strip()
        password = os.environ.get("XC_PASS", "")
        if not username or not password:
            msg = "Set XC_USER and XC_PASS (COMPAT_XC_USER and COMPAT_XC_PASS in .env)."
            raise CommandError(msg)
        with transaction.atomic():
            credential = (
                XtreamCredential.objects.select_related("device__user")
                .filter(username__iexact=username)
                .first()
            )
            if credential is None:
                services.create_customer(
                    {"name": CUSTOMER_NAME, "email": CUSTOMER_EMAIL, "locale": "en"},
                    access={"max_streams": MAX_STREAMS, "max_devices": 2},
                    device={"name": "Contract checks", "username": username, "password": password},
                    actor=None,
                )
                self.stdout.write(f"Created the contract account {username}.")
                return
            user = credential.device.user
            services.update_access(user, {"max_streams": MAX_STREAMS}, actor=None)
            services.reset_credential(
                credential.device, username=username, password=password, actor=None
            )
            self.stdout.write(f"Updated the contract account {username}.")
