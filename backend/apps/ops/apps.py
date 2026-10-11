from typing import Any

from django.apps import AppConfig
from django.db import connections
from django.db.models.signals import post_delete, post_migrate, post_save, pre_migrate


class OpsConfig(AppConfig):
    """Backups, retention, database privileges, CrowdSec and key rotation (ADR-0020)."""

    name = "apps.ops"
    label = "ops"
    verbose_name = "Operations"

    def ready(self) -> None:
        from apps.core.metrics import register_scrape_collector  # noqa: PLC0415
        from apps.core.models import Setting  # noqa: PLC0415
        from apps.ops.metrics import BackupCollector  # noqa: PLC0415

        register_scrape_collector(BackupCollector())
        pre_migrate.connect(_refuse_app_role, sender=self, dispatch_uid="ops-refuse-app-role")
        post_migrate.connect(_apply_grants, sender=self, dispatch_uid="ops-apply-grants")
        post_save.connect(_setting_saved, sender=Setting, dispatch_uid="ops-setting-saved")
        post_delete.connect(_setting_saved, sender=Setting, dispatch_uid="ops-setting-reset")


def _refuse_app_role(using: str = "default", **kwargs: Any) -> None:
    """`migrate` run as the restricted role would fail half-way: say what to do instead."""
    from django.core.management.base import CommandError  # noqa: PLC0415

    from apps.ops import grants  # noqa: PLC0415

    if not grants.owns_schema(connections[using]):
        msg = (
            "This connection uses the restricted database role (DB_ROLE=app), which cannot "
            "change the schema. Run migrations with the owner: `make migrate` in development, "
            "the `migrate` service in production (docker compose ... run --rm migrate)."
        )
        raise CommandError(msg)


def _apply_grants(using: str = "default", **kwargs: Any) -> None:
    from apps.ops import grants  # noqa: PLC0415

    grants.apply(connections[using])


def _setting_saved(instance: Any, **kwargs: Any) -> None:
    """A CrowdSec setting changed: render Traefik's file now, not at the next minute."""
    if not str(getattr(instance, "key", "")).startswith("security.crowdsec_"):
        return
    from django.db import transaction  # noqa: PLC0415

    from apps.ops import tasks  # noqa: PLC0415

    # A moment later: the settings cache's new version is published after this commit
    # hook runs, and the worker must read the new value.
    transaction.on_commit(lambda: tasks.render_crowdsec.apply_async(countdown=2))
