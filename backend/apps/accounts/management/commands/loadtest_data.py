"""`manage.py loadtest_data`: throwaway customers for `make loadtest` (M15, ADR-0022;
docs/benchmarks/load.md). Development only: it refuses to run unless DEBUG is on.

- `--viewers N --groups G` creates the customers:
  - N viewers, with one device and one stream each;
  - G contention customers, each with `max_streams` 2, the reject policy and 4 devices.

  Every credential uses the password in LOADTEST_PASSWORD (never printed or stored here).
  Its Argon2id hash is computed once, which is all that is shortcut: sign-ins still
  verify it. The usernames are `ltv00001…` and `ltc0001d1…`; the customers carry the
  email domain `loadtest.invalid`. They are not audited: they are fixtures, deleted
  after the run. Idempotent: existing ones are kept.
- `--slots` prints, as JSON, the contention customers' active streams now, the
  over-allocation check, and the viewers' total.
- `--delete` stops their sessions and deletes them, with their devices, credentials
  and sessions.
"""

import json
import os
from typing import Any
from unittest import mock

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.accounts import credentials as creds
from apps.accounts import services
from apps.accounts.models import ConcurrencyPolicy, User
from apps.audit import services as audit
from apps.playback import concurrency
from apps.playback.concurrency import KickReason
from apps.playback.services import open_sessions, stop_sessions

DOMAIN = "loadtest.invalid"
CONTENTION_STREAMS = 2
CONTENTION_DEVICES = 4


def viewer_name(n: int) -> str:
    return f"ltv{n:05d}"


def device_name(group: int, k: int) -> str:
    return f"ltc{group:04d}d{k}"


def tagged() -> Any:
    return User.objects.filter(email__endswith=f"@{DOMAIN}")


class Command(BaseCommand):
    help = "Create, check or delete the load test's throwaway customers (development only)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--viewers", type=int, default=0)
        parser.add_argument("--groups", type=int, default=0)
        parser.add_argument("--slots", action="store_true")
        parser.add_argument("--delete", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        if not settings.DEBUG:
            msg = "loadtest_data runs in development only (DEBUG)."
            raise CommandError(msg)
        if options["delete"]:
            self._delete()
        elif options["slots"]:
            self._slots()
        else:
            self._create(options["viewers"], options["groups"])

    def _create(self, viewers: int, groups: int) -> None:
        password = os.environ.get("LOADTEST_PASSWORD", "")
        if len(password) < 12:
            msg = "Set LOADTEST_PASSWORD (12+ characters) in the environment."
            raise CommandError(msg)
        hashed = creds.hash_password(password)
        existing = set(tagged().values_list("devices__credential__username", flat=True))
        made = 0
        with (
            mock.patch.object(creds, "hash_password", return_value=hashed),
            mock.patch.object(audit, "record", return_value=None),
        ):
            for n in range(1, viewers + 1):
                if viewer_name(n) in existing:
                    continue
                services.create_customer(
                    {
                        "name": f"Load viewer {n}",
                        "email": f"{viewer_name(n)}@{DOMAIN}",
                        "locale": "en",
                    },
                    access={"max_streams": 1, "max_devices": 1},
                    device={"name": "Load test", "username": viewer_name(n), "password": password},
                    actor=None,
                )
                made += 1
            for g in range(1, groups + 1):
                if device_name(g, 1) in existing:
                    continue
                user, _issued = services.create_customer(
                    {
                        "name": f"Load contention {g}",
                        "email": f"ltc{g:04d}@{DOMAIN}",
                        "locale": "en",
                    },
                    access={
                        "max_streams": CONTENTION_STREAMS,
                        "max_devices": CONTENTION_DEVICES,
                        "concurrency_policy": ConcurrencyPolicy.REJECT,
                    },
                    device={
                        "name": "Load test 1",
                        "username": device_name(g, 1),
                        "password": password,
                    },
                    actor=None,
                )
                for k in range(2, CONTENTION_DEVICES + 1):
                    services.create_device_credential(
                        user,
                        name=f"Load test {k}",
                        username=device_name(g, k),
                        password=password,
                        actor=None,
                    )
                made += 1
        self.stdout.write(f"Load test customers: {made} created, {tagged().count()} in all.")

    def _slots(self) -> None:
        groups = tagged().filter(email__startswith="ltc").values_list("pk", flat=True)
        counts = [concurrency.active_streams(pk) for pk in groups]
        viewers = tagged().filter(email__startswith="ltv").values_list("pk", flat=True)
        # Machine-readable, for loadtests/run_load.py.
        self.stdout.write(
            json.dumps(
                {
                    "groups": len(counts),
                    "limit": CONTENTION_STREAMS,
                    "max": max(counts, default=0),
                    "over": sum(1 for count in counts if count > CONTENTION_STREAMS),
                    "group_streams": sum(counts),
                    "viewer_streams": sum(concurrency.active_streams(pk) for pk in viewers),
                }
            )
        )

    def _delete(self) -> None:
        users = tagged()
        stopped = stop_sessions(open_sessions().filter(user__in=users), KickReason.KICKED)
        count, _ = users.delete()
        self.stdout.write(
            f"Load test customers deleted: {len(stopped)} sessions stopped, {count} rows."
        )
