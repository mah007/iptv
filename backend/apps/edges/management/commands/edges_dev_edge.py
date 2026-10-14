"""Development only (DEBUG): the dev machine's standalone edge in the admin (ADR-0022).

    manage.py edges_dev_edge --edge-id dev-edge --base-url http://127.0.0.1:18090 \
        [--storage origin] [--canary <username> ...]

Creates or updates the edge and prints a new bearer secret on stdout (the Makefile
writes it to secrets/dev-edge/edge_token, which the dev edge reads). Refused unless DEBUG.
"""

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction

from apps.accounts.models import User
from apps.edges import services
from apps.edges.models import Audience, Edge, EdgeKind, EdgeStorage


class Command(BaseCommand):
    help = "DEBUG only: create or update the dev standalone edge and print a new secret."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--edge-id", default="dev-edge")
        parser.add_argument("--base-url", default="http://127.0.0.1:18090")
        parser.add_argument("--storage", choices=EdgeStorage.values, default=EdgeStorage.ORIGIN)
        parser.add_argument("--canary", nargs="*", default=None, help="customers' user names")

    def handle(self, *args: Any, **options: Any) -> None:
        if not settings.DEBUG:
            msg = "edges_dev_edge is for development only (DEBUG)."
            raise CommandError(msg)
        with transaction.atomic():
            edge, _ = Edge.objects.update_or_create(
                edge_id=options["edge_id"],
                defaults={
                    "name": "Dev edge",
                    "kind": EdgeKind.STANDALONE,
                    "storage": options["storage"],
                    "base_url": options["base_url"],
                    "enabled": True,
                },
            )
            if options["canary"] is not None:
                users = list(User.objects.filter(username__in=options["canary"]))
                edge.audience = Audience.CANARY
                edge.save(update_fields=["audience", "updated_at"])
                edge.canary_users.set(users)
            secret = services.rotate_secret(edge)
        services.rebuild_auth_mirror()
        self.stdout.write(secret)
